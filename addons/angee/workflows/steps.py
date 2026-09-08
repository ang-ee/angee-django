"""Step implementation seam for workflow definitions.

Step rows store a registry key in ``step_class``. The configured class owns the
behavior selected by that key and validates the row's ``config`` before the row is
saved. Product addons register their own :class:`StepImpl` subclasses through
``ANGEE_WORKFLOW_STEP_CLASSES``; row data never stores dotted import paths.

Implementations declare ``deterministic``: deterministic implementations may be
replayed for routing, while non-deterministic activity implementations are
journaled by the runtime. Implementations may also declare ``decision_schema`` for
typed resume payloads. Suspension persists no in-process state: ``resume_state``
on the future step-run journal is the only state surviving suspension, and an
implementation must write any continuation facts there before returning a
suspended result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal, Self

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from pydantic import BaseModel
from rebac import system_context

from angee.base.impl import ImplBase, ImplChoice
from angee.workflows.attempts import AttemptResult, AttemptResultKind, DecisionSpec, JsonPresence
from angee.workflows.configs import GateConfig, MapConfig, WaitConfig
from angee.workflows.data_contracts import DataContract, model_data_contract

_MODEL_LABEL_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
_OUTCOME_KEY_FIELD = models.SlugField(max_length=100)


class TransientStepError(Exception):
    """Signal that a step implementation failed with a retryable condition."""


@dataclass(frozen=True, slots=True)
class StepRetryPolicy:
    """Static queue retry policy declared by one step's JSON config."""

    max_attempts: int = 1
    wait: int = 0
    linear_wait: int = 0
    exponential_wait: int = 0

    def delay_for(self, retry_index: int) -> int:
        """Return the declared delay for one positive retry-series index."""

        if retry_index <= 0:
            raise ValueError("Retry index must be positive.")
        if self.wait:
            return self.wait
        if self.linear_wait:
            return self.linear_wait * retry_index
        if self.exponential_wait:
            return self.exponential_wait * (2 ** (retry_index - 1))
        return 0


class StepEffect(str, Enum):
    """Declared external effect category of a workflow operation."""

    UNKNOWN = "unknown"
    NONE = "none"
    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """One labeled routing outcome a step implementation may produce."""

    key: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class StepOperation:
    """Complete authoring metadata projected from one registered step implementation."""

    choice: ImplChoice
    description: str
    selectable: bool
    input_contract: DataContract
    output_contract: DataContract
    outcomes: tuple[StepOutcome, ...]
    effect: StepEffect
    effect_description: str
    idempotent: bool | None
    subject_declaration: str

    @property
    def input_schema(self) -> dict[str, Any] | None:
        """Return the compatibility schema from the Pydantic-owned contract."""

        return self.input_contract.raw_schema

    @property
    def output_schema(self) -> dict[str, Any] | None:
        """Return the compatibility schema from the Pydantic-owned contract."""

        return self.output_contract.raw_schema


@dataclass(frozen=True, slots=True)
class StepResult:
    """Result returned by a workflow step implementation.

    ``done(output, outcome)`` completes the step and routes by ``outcome``.
    ``wait(until=...)`` records a durable timer wake. External events use
    :func:`angee.workflows.engine.deliver`, whose run-scoped generation prevents
    a delivery racing with this wait from being lost. ``suspend()`` pauses the
    step until an external resolution writes the next journal facts.
    """

    kind: str
    output: Any = None
    outcome: str = ""
    until: datetime | None = None
    resume_state: dict[str, Any] | None = None
    decisions: tuple[DecisionSpec, ...] = ()
    waiting_kind: Literal["scheduled", "approval", "external"] | str = ""

    def to_attempt_result(self) -> AttemptResult:
        """Convert the implementation result into the retained closed envelope."""

        kind = AttemptResultKind(self.kind)
        checkpoint = JsonPresence(self.resume_state is not None, self.resume_state)
        return AttemptResult(
            kind=kind,
            output_present=kind == AttemptResultKind.DONE,
            output=self.output if kind == AttemptResultKind.DONE else None,
            checkpoint_present=checkpoint.present,
            checkpoint=checkpoint.value,
            outcome=self.outcome,
            waiting_kind=self.waiting_kind,
            requested_until=self.until,
            decisions=self.decisions,
        )

    @classmethod
    def done(cls, output: Any = None, outcome: str = "") -> Self:
        """Return a completed step result."""

        return cls(kind="done", output=output, outcome=outcome)

    @classmethod
    def wait(
        cls,
        *,
        until: datetime | None = None,
        resume_state: dict[str, Any] | None = None,
        waiting_kind: Literal["scheduled", "approval", "external"] = "scheduled",
    ) -> Self:
        """Return a durable wait result."""

        if until is None:
            raise ValueError("StepResult.wait requires until.")
        return cls(kind="wait", until=until, resume_state=resume_state, waiting_kind=waiting_kind)

    @classmethod
    def suspend(
        cls,
        *,
        resume_state: dict[str, Any] | None = None,
        decisions: list[DecisionSpec] | tuple[DecisionSpec, ...] = (),
        waiting_kind: Literal["scheduled", "approval", "external"] | None = None,
    ) -> Self:
        """Return a suspended step result."""

        decisions_tuple = tuple(decisions)
        resolved_kind = waiting_kind or ("approval" if decisions_tuple else "external")
        return cls(
            kind="suspend",
            resume_state=resume_state,
            decisions=decisions_tuple,
            waiting_kind=resolved_kind,
        )


class StepImpl(ImplBase):
    """Base class for registry-selected workflow step implementations."""

    description: ClassVar[str] = ""
    selectable: ClassVar[bool] = True
    input_model: ClassVar[type[BaseModel] | None] = None
    output_model: ClassVar[type[BaseModel] | None] = None
    outcomes: ClassVar[tuple[StepOutcome, ...]] = ()
    effect: ClassVar[StepEffect] = StepEffect.UNKNOWN
    effect_description: ClassVar[str] = ""
    idempotent: ClassVar[bool | None] = None
    subject_declaration: ClassVar[str] = ""
    deterministic: ClassVar[bool] = True
    decision_schema: ClassVar[type[Any] | None] = None
    map_body_operation: ClassVar[bool] = False

    @classmethod
    def input_contract(cls) -> DataContract:
        """Return Pydantic's declared validation shape for operation input."""

        return model_data_contract(cls.input_model, mode="validation")

    @classmethod
    def output_contract(cls) -> DataContract:
        """Return Pydantic's declared serialization shape for operation output."""

        return model_data_contract(cls.output_model, mode="serialization")

    @classmethod
    def is_executable(cls, *, registered_key: str) -> bool:
        """Return whether the implementation supplies concrete runtime behavior."""

        del registered_key
        return cls.run is not StepImpl.run

    @classmethod
    def map_body_target(cls, config: Any) -> str | None:
        """Return the referenced Map body key when this operation expands one."""

        del config
        return None

    @classmethod
    def operation(cls, *, key: str) -> StepOperation:
        """Return this registered implementation's workflow-owned authoring contract."""

        cls._validate_operation(key=key)
        choice = cls.choice()
        return StepOperation(
            choice=replace(choice, key=key),
            description=cls.description,
            selectable=cls.selectable,
            input_contract=cls.input_contract(),
            output_contract=cls.output_contract(),
            outcomes=cls.outcomes,
            effect=cls.effect,
            effect_description=cls.effect_description,
            idempotent=cls.idempotent,
            subject_declaration=cls.subject_declaration,
        )

    @classmethod
    def _validate_operation(cls, *, key: str) -> None:
        """Reject malformed authoring declarations at their registered key."""

        owner = f"Workflow step implementation {key!r}"
        if not isinstance(cls.effect, StepEffect):
            raise ImproperlyConfigured(f"{owner} declares invalid effect {cls.effect!r}.")
        if not isinstance(cls.outcomes, tuple):
            raise ImproperlyConfigured(f"{owner} declares invalid outcomes {cls.outcomes!r}.")
        seen: set[str] = set()
        for outcome in cls.outcomes:
            if not isinstance(outcome, StepOutcome):
                raise ImproperlyConfigured(f"{owner} declares invalid outcome {outcome!r}.")
            if (
                not isinstance(outcome.key, str)
                or not isinstance(outcome.label, str)
                or not isinstance(outcome.description, str)
            ):
                raise ImproperlyConfigured(f"{owner} declares invalid outcome {outcome!r}.")
            if not outcome.key.strip() or not outcome.label.strip():
                raise ImproperlyConfigured(f"{owner} outcome keys and labels must be non-blank.")
            try:
                _OUTCOME_KEY_FIELD.run_validators(outcome.key)
            except ValidationError as error:
                raise ImproperlyConfigured(f"{owner} declares invalid outcome key {outcome.key!r}.") from error
            if outcome.key in seen:
                raise ImproperlyConfigured(f"{owner} declares duplicate outcome key {outcome.key!r}.")
            seen.add(outcome.key)
        declaration = cls.subject_declaration
        if not isinstance(declaration, str):
            raise ImproperlyConfigured(f"{owner} declares invalid subject label {declaration!r}.")
        if declaration and _MODEL_LABEL_RE.fullmatch(declaration) is None:
            raise ImproperlyConfigured(f"{owner} declares invalid subject label {declaration!r}.")

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate a step row's JSON config for this implementation."""

        if not isinstance(config, Mapping):
            raise ValidationError({"config": "Step config must be a JSON object."})
        if cls.config_model is not None:
            cls.normalize_config(config)

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Execute one step-run journal row."""

        del now
        raise NotImplementedError(f"{type(self).__name__}.run() is supplied by a runtime slice.")

    def heartbeat(self, step_run: Any, *, at: datetime | None = None) -> None:
        """Refresh ``step_run``'s heartbeat while a long implementation is running."""

        if str(getattr(step_run.status, "value", step_run.status)) != "started":
            return
        step_run.heartbeat_at = at or timezone.now()
        with system_context(reason="workflows.step.heartbeat"):
            step_run.save(update_fields=["heartbeat_at", "updated_at"])


def retry_policy_from_config(config: Any) -> StepRetryPolicy:
    """Return the queue retry policy declared by ``config``."""

    if not isinstance(config, Mapping):
        raise ValidationError({"config": "Step config must be a JSON object."})
    retry = config.get("retry")
    if retry in (None, "", False):
        return StepRetryPolicy()
    if not isinstance(retry, Mapping):
        raise ValidationError({"config": "Step retry must be a JSON object."})

    max_attempts = positive_int(retry.get("max_attempts", 1), "Step retry max_attempts")
    wait = 0
    linear_wait = 0
    exponential_wait = 0
    backoff = retry.get("backoff", 0)
    if isinstance(backoff, Mapping):
        wait = non_negative_int(backoff.get("wait", 0), "Step retry backoff.wait")
        linear_wait = non_negative_int(backoff.get("linear_wait", 0), "Step retry backoff.linear_wait")
        exponential_wait = non_negative_int(
            backoff.get("exponential_wait", 0),
            "Step retry backoff.exponential_wait",
        )
    else:
        wait = non_negative_int(backoff, "Step retry backoff")
    return StepRetryPolicy(
        max_attempts=max_attempts,
        wait=wait,
        linear_wait=linear_wait,
        exponential_wait=exponential_wait,
    )


def validate_retry_config(config: Any) -> None:
    """Validate the common per-step retry block."""

    retry_policy_from_config(config)


class HandlerStep(StepImpl):
    """Abstract activity step base registered as the built-in ``handler`` key."""

    key = "handler"
    label = "Handler"
    category = "Activity"
    description = "Legacy abstract activity handler retained for stored workflow compatibility."
    selectable = False
    deterministic = False


class WaitStep(StepImpl):
    """Built-in timer wait step."""

    key = "wait"
    label = "Wait"
    category = "Control"
    description = "Wait until the configured timestamp before routing through the timer outcome."
    outcomes = (StepOutcome("timer", "Timer elapsed"),)
    effect = StepEffect.NONE
    effect_description = "Does not read or change the workflow subject."
    idempotent = True
    config_model = WaitConfig

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Return done once the timer or event condition has arrived."""

        if str(getattr(step_run.status, "value", step_run.status)) == "waiting":
            if step_run.wait_until is not None and step_run.wait_until <= now:
                return StepResult.done(output=step_run.output, outcome="timer")
            return StepResult.wait(
                until=step_run.wait_until,
                resume_state=dict(step_run.resume_state),
            )

        config = dict(step_run.step.config)
        until = _config_until(config.get("until")) if "until" in config else None
        if until is not None and until <= now:
            return StepResult.done(output={}, outcome="timer")
        return StepResult.wait(until=until, resume_state={"config": config})


class GateStep(StepImpl):
    """Built-in gate step that suspends until Slice 4 decision rows exist."""

    key = "gate"
    label = "Gate"
    category = "Control"
    description = "Suspend execution until the configured approval policy resolves."
    outcomes = tuple(
        StepOutcome(key, label)
        for key, label in (
            ("completed", "Completed"),
            ("rejected", "Rejected"),
            ("escalated", "Escalated"),
            ("expired", "Expired"),
        )
    )
    effect = StepEffect.NONE
    effect_description = "Creates workflow decision journals without changing the workflow subject."
    config_model = GateConfig

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Suspend the step, keeping only durable resume state."""

        del now
        config = dict(step_run.step.config)
        return StepResult.suspend(
            resume_state={"gate": config},
            decisions=_decision_specs_from_config(config),
        )


class MapStep(StepImpl):
    """Built-in control step that maps one target step over a journaled item list."""

    key = "map"
    label = "Map"
    category = "Control"
    description = "Run one target step for each configured item and aggregate the child results."
    outcomes = (
        StepOutcome("succeeded", "Succeeded"),
        StepOutcome("failed", "Failed"),
    )
    effect_description = "Effects and idempotency depend on the configured target operation."
    config_model = MapConfig
    map_body_operation = True

    @classmethod
    def is_executable(cls, *, registered_key: str) -> bool:
        """Match the exact operation key selected by the engine's Map query."""

        return registered_key == MapStep.key

    @classmethod
    def map_body_target(cls, config: Any) -> str | None:
        """Return the declared body step key without resolving persistence."""

        if not isinstance(config, Mapping):
            return None
        target = config.get("target_step")
        return str(target) if isinstance(target, str) and target else None

    @classmethod
    def engine_expanded_filter(cls) -> dict[str, Any]:
        """Return the ORM predicate for map parent rows the engine expands."""

        return {"step__step_class": cls.key, "map_index": -1}

    @classmethod
    def target_step(cls, step_run: Any) -> Any:
        """Return the configured target step for one map parent row."""

        config = cls.config_mapping(step_run)
        key = str(config.get("target_step") or "")
        if not key:
            raise ValidationError({"config": "Map steps require target_step."})
        try:
            return step_run.run.workflow.steps.get(key=key)
        except ObjectDoesNotExist as error:
            raise ValidationError({"config": f"Map target step {key!r} does not exist."}) from error

    @classmethod
    def items(cls, step_run: Any) -> list[Any]:
        """Return the item list resolved from this map step's config."""

        expression = cls.config_mapping(step_run).get("items")
        value = cls.expression_value(expression, step_run)
        if not isinstance(value, list):
            raise ValidationError({"config": "Map items expression must resolve to a list."})
        return list(value)

    @classmethod
    def policy_passes(cls, config: Any, output: Mapping[str, Any]) -> bool:
        """Return whether aggregate map output satisfies ``config``."""

        total = int(output["total"])
        successes = int(output["successes"])
        failures = int(output["failures"])
        mapping = config if isinstance(config, Mapping) else {}
        if bool(mapping.get("all_must_succeed", False)):
            return failures == 0 and successes == total
        ratio = optional_number(mapping.get("min_success_ratio", mapping.get("min_success")), "Map min_success_ratio")
        if ratio is None:
            return failures == 0 and successes == total
        if total == 0:
            return ratio <= 0
        return successes / total >= ratio

    @classmethod
    def config_mapping(cls, step_run: Any) -> Mapping[str, Any]:
        """Return the map config as a mapping."""

        config = step_run.step.config
        return config if isinstance(config, Mapping) else {}

    @classmethod
    def expression_value(cls, expression: Any, step_run: Any) -> Any:
        """Resolve a map ``items`` expression against subject, run, or input."""

        if isinstance(expression, list):
            return expression
        if not isinstance(expression, str) or not expression:
            raise ValidationError({"config": "Map steps require an items expression."})
        root, *path = expression.split(".")
        if root == "subject":
            value = step_run.run.subject
        elif root == "run":
            value = step_run.run
        elif root == "input":
            value = step_run.input
        else:
            raise ValidationError({"config": "Map items expression must start with subject, run, or input."})
        for part in path:
            value = cls.lookup(value, part)
        return value

    @staticmethod
    def lookup(value: Any, key: str) -> Any:
        """Read one expression path segment from a mapping or object."""

        if isinstance(value, Mapping):
            return value.get(key)
        return getattr(value, key)


def _config_until(value: Any) -> datetime | None:
    """Return an aware datetime parsed from a wait config value."""

    return _config_datetime(value)


def _config_datetime(value: Any) -> datetime | None:
    """Return an aware datetime parsed from a config value."""

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def positive_int(value: Any, label: str) -> int:
    """Return ``value`` as a positive integer or raise a config error."""

    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError({"config": f"{label} must be an integer."}) from error
    if parsed < 1:
        raise ValidationError({"config": f"{label} must be positive."})
    return parsed


def non_negative_int(value: Any, label: str) -> int:
    """Return ``value`` as a non-negative integer or raise a config error."""

    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError({"config": f"{label} must be an integer."}) from error
    if parsed < 0:
        raise ValidationError({"config": f"{label} must be non-negative."})
    return parsed


def optional_number(value: Any, label: str) -> float | None:
    """Return ``value`` as a number when present, or raise a config error."""

    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValidationError({"config": f"{label} must be a number."})
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValidationError({"config": f"{label} must be a number."}) from error


def optional_positive_int(value: Any) -> int | None:
    """Leniently return ``value`` as a positive integer when possible."""

    parsed = optional_non_negative_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def optional_non_negative_int(value: Any) -> int | None:
    """Leniently return ``value`` as a non-negative integer when possible."""

    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed >= 0 else None


def _decision_specs_from_config(config: Mapping[str, Any]) -> tuple[DecisionSpec, ...]:
    """Return gate decision specs from declarative config."""

    slots = config.get("slots")
    if slots is None:
        slots = [{"assignee": subject} for subject in config.get("assignees", ())]
    action = str(config.get("action", "") or "")
    payload = dict(config.get("payload") or {})
    requester = str(config.get("requester", "") or "")
    escalation = tuple(str(subject) for subject in config.get("escalation", ()) if str(subject))
    max_attempts = config.get("max_attempts")
    parsed_max_attempts = None if max_attempts in (None, "") else int(str(max_attempts))
    expires_at = _config_datetime(config.get("expires_at"))
    escalate_at = _config_datetime(config.get("escalate_at"))
    decision_schema = dict(config.get("decision_schema") or {})
    specs: list[DecisionSpec] = []
    for index, slot in enumerate(slots if isinstance(slots, list) else []):
        if not isinstance(slot, Mapping):
            continue
        specs.append(
            DecisionSpec(
                assignees=_slot_assignees(slot),
                action=action,
                payload=payload,
                priority=int(slot.get("priority", index)),
                requester=str(slot.get("requester", requester) or requester),
                escalation=tuple(
                    str(subject)
                    for subject in (escalation if slot.get("escalation") is None else slot["escalation"])
                    if str(subject)
                ),
                max_attempts=parsed_max_attempts,
                expires_at=expires_at,
                escalate_at=escalate_at,
                decision_schema=decision_schema,
            )
        )
    return tuple(specs)


def _slot_assignees(slot: Mapping[str, Any]) -> tuple[str, ...]:
    """Return normalized assignee subject refs for one gate slot."""

    raw = slot.get("assignees", slot.get("assignee", ()))
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if raw is None:
        return ()
    return tuple(str(subject) for subject in raw if str(subject))
