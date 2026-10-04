"""Typed workflow steps composed through the framework implementation registry."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from functools import cache
from types import get_original_bases
from typing import Any, ClassVar, Literal, TypeVar, cast, get_args, get_origin

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models.functions import Now
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, PydanticInvalidForJsonSchema

from angee.base.impl import ImplBase, resolve_impl_class
from angee.base.jsonschema import check_schema, validate, validator
from angee.base.serialization import strip_null_bytes
from angee.base.validation import get_type_adapter
from angee.decisions.contracts import DecisionRequest
from angee.jobs.timeouts import TASK_SETTLEMENT_RESERVE_SECONDS, task_time_budget
from angee.workflows.states import (
    DONE_OUTCOME,
    ERROR_OUTCOME,
    Outcome,
    StepRunStatus,
    WaitingKind,
)


class Retryable(Exception):
    """A transient failure eligible for the step's declared retry policy."""


class Superseded(Exception):
    """An attempt lost its fence; its settlement and further effects are forbidden."""


class StepMode(StrEnum):
    """Transaction boundary for a workflow step body."""

    DATABASE = "DATABASE"
    IO = "IO"


@dataclass(frozen=True)
class RetryPolicy:
    """Maximum attempts and exponential delay after a failed attempt."""

    max_attempts: int = 1
    backoff: timedelta = timedelta(seconds=1)

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.backoff < timedelta():
            raise ValueError("Retries require positive attempts and nonnegative backoff.")

    def delay_for(self, retries: int) -> timedelta:
        """Return the delay after the given positive failed-attempt count."""
        return self.backoff * 2 ** max(0, retries - 1)


class _Settlement:
    """Shared in-process protocol for public and planner-owned settlements."""

    def check(self, step: type[Step], *, config: Any = None) -> _Settlement:
        """Validate the body's values before any row transition."""
        return self

    def admit(self, ctx: Any) -> _Settlement:
        """Prepare checked settlement resources inside the body's transaction."""
        return self

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        """Apply this settlement through the queryset's named transition."""
        raise NotImplementedError

    @property
    def keeps_watches(self) -> bool:
        return False


@dataclass(frozen=True)
class Done(_Settlement):
    """A completed step carrying its raw output and named success outcome."""

    output: Any = field(default_factory=dict)
    outcome: str = DONE_OUTCOME

    def check(self, step: type[Step], *, config: Any = None) -> Done:
        outcome = step.parse_value(self.outcome, Outcome, "outcome")
        if (
            outcome == ERROR_OUTCOME
            and outcome not in step.outcomes_for(config)
            or outcome not in step.available_outcomes(config)
        ):
            raise ValidationError(f"Step {step.key!r} does not offer success outcome {outcome!r}.")
        if outcome in step.empty_outcomes:
            return Done(outcome=outcome)
        parsed = step.parse_value({} if self.output is None else self.output, step.output_model, "output")
        output = strip_null_bytes(get_type_adapter(step.output_model).dump_python(parsed, mode="json", by_alias=True))
        if step.output_model is Any:
            schema = step.output_schema(config=config, outcomes={outcome})
            if schema:
                validate(validator(schema), output)
        return Done(output=output, outcome=outcome)

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        return rows.update(
            **rows._cleared_wait(),
            status=StepRunStatus.SUCCEEDED,
            output=self.output,
            outcome=self.outcome,
            retries=0,
        )


@dataclass(frozen=True)
class Wait(_Settlement):
    """A record or time wait preserving the checkpoint and retry count."""

    until: datetime | None = None
    state: Any = field(default_factory=dict)
    waiting_kind: WaitingKind = cast(WaitingKind, WaitingKind.TIME)

    def __post_init__(self) -> None:
        if self.until is not None and isinstance(self.until, datetime) and timezone.is_naive(self.until):
            raise ValueError("A wait deadline requires an aware datetime.")

    def check(self, step: type[Step], *, config: Any = None) -> Wait:
        until = step.parse_value(self.until, datetime | None, "until")
        if until is not None and timezone.is_naive(until):
            raise ValidationError("A wait deadline requires an aware datetime.")
        waiting_kind = step.parse_value(self.waiting_kind, WaitingKind, "waiting_kind")
        if waiting_kind not in (WaitingKind.TIME, WaitingKind.RECORD):
            raise ValidationError("A consumer wait must be a time or record wait.")
        return replace(self, until=until, state=step.serialize_state(self.state), waiting_kind=waiting_kind)

    def admit(self, ctx: Any) -> Wait:
        """Let the watch owner choose the wait kind after body validation."""
        kind = apps.get_model("workflows", "StepWatch").objects.wait_kind(ctx.step_run, self.until)
        return replace(self, waiting_kind=kind)

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        return rows.to_waiting(kind=self.waiting_kind, until=self.until, state=self.state)

    @property
    def keeps_watches(self) -> bool:
        return self.waiting_kind == WaitingKind.RECORD


@dataclass(frozen=True)
class NextPage(_Settlement):
    """A completed page whose checkpoint continues in a new attempt."""

    state: Any = field(default_factory=dict)

    def check(self, step: type[Step], *, config: Any = None) -> NextPage:
        return replace(self, state=step.serialize_state(self.state))

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        return rows.to_ready(state=self.state, reset_retries=True, next_page=True)


@dataclass(frozen=True)
class Fail(_Settlement):
    """An unsuccessful attempt whose body writes must roll back."""

    error: str = ""

    def check(self, step: type[Step], *, config: Any = None) -> Fail:
        return replace(self, error=step.parse_value(self.error, str, "error"))

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        retries = step_run.retries + 1
        if attempt.retryable and step_run.requires_duplicate_acknowledgement:
            return rows.to_waiting(kind=WaitingKind.ERROR, reason="possible duplicate effect", retries=retries)
        if attempt.retryable and retries < step_run.step.retry.max_attempts:
            return rows.to_waiting(
                until=Now() + step_run.step.retry.delay_for(retries),
                state=step_run.state,
                retries=retries,
            )
        error_field = apps.get_model("workflows", "StepAttempt")._meta.get_field("error")
        return rows.update(
            **rows._cleared_wait(),
            status=StepRunStatus.FAILED,
            outcome=ERROR_OUTCOME,
            output={"error": error_field.get_prep_value(self.error)},
            retries=retries,
        )


@dataclass(frozen=True)
class Ask(_Settlement):
    """One question admitted with the step's continuation state."""

    request: DecisionRequest
    decision_id: Any = None
    state: Any = field(default_factory=dict)

    def check(self, step: type[Step], *, config: Any = None) -> Ask:
        request = DecisionRequest.model_validate(self.request)
        offered = step.available_outcomes(config)
        if any(
            alternative.outcome not in offered for alternative in request.proposal.alternatives
        ):
            raise ValidationError("A proposal names an outcome not declared by the asking step.")
        return replace(self, request=request, state=step.serialize_state(self.state))

    def admit(self, ctx: Any) -> Ask:
        manager = apps.get_model("decisions", "Decision").objects
        if ctx.step_run.decision_id is not None:
            raise ValidationError("This step has already asked its decision.")
        decision = manager.ask(self.request, actor=ctx.actor)
        for record in self.request.records:
            ctx.record(record)
        return replace(self, decision_id=decision.pk)

    def transition(self, rows: Any, step_run: Any, attempt: Any) -> int:
        return rows.to_waiting(kind=WaitingKind.DECISION, state=self.state, decision_id=self.decision_id)


type Settlement = Done | Wait | NextPage | Fail | Ask


class EmptyOutput(BaseModel):
    """The empty object persisted for a step's declared empty-output outcomes."""

    model_config = ConfigDict(extra="forbid")


class Step[I, O, C](ImplBase):
    """A step with explicit execution mode and typed input, output and config.

    ``None`` input/output parameters accept arbitrary JSON. Typed values share
    the base validation owner's cached adapters and ``ImplBase``'s config parsing
    and field-path errors. Step schema projections are cached by their declared type.
    """

    config_form_spec_json_fields = True
    registry_setting = "ANGEE_WORKFLOW_STEP_CLASSES"

    input_model: ClassVar[Any] = None
    output_model: ClassVar[Any] = None
    _model_parameters: ClassVar[tuple[str, ...]] = ("input_model", "output_model", "config_model")
    outcomes: ClassVar[dict[Outcome, str]] = {DONE_OUTCOME: "Done"}
    empty_outcomes: ClassVar[frozenset[str]] = frozenset({ERROR_OUTCOME})
    """Outcomes whose persisted output is the empty object, independent of O."""
    subject: ClassVar[str | None] = None
    mode: ClassVar[StepMode] = StepMode.DATABASE
    timeout: ClassVar[timedelta] = timedelta(seconds=30)
    """Per-statement DATABASE limit or whole-attempt IO deadline.

    Changing mode without declaring timeout selects that mode's default: 30
    seconds for DATABASE, five minutes for IO. Subclasses retain custom limits
    while inheriting the same mode. IO deadlines must remain strictly below the
    worker's soft and hard limits minus the jobs owner's settlement reserve.
    """
    retry: ClassVar[RetryPolicy] = RetryPolicy()
    effect_idempotent: ClassVar[bool] = False
    internal: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not isinstance(cls.mode, StepMode):
            raise ImproperlyConfigured("A step mode must be DATABASE or IO.")
        if "timeout" not in cls.__dict__ and cls.mode != getattr(super(cls, cls), "mode"):
            cls.timeout = timedelta(minutes=5) if cls.mode == StepMode.IO else Step.timeout
        cls.outcomes = cls.parse_value(cls.outcomes, dict[Outcome, str], "outcomes")
        for base in get_original_bases(cls):
            origin = get_origin(base)
            if (
                isinstance(origin, type)
                and issubclass(origin, Step)
                and not any(isinstance(arg, TypeVar) for arg in get_args(base))
            ):
                for name, model in zip(cls._model_parameters, get_args(base), strict=False):
                    if name in Step._model_parameters or name not in cls.__dict__:
                        setattr(cls, name, None if model is type(None) else model)
                if cls.config_model is not None and not issubclass(cls.config_model, BaseModel):
                    raise TypeError("A step config must be a Pydantic model or None.")

    @classmethod
    @cache
    def _schema(cls, model: Any, mode: Literal["validation", "serialization"]) -> dict[str, Any]:
        try:
            schema = get_type_adapter(model).json_schema(mode=mode)
        except (KeyError, PydanticInvalidForJsonSchema) as error:
            raise ValidationError(f"Cannot generate step schema: {error}") from error
        check_schema(schema)
        return schema

    @classmethod
    def input_schema(cls) -> dict[str, Any]:
        """Return the input model's cached validation schema."""
        return cls._schema(cls.input_model, "validation")

    @classmethod
    def output_schema(cls, *, config: Any = None, outcomes: set[str] | None = None) -> dict[str, Any]:
        """Return the output model's cached persisted-value schema."""
        return cls._schema(cls.output_model, "serialization")

    @classmethod
    def outcomes_for(cls, config: Any) -> dict[Outcome, str]:
        """Return this step's outcomes for its parsed config."""
        return cls.outcomes

    @classmethod
    def required_outcomes(cls, config: Any) -> set[Outcome]:
        """Outcomes the graph must route instead of silently ending the branch."""
        return set()

    @classmethod
    def available_outcomes(cls, config: Any, *, validate: bool = False) -> dict[Outcome, str]:
        """Compose failure routing, validating every hook result at publication."""
        outcomes = cls.outcomes_for(config)
        if validate:
            outcomes = cls.parse_value(outcomes, dict[Outcome, str], "outcomes")
        return cls._with_error_outcome(outcomes)

    @staticmethod
    def _with_error_outcome(outcomes: dict[Outcome, str]) -> dict[Outcome, str]:
        return {**outcomes, ERROR_OUTCOME: "Error"}

    @classmethod
    def parse_input(cls, value: Any) -> Any:
        """Parse admitted input using the inherited validation-error owner."""
        return cls.parse_value(value, cls.input_model, "input")

    @classmethod
    def normalize_input(cls, value: Any) -> Any:
        """Return JSON input after applying its model's defaults and validation."""
        return get_type_adapter(cls.input_model).dump_python(cls.parse_input(value), mode="json", by_alias=True)

    @staticmethod
    def done(output: Any = None, *, outcome: str = DONE_OUTCOME) -> Done:
        """Construct a completion without validating or serializing the body's values."""
        return Done(output=output, outcome=outcome)

    @classmethod
    def serialize_state(cls, state: Any) -> Any:
        """Serialize a continuation checkpoint once at the body boundary."""
        return strip_null_bytes(get_type_adapter(Any).dump_python({} if state is None else state, mode="json"))

    @classmethod
    def check(cls, settlement: _Settlement, *, config: Any = None) -> _Settlement:
        """Validate and serialize a body's settlement once, at the body boundary.

        Helpers construct plain values. The runner calls this inside the body's
        exception handler, and DATABASE mode's savepoint, so invalid returns fail
        the attempt and roll back DATABASE writes. IO writes are already committed.
        Pydantic receives the original values exactly once,
        preserving its validation aliases, validators and serialization behavior.
        """
        if not isinstance(settlement, _Settlement):
            raise ValidationError("A step must return a settlement.")
        return settlement.check(cls, config=config)

    def run(self, ctx: Any) -> _Settlement:
        """Execute under the context actor with the class's declared transaction boundary."""
        raise NotImplementedError


def resolve_step(key: str) -> type[Step[Any, Any, Any]]:
    """Resolve one trusted registry key without maintaining a second registry."""
    step = resolve_impl_class(Step, key)
    if step.subject is not None:
        try:
            step.subject = apps.get_model(step.subject)._meta.label
        except (LookupError, ValueError) as error:
            raise ImproperlyConfigured(f"Unknown step subject model {step.subject!r}.") from error
    if not timedelta(milliseconds=1) <= step.timeout <= timedelta(seconds=900):
        raise ImproperlyConfigured("A step timeout must be at least 1 millisecond and at most 900 seconds.")
    if step.mode == StepMode.IO and step.timeout >= task_time_budget():
        raise ImproperlyConfigured(
            f"An IO timeout must leave more than {TASK_SETTLEMENT_RESERVE_SECONDS} seconds "
            "below the worker's soft and hard time limits."
        )
    return step
