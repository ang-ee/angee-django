"""Vendor-free archive extraction workflow steps.

Archive extractor classes arrive through
``ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES``. Each extractor recognizes its
``storage.File`` or ``storage.Drive`` subject with a boolean result and executes through the target
domain's own idempotent ingest surface. The workflow steps only orchestrate that
contract: probe emits proposals, gate authors the serializable mapping form, and
execute prepares and runs the stock ``MapStep`` units.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Literal, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from pydantic import BaseModel, ConfigDict, Field, RootModel
from rebac import system_context

from angee.base.db import get_write_alias, related_on
from angee.base.impl import ImplBase, resolve_all_impl_classes, resolve_impl_class
from angee.base.permissions import require_authorization_database
from angee.workflows import engine
from angee.workflows.attempts import DecisionGateOutput, validate_json_value
from angee.workflows.configs import NonBlankString, WorkflowStepConfig
from angee.workflows.decision_actions import ReviewAction
from angee.workflows.steps import (
    DecisionApplyStep,
    GateStep,
    StepEffect,
    StepExecutionMode,
    StepImpl,
    StepOutcome,
    StepResult,
)

ARCHIVE_EXTRACTOR_CLASSES_SETTING = "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES"
"""Settings mapping from stable archive extractor keys to trusted class paths."""


class ArchiveGateConfig(WorkflowStepConfig):
    """Reviewer and action metadata for the archive mapping gate."""

    action: NonBlankString = "map-archive"
    assignee: NonBlankString | None = None
    max_attempts: int = Field(default=3, ge=1)


class ArchiveExecuteConfig(WorkflowStepConfig):
    """Choose preparation of a reviewed batch or execution of one Map item."""

    mode: Literal["prepare", "unit"]


class ArchiveProposal(BaseModel):
    """One registered extractor and its review metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    extractor: str
    label: str
    target_resource: str


class ArchiveProbeOutput(BaseModel):
    """Registered extractor proposals retained for review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposals: list[ArchiveProposal]


class ArchiveMappingUnit(BaseModel):
    """One admitted extractor-to-target mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    extractor: str = Field(min_length=1, pattern=r"\S")
    target: str = Field(min_length=1, pattern=r"\S")


class ArchiveMappingRow(BaseModel):
    """A review row, whose target is blank until the reviewer supplies it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    extractor: str = Field(json_schema_extra={"label": "Extractor key", "readOnly": True})
    label: str = Field(json_schema_extra={"label": "Archive type", "readOnly": True})
    target: str = Field(json_schema_extra={"label": "Target"})


class ArchiveMappings(BaseModel):
    """Read-only projection of mapping rows retained in a Decision payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mappings: list[ArchiveMappingRow]


class ArchiveMappingResolution(ArchiveMappings):
    """The reviewer's mapping action retained by the Decision owner."""

    action: Literal["apply_mappings"]


class ArchiveUnsupportedOutput(BaseModel):
    """Typed failure projection for heterogeneous archive proposals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposals: list[ArchiveProposal]
    target_resources: list[str]
    unsupported: str


class ArchiveGateOutput(RootModel[DecisionGateOutput | ArchiveUnsupportedOutput]):
    """Typed review evidence or the unsupported heterogeneous proposal result."""


class ArchiveExecuteInput(RootModel[DecisionGateOutput | ArchiveMappingUnit]):
    """Typed prepare-gate or stock-map unit input."""


class ArchiveExecutionOutput(BaseModel):
    """One extractor's durable workflow journal result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    extractor: str
    target: str
    result: Any


class ArchiveExecuteOutput(RootModel[list[ArchiveMappingUnit] | ArchiveExecutionOutput]):
    """Typed prepared mappings or one extractor execution result."""


@dataclass(frozen=True, slots=True)
class ArchiveExecutionReporter:
    """Workflow-owned progress reporter passed to one extractor execution.

    The current engine has one durable progress primitive: the step heartbeat.
    Extractors call :meth:`heartbeat` during long ingest work; richer progress
    remains an engine concern rather than vendor state hidden in this addon.
    The wrapper is a deliberate capability-narrowing boundary: vendor extractor
    code receives only this reporter, never the ``StepImpl``/``step_run``
    surface — ``mark_failed``, ``resume_state``, and the engine verbs stay
    workflow-owned.
    """

    step: StepImpl
    step_run: Any
    using: str | None = None

    def heartbeat(self, *, at: datetime | None = None) -> None:
        """Refresh the mapped step-run heartbeat."""

        self.step.heartbeat(self.step_run, at=at, using=self.using)


class ArchiveExtractor(ImplBase, ABC):
    """Base contract for a settings-registered archive extractor.

    Subclasses declare a stable :attr:`key`, a human :attr:`label`,
    ``target_resource`` as an ``app_label.Model`` string, and
    ``subject_resource`` — the storage container kind the run subject is, a
    ``storage.File`` (an archive to open) or a ``storage.Drive`` (a mounted tree
    to inspect). ``recognizes(subject)`` returns a real :class:`bool` —
    confidence scores and truthy substitutes are not accepted.
    ``execute(subject, target_pk, reporter)`` must land content via the target
    domain's own idempotent ingest API and return JSON-safe journal output.
    Vendor parsing and target-domain identity rules stay on the concrete
    extractor and its owning addon. Subjects are pinned to the operation's
    database, also exposed as ``reporter.using``. Archive workflows currently
    require the default database: Decision authorization and external backup
    ingest owners do not yet support a complete multi-database operation.
    """

    target_resource: ClassVar[str] = ""
    subject_resource: ClassVar[str] = "storage.File"

    @abstractmethod
    def recognizes(self, subject: Any) -> bool:
        """Return whether ``subject`` is a container this extractor owns.

        ``subject`` is the run subject named by :attr:`subject_resource`. The
        probe only invokes extractors whose ``subject_resource`` matches the run
        subject, so a file extractor never sees a drive. Recognition must stay
        bounded: read only a header/prefix of a file, or a metadata lookup plus a
        bounded manifest probe of a drive — the probe runs every matching
        extractor against the same subject.
        """

        raise NotImplementedError

    @abstractmethod
    def execute(
        self,
        subject: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> Any:
        """Idempotently ingest ``subject`` into ``target_pk`` and return journal output."""

        raise NotImplementedError


def archive_extractor_classes() -> tuple[type[ArchiveExtractor], ...]:
    """Return configured extractor classes in deterministic stable-key order."""

    classes = cast(
        tuple[type[ArchiveExtractor], ...],
        resolve_all_impl_classes(
            ARCHIVE_EXTRACTOR_CLASSES_SETTING,
            ArchiveExtractor,
        ),
    )
    for extractor in classes:
        _validate_extractor_declaration(extractor)
    return classes


def archive_extractor_class(key: str) -> type[ArchiveExtractor]:
    """Resolve and validate the extractor registered as ``key``."""

    extractor = cast(
        type[ArchiveExtractor],
        resolve_impl_class(ARCHIVE_EXTRACTOR_CLASSES_SETTING, key, ArchiveExtractor),
    )
    _validate_extractor_declaration(extractor)
    return extractor


class ArchiveProbeStepImpl(StepImpl):
    """Probe a workflow run's storage container with every matching extractor."""

    key = "archive_probe"
    label = "Probe archive"
    category = "Activity"

    config_model = WorkflowStepConfig
    output_model = ArchiveProbeOutput
    outcomes = (StepOutcome("recognized", "Recognized"), StepOutcome("failed", "Unrecognized"))
    effect = StepEffect.READ
    effect_description = "Inspects the archive through the configured extractors without ingesting it."
    idempotent = True
    deterministic = False

    def run(self, step_run: Any, *, now: datetime, using: str | None = None) -> StepResult:
        """Return stable extractor proposals or the routable ``failed`` outcome."""

        del now
        alias = _archive_alias(step_run, using=using)
        subject = _subject_container(step_run, using=alias)
        subject_resource = subject._meta.label
        proposals: list[ArchiveProposal] = []
        with self.heartbeat_during(step_run, using=alias):
            for extractor_class in archive_extractor_classes():
                if extractor_class.subject_resource != subject_resource:
                    continue
                recognized = extractor_class().recognizes(subject)
                if not isinstance(recognized, bool):
                    raise TypeError(
                        f"{extractor_class.__name__}.recognizes() must return bool, got {type(recognized).__name__}."
                    )
                if recognized:
                    proposals.append(_proposal(extractor_class))
        return StepResult.done(
            output=ArchiveProbeOutput(proposals=proposals).model_dump(mode="json"),
            outcome="recognized" if proposals else "failed",
        )


class ArchiveGateStepImpl(GateStep):
    """Suspend for a fixed-row extractor-to-target mapping decision.

    Mapping rows share one target resource, so every recognized extractor must
    declare the same target resource. Heterogeneous archives route down the
    ``failed`` edge.
    """

    key = "archive_gate"
    label = "Map archive targets"
    category = "Control"
    config_model = ArchiveGateConfig
    input_model = ArchiveProbeOutput
    output_model = ArchiveGateOutput
    outcomes = (*GateStep.outcomes, StepOutcome("failed", "Unsupported archive"))

    def run(self, step_run: Any, *, now: datetime, using: str | None = None) -> StepResult:
        """Route unsupported mixed-resource proposals before using the built-in gate."""

        del now
        alias = _archive_alias(step_run, using=using)
        with system_context(reason="workflows_integrate.archive_gate"):
            step_run.step = related_on(step_run, "step", using=alias)
            step_run.run = related_on(step_run, "run", using=alias)
        value = self.validate_input(step_run.input)
        proposals = value.proposals
        _validate_proposals(proposals)
        target_resources = sorted({proposal.target_resource for proposal in proposals})
        if len(target_resources) != 1:
            return StepResult.done(
                output=ArchiveUnsupportedOutput(
                    proposals=proposals,
                    target_resources=target_resources,
                    unsupported="Archive mapping rows require one shared target resource.",
                ).model_dump(mode="json"),
                outcome="failed",
            )
        resumed = self.resumption(step_run)
        if resumed is not None:
            return StepResult.done(output=resumed.resolutions, outcome=resumed.outcome)
        return self.gate_result(
            step_run,
            config=self._archive_gate_config(step_run, proposals=proposals, target_resource=target_resources[0]),
        )

    @classmethod
    def _archive_gate_config(
        cls,
        step_run: Any,
        *,
        proposals: list[ArchiveProposal],
        target_resource: str,
    ) -> Mapping[str, Any]:
        """Author one built-in gate config from the admitted probe output."""

        alias = get_write_alias(type(step_run), instance=step_run)
        config = cls.normalize_config(step_run.step.config)
        assignee = str(engine.resolve_workflow_actor(config["assignee"] or step_run.run, using=alias).subject)
        mappings = [
            {
                "extractor": proposal.extractor,
                "label": proposal.label,
                "target": "",
            }
            for proposal in proposals
        ]
        return {
            "policy": "one_done",
            "action": config["action"],
            "slots": [{"assignees": [assignee]}],
            "payload": {"mappings": mappings},
            "max_attempts": config["max_attempts"],
            "actions": [
                ReviewAction(
                    value="apply_mappings",
                    label="Apply mappings",
                    verdict="COMPLETE",
                    fields=("mappings",),
                    required=("mappings",),
                ).model_dump(mode="json")
            ],
            "properties": {"mappings": _mapping_rows_schema(target_resource)},
        }


class ArchiveExecuteStepImpl(DecisionApplyStep):
    """Prepare a confirmed decision mapping or execute one stock-map unit.

    A workflow uses this implementation twice: ``mode=prepare`` follows the
    archive gate and turns its completed decision into a plain mapping list;
    the built-in ``map`` step consumes that list and targets a second step with
    ``mode=unit``. This keeps decision lookup outside the generic map engine and
    preserves its existing per-unit partial-failure accounting.
    """

    key = "archive_execute"
    label = "Execute archive import"
    category = "Activity"
    deterministic = False
    config_model = ArchiveExecuteConfig
    input_model = ArchiveExecuteInput
    output_model = ArchiveExecuteOutput
    outcomes = (
        StepOutcome("prepared", "Prepared"),
        StepOutcome("completed", "Completed"),
    )
    effect = StepEffect.EXTERNAL
    execution_mode = StepExecutionMode.STANDARD
    effect_description = "Consumes a reviewed mapping or invokes its registered archive extractor."
    idempotent = True
    gate_step_class = ArchiveGateStepImpl

    def run(self, step_run: Any, *, now: datetime, using: str | None = None) -> StepResult:
        """Prepare confirmed mappings or execute the mapped extractor unit."""

        alias = _archive_alias(step_run, using=using)
        with system_context(reason="workflows_integrate.archive_execute"):
            step_run.step = related_on(step_run, "step", using=alias)
        config = self.normalize_config(step_run.step.config)
        value = self.validate_input(step_run.input).root
        mode = config["mode"]
        if mode == "prepare":
            if not isinstance(value, DecisionGateOutput):
                raise ValidationError({"input": "Archive preparation requires a completed mapping gate."})
            return super().run(step_run, now=now)

        if not isinstance(value, ArchiveMappingUnit):
            raise ValidationError({"input": "Archive execute unit requires extractor and target."})
        subject = _subject_container(step_run, using=alias)
        extractor_key, target_pk = value.extractor, value.target
        extractor_class = archive_extractor_class(extractor_key)
        if extractor_class.subject_resource != subject._meta.label:
            raise ValidationError({"subject": "Archive extractor does not accept this storage container."})
        reporter = ArchiveExecutionReporter(step=self, step_run=step_run, using=alias)
        with self.heartbeat_during(step_run, using=alias):
            result = extractor_class().execute(subject, target_pk, reporter)
        return StepResult.done(
            output={
                "extractor": extractor_key,
                "target": target_pk,
                "result": result,
            },
            outcome="completed",
        )

    def invoke_command(self, step_run: Any, *, decision_id: int, actor: Any, now: datetime) -> StepResult:
        """Prepare mapping values only from a locked, validated Decision resolution."""

        del now
        alias = get_write_alias(type(step_run), instance=step_run)
        with (
            apps.get_model("workflows", "Decision")
            .objects.db_manager(alias)
            .locked_resolution(
                decision_id,
                actor=actor,
                consumer_step_run_id=step_run.pk,
            ) as decision
        ):
            mappings: list[dict[str, str]] = []
            seen: set[str] = set()
            expected_rows = validate_json_value(ArchiveMappings.model_validate_json, decision.payload).mappings
            resolved_rows = validate_json_value(
                ArchiveMappingResolution.model_validate_json, decision.resolution
            ).mappings
            if len(expected_rows) != len(resolved_rows):
                raise ValidationError({"input": "Archive mapping resolution must preserve every proposed row."})
            for expected, resolved in zip(expected_rows, resolved_rows, strict=True):
                extractor_key = resolved.extractor
                label = resolved.label
                target_pk = resolved.target
                if extractor_key != expected.extractor or label != expected.label:
                    raise ValidationError({"input": "Archive mapping resolution changed a proposed extractor."})
                extractor = _registered_extractor(extractor_key, owner="input")
                if label != extractor.display_label():
                    raise ValidationError({"input": "Archive mapping resolution has stale extractor metadata."})
                if not target_pk:
                    raise ValidationError({"input": f"Archive extractor {extractor_key!r} requires a target."})
                if extractor_key in seen:
                    raise ValidationError({"input": f"Archive extractor {extractor_key!r} is mapped twice."})
                seen.add(extractor_key)
                mappings.append({"extractor": extractor_key, "target": target_pk})
            return StepResult.done(output=mappings, outcome="prepared")


def _validate_extractor_declaration(extractor: type[ArchiveExtractor]) -> None:
    """Fail fast when configured extractor metadata is incomplete."""

    key = extractor.key
    if not extractor.display_label().strip():
        raise ImproperlyConfigured(f"Archive extractor {key!r} must declare a display label.")
    _validate_resource_label(key, "target_resource", extractor.target_resource)
    _validate_resource_label(key, "subject_resource", extractor.subject_resource)


def _validate_resource_label(key: str, attr: str, value: str) -> None:
    """Validate one ``app_label.Model`` extractor attribute is installed and canonical."""

    label = str(value or "")
    app_label, separator, model_name = label.partition(".")
    if not separator or not app_label or not model_name or "." in model_name:
        raise ImproperlyConfigured(f"Archive extractor {key!r} {attr} must be an app_label.Model string.")
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError as error:
        raise ImproperlyConfigured(f"Archive extractor {key!r} {attr} {label!r} is not installed.") from error
    if model._meta.label != label:
        raise ImproperlyConfigured(f"Archive extractor {key!r} {attr} must use canonical label {model._meta.label!r}.")


def _proposal(extractor: type[ArchiveExtractor]) -> ArchiveProposal:
    """Return one typed probe proposal owned by ``extractor``."""

    return ArchiveProposal(
        extractor=extractor.key,
        label=extractor.display_label(),
        target_resource=extractor.target_resource,
    )


def _archive_alias(step_run: Any, *, using: str | None) -> str:
    """Pin the operation and fail before entering default-only review and ingest owners."""

    alias = get_write_alias(type(step_run), using=using, instance=step_run)
    require_authorization_database(
        alias,
        operation="Archive workflows and their external ingest owners",
        error_field="using",
    )
    step_run._state.db = alias
    return alias


def _subject_container(step_run: Any, *, using: str) -> Any:
    """Load the File or Drive subject through Django's generic relation on the write alias."""

    with system_context(reason="workflows_integrate.archive_subject"):
        run = related_on(step_run, "run", using=using)
        if run is None:
            raise ValidationError({"subject": "Archive steps require a workflow run."})
        content_type = related_on(run, "subject_content_type", using=using)
        file_model = apps.get_model("storage", "File")
        drive_model = apps.get_model("storage", "Drive")
        if content_type is None or content_type.model_class() not in (file_model, drive_model):
            raise ValidationError({"subject": "Archive workflow runs require a storage.File or storage.Drive subject."})
        return content_type.get_object_for_this_type(using=using, pk=run.subject_object_id)


def _validate_proposals(proposals: list[ArchiveProposal]) -> None:
    """Check admitted proposals against the current extractor registry."""

    seen: set[str] = set()
    for proposal in proposals:
        extractor = _registered_extractor(proposal.extractor, owner="input")
        if proposal != _proposal(extractor):
            raise ValidationError({"input": "Archive proposal has stale extractor metadata."})
        if proposal.extractor in seen:
            raise ValidationError({"input": f"Archive extractor {proposal.extractor!r} is proposed twice."})
        seen.add(proposal.extractor)
    if not proposals:
        raise ValidationError({"input": "Archive gate requires at least one recognized extractor."})


def _registered_extractor(key: str, *, owner: str) -> type[ArchiveExtractor]:
    """Resolve ``key`` for input validation, keying registry drift as input errors."""

    try:
        return archive_extractor_class(key)
    except ImproperlyConfigured as error:
        raise ValidationError({owner: f"Archive extractor {key!r} is not registered."}) from error


def _mapping_rows_schema(target_resource: str) -> dict[str, Any]:
    """Return the editable fixed-row mapping property for ``target_resource``."""

    row_schema = ArchiveMappingRow.model_json_schema()
    row_schema["properties"]["target"]["relation"] = {
        "resource": target_resource,
        "create": {"resource": target_resource},
    }
    return {
        "type": "array",
        "widget": "rows",
        "label": "Archive mappings",
        "items": row_schema,
    }
