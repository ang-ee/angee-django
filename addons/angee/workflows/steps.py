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

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Self

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rebac import system_context

from angee.base.impl import ImplBase

GATE_POLICIES = frozenset({"one_done", "all_success", "majority", "sequential"})
"""Seat aggregation policies supported by the built-in gate step."""


class TransientStepError(Exception):
    """Signal that a step implementation failed with a retryable condition."""


@dataclass(frozen=True, slots=True)
class StepRetryPolicy:
    """Static queue retry policy declared by one step's JSON config."""

    max_attempts: int = 1
    wait: int = 0
    linear_wait: int = 0
    exponential_wait: int = 0


@dataclass(frozen=True, slots=True)
class DecisionSpec:
    """Declaration for one awaited decision slot created while a step suspends."""

    assignees: tuple[str, ...]
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    requester: str = ""
    escalation: tuple[str, ...] = ()
    max_attempts: int | None = None
    expires_at: datetime | None = None
    escalate_at: datetime | None = None
    decision_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StepResult:
    """Result returned by a workflow step implementation.

    ``done(output, outcome)`` completes the step and routes by ``outcome``.
    ``wait(until=...)`` records a durable timer wake. External events use
    :func:`angee.workflows.engine.deliver`, which wakes the waiting journal row
    before normal engine advancement claims it. ``suspend()`` pauses the step
    until an external resolution writes the next journal facts.
    """

    kind: str
    output: Any = None
    outcome: str = ""
    until: datetime | None = None
    resume_state: dict[str, Any] | None = None
    decisions: tuple[DecisionSpec, ...] = ()

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
    ) -> Self:
        """Return a durable wait result."""

        if until is None:
            raise ValueError("StepResult.wait requires until.")
        return cls(kind="wait", until=until, resume_state=resume_state)

    @classmethod
    def suspend(
        cls,
        *,
        resume_state: dict[str, Any] | None = None,
        decisions: list[DecisionSpec] | tuple[DecisionSpec, ...] = (),
    ) -> Self:
        """Return a suspended step result."""

        return cls(kind="suspend", resume_state=resume_state, decisions=tuple(decisions))


class StepImpl(ImplBase):
    """Base class for registry-selected workflow step implementations."""

    deterministic: ClassVar[bool] = True
    decision_schema: ClassVar[type[Any] | None] = None

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate a step row's JSON config for this implementation."""

        if not isinstance(config, Mapping):
            raise ValidationError({"config": "Step config must be a JSON object."})

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
    deterministic = False


class WaitStep(StepImpl):
    """Built-in timer wait step."""

    key = "wait"
    label = "Wait"
    category = "Control"

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate timer wait configuration."""

        super().validate_config(config)
        if "until" not in config:
            raise ValidationError({"config": "Wait steps require an until timestamp."})
        if "until" in config and _config_until(config["until"]) is None:
            raise ValidationError({"config": "Wait until must be an ISO datetime."})

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

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate declarative gate slot configuration."""

        super().validate_config(config)
        policy = str(config.get("policy", "one_done") or "one_done")
        if policy not in GATE_POLICIES:
            raise ValidationError({"config": f"Gate policy must be one of {', '.join(sorted(GATE_POLICIES))}."})
        if not str(config.get("action", "") or ""):
            raise ValidationError({"config": "Gate steps require an action slug."})
        slots = config.get("slots")
        if slots is None:
            slots = [{"assignee": subject} for subject in config.get("assignees", ())]
        if not isinstance(slots, list) or not slots:
            raise ValidationError({"config": "Gate steps require at least one slot."})
        for index, slot in enumerate(slots):
            if not isinstance(slot, Mapping):
                raise ValidationError({"config": f"Gate slot {index + 1} must be an object."})
            assignees = _slot_assignees(slot)
            if not assignees:
                raise ValidationError({"config": f"Gate slot {index + 1} requires an assignee."})
            try:
                int(slot.get("priority", index))
            except (TypeError, ValueError) as error:
                raise ValidationError({"config": f"Gate slot {index + 1} priority must be an integer."}) from error
        max_attempts = config.get("max_attempts")
        if max_attempts not in (None, ""):
            try:
                parsed_max_attempts = int(max_attempts)
            except (TypeError, ValueError) as error:
                raise ValidationError({"config": "Gate max_attempts must be an integer."}) from error
            if parsed_max_attempts < 1:
                raise ValidationError({"config": "Gate max_attempts must be positive when set."})
        for key in ("expires_at", "escalate_at"):
            if config.get(key) not in (None, "") and _config_datetime(config.get(key)) is None:
                raise ValidationError({"config": f"Gate {key} must be an ISO datetime."})

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

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate the map declaration consumed by the engine."""

        super().validate_config(config)
        if not str(config.get("target_step", "") or ""):
            raise ValidationError({"config": "Map steps require target_step."})
        items = config.get("items")
        if not isinstance(items, (str, list)) or not items:
            raise ValidationError({"config": "Map steps require an items expression or literal list."})
        parsed = optional_number(config.get("min_success_ratio", config.get("min_success")), "Map min_success_ratio")
        if parsed is not None:
            if parsed < 0 or parsed > 1:
                raise ValidationError({"config": "Map min_success_ratio must be between 0 and 1."})

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
                escalation=tuple(str(subject) for subject in slot.get("escalation", escalation) if str(subject)),
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
