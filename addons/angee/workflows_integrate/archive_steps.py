"""Reviewed archive import over the workflow, decision and storage owners."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from angee.base.evidence import FactAuthority
from angee.base.identity import public_id_of
from angee.base.impl import ImplBase, resolve_all_impl_classes, resolve_impl_class
from angee.decisions.contracts import DecisionContext, DecisionFact, DecisionProposal, DecisionRequest
from angee.workflows.context import StepContext
from angee.workflows.maps import MapItem
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.steps import Settlement, Step, StepMode


class ArchiveProposal(BaseModel):
    """One recognized extractor's immutable review identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    extractor: str
    label: str
    target_resource: str


class ArchiveProbeOutput(BaseModel):
    """The proposals retained by bounded recognition."""

    model_config = ConfigDict(extra="forbid")
    proposals: list[ArchiveProposal]


class ArchiveMappingUnit(BaseModel):
    """One admitted extractor and target for the workflow map body."""

    model_config = ConfigDict(extra="forbid")
    extractor: str = Field(min_length=1)
    target: str = Field(min_length=1)


class ArchiveExecutionOutput(ArchiveMappingUnit):
    """One extractor's JSON journal result."""

    result: JsonValue


class ArchiveGateConfig(BaseModel):
    """Optional actor-visible reviewer for a shared archive graph."""

    model_config = ConfigDict(extra="forbid")
    assignee: str = ""


@dataclass(frozen=True, slots=True)
class ArchiveExecutionReporter:
    """Expose only the engine heartbeat to long-running extractors."""

    context: StepContext

    def heartbeat(self) -> None:
        """Refresh the fenced IO attempt and notice cancellation."""
        self.context.heartbeat()
        self.context.raise_if_canceled()


class ArchiveExtractor(ImplBase, ABC):
    """Registered vendor adapter for a storage container and target resource."""
    registry_setting = "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES"

    target_resource: ClassVar[str] = ""
    subject_resource: ClassVar[str] = "storage.File"

    @abstractmethod
    def recognizes(self, subject: Any) -> bool:
        """Boundedly recognize the subject, returning exactly a bool."""

    @abstractmethod
    def execute(self, subject: Any, target_pk: str, reporter: ArchiveExecutionReporter) -> JsonValue:
        """Idempotently ingest the reviewed target and return JSON journal facts."""

    @classmethod
    def registered_classes(cls) -> tuple[type[ArchiveExtractor], ...]:
        """Resolve and validate registered extractors in stable key order."""
        classes = resolve_all_impl_classes(cls)
        for extractor in classes:
            extractor.validate_contract()
        return classes

    @classmethod
    def resolve_class(cls, key: str) -> type[ArchiveExtractor]:
        """Resolve and validate one registered extractor."""
        extractor = resolve_impl_class(cls, key)
        extractor.validate_contract()
        return extractor

    @classmethod
    def validate_contract(cls) -> None:
        """Require usable labels and a storage subject for this adapter."""
        if not cls.key or not cls.display_label().strip():
            raise ImproperlyConfigured("Archive extractors require a key and label.")
        for attr in ("subject_resource", "target_resource"):
            label = getattr(cls, attr)
            try:
                model = apps.get_model(label)
            except (LookupError, ValueError) as error:
                raise ImproperlyConfigured(f"Archive extractor {cls.key!r} has unknown {attr}.") from error
            if model._meta.label != label:
                raise ImproperlyConfigured(f"Archive extractor {cls.key!r} must use canonical {attr}.")
        if cls.subject_resource not in {"storage.File", "storage.Drive"}:
            raise ImproperlyConfigured("Archive extractors require a storage.File or storage.Drive subject.")


def _subject(ctx: Any) -> Any:
    subject = ctx.subject
    if subject is None or subject._meta.label not in {"storage.File", "storage.Drive"}:
        raise ValidationError("Archive imports require a storage.File or storage.Drive subject.")
    return subject


def _proposal(extractor: type[ArchiveExtractor]) -> ArchiveProposal:
    return ArchiveProposal(
        extractor=extractor.key, label=extractor.display_label(), target_resource=extractor.target_resource,
    )


def _validate_proposals(proposals: list[ArchiveProposal]) -> str | None:
    if not proposals or len({proposal.extractor for proposal in proposals}) != len(proposals):
        raise ValidationError("Archive review requires distinct recognized extractors.")
    for proposal in proposals:
        if proposal != _proposal(ArchiveExtractor.resolve_class(proposal.extractor)):
            raise ValidationError("Archive extractor metadata changed after recognition.")
    resources = {proposal.target_resource for proposal in proposals}
    return resources.pop() if len(resources) == 1 else None


class ArchiveProbe(Step[None, ArchiveProbeOutput, None]):
    """Run each matching provider's bounded recognition in IO mode."""

    key = "archive_probe"
    label = "Probe archive"
    mode = StepMode.IO
    outcomes = {"recognized": "Recognized", "unrecognized": "Unrecognized"}

    def run(self, ctx: Any) -> Settlement:
        subject = _subject(ctx)
        proposals = []
        for extractor in ArchiveExtractor.registered_classes():
            if extractor.subject_resource != subject._meta.label:
                continue
            ctx.heartbeat()
            recognized = extractor().recognizes(subject)
            if type(recognized) is not bool:
                raise TypeError(f"{extractor.__name__}.recognizes() must return bool.")
            if recognized:
                proposals.append(_proposal(extractor))
        ctx.record(subject, "Archive source")
        return ctx.done(ArchiveProbeOutput(proposals=proposals), outcome="recognized" if proposals else "unrecognized")


class ArchiveGate(DecisionStep[ArchiveProbeOutput, list[ArchiveMappingUnit], ArchiveGateConfig]):
    """Choose one readable writable target for the recognized archive."""

    key = "archive_gate"
    label = "Confirm archive targets"
    outcomes = {"mapped": "Mapped", "skipped": "Skipped", "unsupported": "Missing or incompatible targets"}

    def ask(self, ctx: Any) -> Settlement:
        subject = _subject(ctx)
        proposals = ctx.input.proposals
        resource = _validate_proposals(proposals)
        if resource is None:
            return ctx.done([], outcome="unsupported")
        targets = list(apps.get_model(resource).objects.with_actor(ctx.actor).for_write().order_by("pk"))
        if not targets:
            return ctx.done([], outcome="unsupported")
        mappings = {
            public_id_of(target): [
                ArchiveMappingUnit(extractor=item.extractor, target=public_id_of(target)).model_dump()
                for item in proposals
            ]
            for target in targets
        }
        assignee = ctx.load(apps.get_model("iam.User"), ctx.config.assignee) if ctx.config.assignee else ctx.actor
        return ctx.ask(
            DecisionRequest(
                kind="map-archive",
                records=(subject, *targets),
                assignees=(assignee,),
                requester=None,
                proposal=DecisionProposal.model_validate(
                    {
                        "alternatives": (
                            *(
                                {"key": public_id_of(target), "label": str(target), "outcome": "mapped"}
                                for target in targets
                            ),
                            {"key": "skip", "label": "Skip archive", "outcome": "skipped"},
                        )
                    }
                ),
                context=DecisionContext(
                    facts=(
                        DecisionFact(
                            pointer="/mappings",
                            label="Archive targets",
                            value=cast(JsonValue, mappings),
                            authority=FactAuthority.SOURCE,
                        ),
                    )
                ),
            ),
            state={"mappings": mappings},
        )

    def continue_with(self, ctx: Any, decision: Any, outcome: str) -> Settlement:
        if outcome == "skipped":
            return ctx.done([], outcome="skipped")
        resource = _validate_proposals(ctx.input.proposals)
        mappings = [ArchiveMappingUnit.model_validate(item) for item in ctx.state["mappings"][decision.verdict[0]]]
        for item in mappings:
            ctx.record(ctx.load(apps.get_model(resource), item.target), "Archive import target")
        return ctx.done(mappings, outcome="mapped")


class ArchiveExecute(Step[ArchiveMappingUnit, ArchiveExecutionOutput, None]):
    """Authorize the target for writing before a fenced extractor effect."""

    key = "archive_execute"
    label = "Import archive unit"
    mode = StepMode.IO
    effect_idempotent = True
    outcomes = {"completed": "Completed"}

    def run(self, ctx: Any) -> Settlement:
        subject = _subject(ctx)
        unit = ctx.input
        extractor = ArchiveExtractor.resolve_class(unit.extractor)
        if extractor.subject_resource != subject._meta.label:
            raise ValidationError("Archive extractor does not accept this storage container.")
        target = ctx.load(apps.get_model(extractor.target_resource), unit.target, permission="write")
        target_id = public_id_of(target)
        ctx.heartbeat()
        ctx.begin_effect()
        result = extractor().execute(subject, target_id, ArchiveExecutionReporter(ctx))
        ctx.record(target, "Imported archive target")
        return ctx.done(
            ArchiveExecutionOutput(extractor=unit.extractor, target=target_id, result=result), outcome="completed"
        )


class ArchiveSummary(Step[list[MapItem[ArchiveExecutionOutput]], list[MapItem[ArchiveExecutionOutput]], None]):
    """Retain ordered item evidence after either map outcome."""

    key = "archive_summary"
    label = "Summarize archive import"
    outcomes = {"complete": "Complete", "partial": "Partial import"}

    def run(self, ctx: Any) -> Settlement:
        return ctx.done(ctx.input, outcome="partial" if any(item.outcome == "error" for item in ctx.input)
                        else "complete")
