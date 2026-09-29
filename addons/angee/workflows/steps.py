"""Typed workflow steps composed through the framework implementation registry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from functools import cache
from types import get_original_bases
from typing import Annotated, Any, ClassVar, Literal, TypeVar, get_args, get_origin

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, PydanticInvalidForJsonSchema

from angee.base.impl import ImplBase, resolve_impl_class
from angee.base.jsonschema import check_schema
from angee.base.serialization import strip_null_bytes
from angee.workflows.states import DONE_OUTCOME, ERROR_OUTCOME, Outcome

IO_SETTLE_WINDOW = timedelta(seconds=30)
"""Time reserved below worker limits for an IO attempt's fenced settlement."""


def io_timeout_budget() -> timedelta:
    """Bound IO leases by the worker lifetime, retaining the settlement reserve."""
    worker_limit = timedelta(seconds=min(settings.CELERY_TASK_SOFT_TIME_LIMIT, settings.CELERY_TASK_TIME_LIMIT))
    return worker_limit - IO_SETTLE_WINDOW


class Retryable(Exception):
    """A transient failure eligible for the step's declared retry policy."""


class Superseded(Exception):
    """An attempt lost its fence; its settlement and further effects are forbidden."""


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


@dataclass(frozen=True)
class Settlement:
    """A body's completed, waiting or failed attempt, checked before persistence."""

    kind: Literal["done", "wait", "next_page", "fail", "ask", "map", "run"]
    output: Any = field(default_factory=dict)
    outcome: str = ""
    until: datetime | None = None
    state: Any = field(default_factory=dict)
    error: str = ""
    retryable: bool = False
    stacktrace: str = ""
    timed_out: bool = False

    def __post_init__(self) -> None:
        if self.kind == "wait" and self.until is not None and timezone.is_naive(self.until):
            raise ValueError("A wait deadline requires an aware datetime.")

    def admit(self, ctx: Any) -> Settlement:
        """Prepare checked settlement resources inside the body's transaction."""
        return self

    def wait_parameters(self) -> dict[str, Any] | None:
        """Return owner transition arguments when this settlement parks the row."""
        return None


@dataclass(frozen=True)
class Done(Settlement):
    """A completed step carrying its raw output and named success outcome."""

    kind: Literal["done"] = field(default="done", init=False)


@dataclass(frozen=True)
class Wait(Settlement):
    """A record or time wait preserving the checkpoint and retry count."""

    kind: Literal["wait"] = field(default="wait", init=False)
    waiting_kind: Literal["time", "record"] = "time"

    def admit(self, ctx: Any) -> Wait:
        """Let the watch owner choose the wait kind after body validation."""
        kind = apps.get_model("workflows", "StepWatch").objects.wait_kind(ctx.step_run, self.until)
        return replace(self, waiting_kind=kind)

    def wait_parameters(self) -> dict[str, Any]:
        """Use the shared wake transition with the body's checkpoint."""
        return {"kind": self.waiting_kind, "until": self.until, "state": self.state}


@dataclass(frozen=True)
class NextPage(Settlement):
    """A completed page whose checkpoint continues in a new attempt."""

    kind: Literal["next_page"] = field(default="next_page", init=False)


@dataclass(frozen=True)
class Fail(Settlement):
    """An unsuccessful attempt whose body writes must roll back."""

    kind: Literal["fail"] = field(default="fail", init=False)


type _ContinuationOrFailure = Annotated[Wait | NextPage | Fail, Field(discriminator="kind")]


class EmptyOutput(BaseModel):
    """The empty object persisted for a step's declared empty-output outcomes."""

    model_config = ConfigDict(extra="forbid")


class Step[I, O, C](ImplBase):
    """A step with explicit execution mode and typed input, output and config.

    ``None`` input/output parameters accept arbitrary JSON. Typed values share
    ``ImplBase``'s cached adapters, config parsing and field-path validation
    errors. Step schema projections are cached by their declared type.
    """

    input_model: ClassVar[Any] = None
    output_model: ClassVar[Any] = None
    _model_parameters: ClassVar[tuple[str, ...]] = ("input_model", "output_model", "config_model")
    outcomes: ClassVar[dict[Outcome, str]] = {DONE_OUTCOME: "Done"}
    empty_outcomes: ClassVar[frozenset[str]] = frozenset({ERROR_OUTCOME})
    """Outcomes whose persisted output is the empty object, independent of O."""
    subject: ClassVar[str | None] = None
    mode: ClassVar[str] = "DATABASE"
    timeout: ClassVar[timedelta] = timedelta(seconds=30)
    """Per-statement DATABASE limit or whole-attempt IO deadline.

    Changing mode without declaring timeout selects that mode's default: 30
    seconds for DATABASE, five minutes for IO. Subclasses retain custom limits
    while inheriting the same mode. IO deadlines must remain strictly below the
    worker's soft and hard limits minus the 30-second settlement reserve.
    """
    retry: ClassVar[RetryPolicy] = RetryPolicy()
    effect_idempotent: ClassVar[bool] = False
    internal: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "timeout" not in cls.__dict__ and cls.mode != getattr(super(cls, cls), "mode"):
            cls.timeout = timedelta(minutes=5) if cls.mode == "IO" else Step.timeout
        cls.outcomes = cls.parse_value(cls.outcomes, dict[Outcome, str], "outcomes")
        for base in get_original_bases(cls):
            origin = get_origin(base)
            if isinstance(origin, type) and issubclass(origin, Step) and not any(
                isinstance(arg, TypeVar) for arg in get_args(base)
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
            schema = cls._adapter(model).json_schema(mode=mode)
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
        return {**outcomes, ERROR_OUTCOME: "Error"}

    @classmethod
    def parse_input(cls, value: Any) -> Any:
        """Parse admitted input using the inherited validation-error owner."""
        return cls.parse_value(value, cls.input_model, "input")

    @classmethod
    def normalize_input(cls, value: Any) -> Any:
        """Return JSON input after applying its model's defaults and validation."""
        return cls._adapter(cls.input_model).dump_python(cls.parse_input(value), mode="json", by_alias=True)

    @staticmethod
    def done(output: Any = None, *, outcome: str = DONE_OUTCOME) -> Done:
        """Construct a completion without validating or serializing the body's values."""
        return Done(output=output, outcome=outcome)

    @classmethod
    def serialize_state(cls, state: Any) -> Any:
        """Serialize a continuation checkpoint once at the body boundary."""
        return strip_null_bytes(cls._adapter(Any).dump_python({} if state is None else state, mode="json"))

    @classmethod
    def check(cls, settlement: Settlement, *, config: Any = None) -> Settlement:
        """Validate and serialize a body's settlement once, at the body boundary.

        Helpers construct plain values. The runner calls this inside the body's
        exception handler, and DATABASE mode's savepoint, so invalid returns fail
        the attempt and roll back DATABASE writes. IO writes are already committed.
        Pydantic receives the original values exactly once,
        preserving its validation aliases, validators and serialization behavior.
        """
        if isinstance(settlement, Done):
            outcome = cls.parse_value(settlement.outcome, Outcome, "outcome")
            if outcome == ERROR_OUTCOME or outcome not in cls.available_outcomes(config):
                raise ValidationError(f"Step {cls.key!r} does not offer success outcome {outcome!r}.")
            adapter = cls._adapter(cls.output_model)
            parsed = cls.parse_value(
                {} if settlement.output is None else settlement.output, cls.output_model, "output"
            )
            return Done(
                output=strip_null_bytes(adapter.dump_python(parsed, mode="json", by_alias=True)), outcome=outcome,
            )
        if not isinstance(settlement, (Wait, NextPage, Fail)):
            raise ValidationError("A step must return Done, Wait, NextPage or Fail.")
        checked = cls.parse_value(asdict(settlement), _ContinuationOrFailure, "settlement")
        if isinstance(checked, (Wait, NextPage)):
            checked = replace(checked, state=cls.serialize_state(checked.state))
        return checked

    def run(self, ctx: Any) -> Settlement:
        """Execute under the context actor with the class's declared transaction boundary."""
        raise NotImplementedError


def resolve_step(key: str) -> type[Step[Any, Any, Any]]:
    """Resolve one trusted registry key without maintaining a second registry."""
    step = resolve_impl_class("ANGEE_WORKFLOW_STEP_CLASSES", key, Step)
    if step.key != key:
        raise ImproperlyConfigured(f"Step registry key {key!r} disagrees with {step.key!r}.")
    if step.mode not in {"DATABASE", "IO"}:
        raise ImproperlyConfigured("A step mode must be DATABASE or IO.")
    if step.subject is not None:
        try:
            step.subject = apps.get_model(step.subject)._meta.label_lower
        except (LookupError, ValueError) as error:
            raise ImproperlyConfigured(f"Unknown step subject model {step.subject!r}.") from error
    if not timedelta(milliseconds=1) <= step.timeout <= timedelta(seconds=900):
        raise ImproperlyConfigured("A step timeout must be at least 1 millisecond and at most 900 seconds.")
    if step.mode == "IO" and step.timeout >= io_timeout_budget():
        raise ImproperlyConfigured(
            "An IO timeout must leave more than 30 seconds below the worker's soft and hard time limits."
        )
    return step
