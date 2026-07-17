"""Vendor-free archive extraction workflow steps.

Archive extractor classes arrive through
``ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES``. Each extractor recognizes a
``storage.File`` with a hard boolean result and executes through the target
domain's own idempotent ingest surface. The workflow steps only orchestrate that
contract: probe emits proposals, gate authors the serializable mapping form, and
execute prepares and runs the stock ``MapStep`` units.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from rebac import system_context
from rebac.actors import to_subject_ref

from angee.base.impl import ImplBase, impl_registry, resolve_impl_class
from angee.workflows.steps import DecisionSpec, StepImpl, StepResult, positive_int

ARCHIVE_EXTRACTOR_CLASSES_SETTING = "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES"
"""Settings mapping from stable archive extractor keys to trusted class paths."""

_EXECUTE_MODES = frozenset({"prepare", "unit"})


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

    def heartbeat(self, *, at: datetime | None = None) -> None:
        """Refresh the mapped step-run heartbeat."""

        self.step.heartbeat(self.step_run, at=at)


class ArchiveExtractor(ImplBase, ABC):
    """Base contract for a settings-registered archive extractor.

    Subclasses declare a stable :attr:`key`, a human :attr:`label`, and
    ``target_resource`` as an ``app_label.Model`` string. ``recognizes(file)``
    returns a real :class:`bool` — confidence scores and truthy substitutes are
    not accepted. ``execute(file, target_pk, reporter)`` must land content via
    the target domain's own idempotent ingest API and return JSON-safe journal
    output. Vendor parsing and target-domain identity rules stay on the concrete
    extractor and its owning addon.
    """

    target_resource: ClassVar[str] = ""

    @abstractmethod
    def recognizes(self, file: Any) -> bool:
        """Return whether ``file`` is an archive this extractor owns.

        Recognition must read only a bounded header/prefix of ``file`` — the
        probe invokes every registered extractor against the same file, so a
        full-body read amplifies storage I/O by the registry size.
        """

        raise NotImplementedError

    @abstractmethod
    def execute(
        self,
        file: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> Any:
        """Idempotently ingest ``file`` into ``target_pk`` and return journal output."""

        raise NotImplementedError


def archive_extractor_classes() -> tuple[type[ArchiveExtractor], ...]:
    """Return configured extractor classes in deterministic stable-key order."""

    classes: list[type[ArchiveExtractor]] = []
    for key in sorted(impl_registry(ARCHIVE_EXTRACTOR_CLASSES_SETTING)):
        extractor = cast(
            type[ArchiveExtractor],
            resolve_impl_class(ARCHIVE_EXTRACTOR_CLASSES_SETTING, key, ArchiveExtractor),
        )
        _validate_extractor_declaration(key, extractor)
        classes.append(extractor)
    return tuple(classes)


def archive_extractor_class(key: str) -> type[ArchiveExtractor]:
    """Resolve and validate the extractor registered as ``key``."""

    extractor = cast(
        type[ArchiveExtractor],
        resolve_impl_class(ARCHIVE_EXTRACTOR_CLASSES_SETTING, key, ArchiveExtractor),
    )
    _validate_extractor_declaration(key, extractor)
    return extractor


class ArchiveProbeStepImpl(StepImpl):
    """Probe a workflow run's storage file with every configured extractor."""

    key = "archive_probe"
    label = "Probe archive"
    category = "Activity"

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Return stable extractor proposals or the routable ``failed`` outcome."""

        del now
        file = _subject_file(step_run)
        proposals: list[dict[str, str]] = []
        for extractor_class in archive_extractor_classes():
            recognized = extractor_class().recognizes(file)
            if not isinstance(recognized, bool):
                raise TypeError(
                    f"{extractor_class.__name__}.recognizes() must return bool, "
                    f"got {type(recognized).__name__}."
                )
            if recognized:
                proposals.append(_proposal(extractor_class))
        return StepResult.done(
            output={"proposals": proposals},
            outcome="recognized" if proposals else "failed",
        )


class ArchiveGateStepImpl(StepImpl):
    """Suspend for a fixed-row extractor-to-target mapping decision.

    v1 renders one shared rows template, so every recognized extractor must
    declare the same target resource; heterogeneous archives route down the
    ``failed`` edge until per-resource row grouping ships.
    """

    key = "archive_gate"
    label = "Map archive targets"
    category = "Control"

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate optional decision action, assignee, and attempt settings."""

        super().validate_config(config)
        if "action" in config and not str(config.get("action") or "").strip():
            raise ValidationError({"config": "Archive gate action must be a non-empty string."})
        if "assignee" in config and not str(config.get("assignee") or "").strip():
            raise ValidationError({"config": "Archive gate assignee must be a non-empty subject ref."})
        positive_int(config.get("max_attempts", 3), "Archive gate max_attempts")

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Author the mapping form from probe output and suspend one decision."""

        del now
        proposals = _input_proposals(step_run.input)
        target_resources = sorted({proposal["target_resource"] for proposal in proposals})
        if len(target_resources) != 1:
            return StepResult.done(
                output={
                    "proposals": proposals,
                    "target_resources": target_resources,
                    "unsupported": "Archive mapping rows require one shared target resource.",
                },
                outcome="failed",
            )
        target_resource = target_resources[0]
        config = dict(step_run.step.config)
        assignee = str(config.get("assignee") or _run_owner_subject(step_run.run))
        mappings = [
            {
                "extractor": proposal["extractor"],
                "label": proposal["label"],
                "target": "",
            }
            for proposal in proposals
        ]
        return StepResult.suspend(
            resume_state={"gate": {"policy": "one_done"}},
            decisions=(
                DecisionSpec(
                    assignees=(assignee,),
                    action=str(config.get("action") or "map-archive"),
                    payload={"mappings": mappings},
                    max_attempts=positive_int(config.get("max_attempts", 3), "Archive gate max_attempts"),
                    decision_schema=_mapping_form_schema(target_resource),
                ),
            ),
        )


class ArchiveExecuteStepImpl(StepImpl):
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

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Require an explicit prepare/unit execution mode."""

        super().validate_config(config)
        mode = str(config.get("mode") or "")
        if mode not in _EXECUTE_MODES:
            raise ValidationError(
                {"config": f"Archive execute mode must be one of {', '.join(sorted(_EXECUTE_MODES))}."}
            )

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Prepare confirmed mappings or execute the mapped extractor unit."""

        mode = str(step_run.step.config.get("mode") or "")
        if mode == "prepare":
            return StepResult.done(output=_prepared_mappings(step_run.input), outcome="prepared")

        file = _subject_file(step_run)
        extractor_key, target_pk = _mapping_unit(step_run.input)
        extractor_class = archive_extractor_class(extractor_key)
        reporter = ArchiveExecutionReporter(step=self, step_run=step_run)
        reporter.heartbeat(at=now)
        result = extractor_class().execute(file, target_pk, reporter)
        return StepResult.done(
            output={
                "extractor": extractor_key,
                "target": target_pk,
                "result": result,
            },
            outcome="completed",
        )


def _validate_extractor_declaration(key: str, extractor: type[ArchiveExtractor]) -> None:
    """Fail fast when configured extractor metadata disagrees with its registry key."""

    if extractor.key != key:
        raise ImproperlyConfigured(
            f"settings.{ARCHIVE_EXTRACTOR_CLASSES_SETTING}[{key!r}] resolves "
            f"{extractor.__name__} with key {extractor.key!r}."
        )
    if not extractor.display_label().strip():
        raise ImproperlyConfigured(f"Archive extractor {key!r} must declare a display label.")
    target_resource = str(extractor.target_resource or "")
    app_label, separator, model_name = target_resource.partition(".")
    if not separator or not app_label or not model_name or "." in model_name:
        raise ImproperlyConfigured(
            f"Archive extractor {key!r} target_resource must be an app_label.Model string."
        )
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError as error:
        raise ImproperlyConfigured(
            f"Archive extractor {key!r} target_resource {target_resource!r} is not installed."
        ) from error
    if model._meta.label != target_resource:
        raise ImproperlyConfigured(
            f"Archive extractor {key!r} target_resource must use canonical label {model._meta.label!r}."
        )


def _proposal(extractor: type[ArchiveExtractor]) -> dict[str, str]:
    """Return one JSON-safe probe proposal owned by ``extractor``."""

    return {
        "extractor": extractor.key,
        "label": extractor.display_label(),
        "target_resource": extractor.target_resource,
    }


def _subject_file(step_run: Any) -> Any:
    """Return the run's storage file subject or reject the workflow definition."""

    file_model = apps.get_model("storage", "File")
    subject = step_run.run.subject
    if subject is None or not isinstance(subject, file_model):
        raise ValidationError({"subject": "Archive workflow runs require a storage.File subject."})
    return subject


def _input_proposals(value: Any) -> list[dict[str, str]]:
    """Return validated probe proposals from a gate step's input."""

    if not isinstance(value, Mapping) or not isinstance(value.get("proposals"), list):
        raise ValidationError({"input": "Archive gate input must contain probe proposals."})
    proposals: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, value_proposal in enumerate(value["proposals"]):
        if not isinstance(value_proposal, Mapping):
            raise ValidationError({"input": f"Archive proposal {index + 1} must be an object."})
        proposal = {
            "extractor": str(value_proposal.get("extractor") or ""),
            "label": str(value_proposal.get("label") or ""),
            "target_resource": str(value_proposal.get("target_resource") or ""),
        }
        # The proposal content is trusted same-run probe output; resolving the
        # key still guards against registry drift between probe and gate.
        _registered_extractor(proposal["extractor"], owner="input")
        if proposal["extractor"] in seen:
            raise ValidationError({"input": f"Archive extractor {proposal['extractor']!r} is proposed twice."})
        seen.add(proposal["extractor"])
        proposals.append(proposal)
    if not proposals:
        raise ValidationError({"input": "Archive gate requires at least one recognized extractor."})
    return proposals


def _registered_extractor(key: str, *, owner: str) -> type[ArchiveExtractor]:
    """Resolve ``key`` for input validation, keying registry drift as input errors."""

    try:
        return archive_extractor_class(key)
    except ImproperlyConfigured as error:
        raise ValidationError({owner: f"Archive extractor {key!r} is not registered."}) from error


def _mapping_form_schema(target_resource: str) -> dict[str, Any]:
    """Return the serializable fixed-row mapping form for ``target_resource``."""

    return {
        "type": "object",
        "required": ["mappings"],
        "properties": {
            "mappings": {
                "type": "array",
                "widget": "rows",
                "label": "Archive mappings",
                "items": {
                    "type": "object",
                    "required": ["extractor", "label", "target"],
                    "properties": {
                        "extractor": {
                            "type": "string",
                            "label": "Extractor key",
                            "readOnly": True,
                        },
                        "label": {
                            "type": "string",
                            "label": "Archive type",
                            "readOnly": True,
                        },
                        "target": {
                            "type": "string",
                            "label": "Target",
                            "relation": {
                                "resource": target_resource,
                                "create": {"resource": target_resource},
                            },
                        },
                    },
                },
            }
        },
    }


def _run_owner_subject(run: Any) -> str:
    """Return the run creator's REBAC subject ref as the mapping assignee."""

    owner_id = getattr(run, "created_by_id", None)
    if owner_id is None:
        raise ValidationError({"run": "Archive mapping gates require a run creator or explicit assignee."})
    user_model = run._meta.get_field("created_by").related_model
    with system_context(reason="workflows_integrate.archive_gate.owner"):
        owner = user_model._base_manager.get(pk=owner_id)
    return str(to_subject_ref(owner))


def _prepared_mappings(value: Any) -> list[dict[str, str]]:
    """Load completed decision resolutions and return verified map items."""

    if not isinstance(value, Mapping) or not isinstance(value.get("decisions"), list):
        raise ValidationError({"input": "Archive execute prepare input must contain decision ids."})
    decision_ids = [str(decision_id) for decision_id in value["decisions"]]
    if not decision_ids or any(not decision_id for decision_id in decision_ids):
        raise ValidationError({"input": "Archive execute prepare input requires completed decisions."})

    decision_model = apps.get_model("workflows", "Decision")
    with system_context(reason="workflows_integrate.archive_execute.decisions"):
        decisions = {
            str(decision.sqid): decision
            for decision in decision_model._base_manager.filter(sqid__in=decision_ids)
        }
    if set(decisions) != set(decision_ids):
        raise ValidationError({"input": "Archive mapping decision was not found."})

    mappings: list[dict[str, str]] = []
    seen: set[str] = set()
    for decision_id in decision_ids:
        decision = decisions[decision_id]
        verdict = str(getattr(decision.verdict, "value", decision.verdict))
        if verdict != "completed":
            raise ValidationError({"input": "Archive mapping decision must be completed."})
        expected_rows = _mapping_rows(decision.payload, owner="payload")
        resolved_rows = _mapping_rows(decision.resolution, owner="resolution")
        if len(expected_rows) != len(resolved_rows):
            raise ValidationError({"input": "Archive mapping resolution must preserve every proposed row."})
        for expected, resolved in zip(expected_rows, resolved_rows, strict=True):
            extractor_key = str(resolved.get("extractor") or "")
            label = str(resolved.get("label") or "")
            target_pk = str(resolved.get("target") or "")
            if extractor_key != str(expected.get("extractor") or "") or label != str(expected.get("label") or ""):
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
    return mappings


def _mapping_rows(value: Any, *, owner: str) -> list[Mapping[str, Any]]:
    """Return mapping rows from a decision payload or resolution."""

    if not isinstance(value, Mapping) or not isinstance(value.get("mappings"), list):
        raise ValidationError({"input": f"Archive mapping decision {owner} is invalid."})
    rows = value["mappings"]
    if any(not isinstance(row, Mapping) for row in rows):
        raise ValidationError({"input": f"Archive mapping decision {owner} rows must be objects."})
    return cast(list[Mapping[str, Any]], rows)


def _mapping_unit(value: Any) -> tuple[str, str]:
    """Return one extractor/target pair from a stock map child input."""

    if not isinstance(value, Mapping):
        raise ValidationError({"input": "Archive execute unit input must be an object."})
    extractor_key = str(value.get("extractor") or "")
    target_pk = str(value.get("target") or "")
    if not extractor_key or not target_pk:
        raise ValidationError({"input": "Archive execute unit requires extractor and target."})
    return extractor_key, target_pk
