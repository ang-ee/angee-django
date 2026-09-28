"""Typed DATABASE steps composed through the framework implementation registry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from functools import cache
from types import MethodType, get_original_bases
from typing import Annotated, Any, ClassVar, Literal, cast, get_args, get_origin

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone
from pydantic import BaseModel, Field, TypeAdapter

from angee.base.impl import ImplBase, resolve_impl_class
from angee.workflows.states import DONE_OUTCOME, ERROR_OUTCOME, Outcome


class Retryable(Exception):
    """A transient failure eligible for the step's declared retry policy."""


class Superseded(Exception):
    """A DATABASE settlement lost its fence and must roll back its domain writes."""


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

    kind: Literal["done", "wait", "fail"]
    output: Any = field(default_factory=dict)
    outcome: str = ""
    until: datetime | None = None
    state: Any = field(default_factory=dict)
    error: str = ""
    retryable: bool = False
    stacktrace: str = ""
    timed_out: bool = False

    def __post_init__(self) -> None:
        if self.kind == "wait" and (self.until is None or timezone.is_naive(self.until)):
            raise ValueError("A time wait requires an aware datetime.")


@dataclass(frozen=True)
class Done(Settlement):
    """A completed step carrying its raw output and named success outcome."""

    kind: Literal["done"] = field(default="done", init=False)


@dataclass(frozen=True)
class Wait(Settlement):
    """A time wait preserving the step run's checkpoint and retry count."""

    kind: Literal["wait"] = field(default="wait", init=False)


@dataclass(frozen=True)
class Fail(Settlement):
    """An unsuccessful attempt whose body writes must roll back."""

    kind: Literal["fail"] = field(default="fail", init=False)


type _WaitOrFail = Annotated[Wait | Fail, Field(discriminator="kind")]


class Step[I, O, C](ImplBase):
    """A database-only step with input, output and config model parameters.

    ``None`` input/output parameters accept arbitrary JSON. Typed values share
    ``ImplBase``'s field-path validation errors; adapters and schemas are cached
    by their declared type so consumers never reconstruct either contract.
    """

    input_model: ClassVar[Any] = None
    output_model: ClassVar[Any] = None
    outcomes: ClassVar[dict[Outcome, str]] = {DONE_OUTCOME: "Done"}
    subject: ClassVar[str | None] = None
    mode: ClassVar[str] = "DATABASE"
    timeout: ClassVar[timedelta] = timedelta(seconds=30)
    """Per-statement limit; the 1 ms floor prevents PostgreSQL disabling its limit."""
    retry: ClassVar[RetryPolicy] = RetryPolicy()
    internal: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.outcomes = cls.parse_value(
            cls.outcomes, parser=cls._adapter(dict[Outcome, str]).validate_python, path="outcomes"
        )
        for base in get_original_bases(cls):
            if get_origin(base) is Step:
                input_model, output_model, config_model = get_args(base)
                cls.input_model = None if input_model is type(None) else input_model
                cls.output_model = None if output_model is type(None) else output_model
                cls.config_model = None if config_model is type(None) else config_model
                if cls.config_model is not None and not issubclass(cls.config_model, BaseModel):
                    raise TypeError("A step config must be a Pydantic model or None.")

    @staticmethod
    @cache
    def _adapter(model: Any) -> TypeAdapter[Any]:
        return TypeAdapter(model or Any)

    @classmethod
    @cache
    def _schema(cls, model: Any, mode: Literal["validation", "serialization"]) -> dict[str, Any]:
        return cls._adapter(model).json_schema(mode=mode)

    @classmethod
    def input_schema(cls) -> dict[str, Any]:
        """Return the input model's cached validation schema."""
        return cls._schema(cls.input_model, "validation")

    @classmethod
    def output_schema(cls) -> dict[str, Any]:
        """Return the output model's cached persisted-value schema."""
        return cls._schema(cls.output_model, "serialization")

    @classmethod
    def outcomes_for(cls, config: Any) -> dict[Outcome, str]:
        """Return this step's outcomes for its parsed config."""
        return cls.outcomes

    @classmethod
    def available_outcomes(cls, config: Any, *, validate_dynamic: bool = False) -> dict[Outcome, str]:
        """Compose failure routing, validating overridden hooks at publication."""
        outcomes = cls.outcomes_for(config)
        if (
            validate_dynamic
            and cast(MethodType, cls.outcomes_for).__func__ is not cast(MethodType, Step.outcomes_for).__func__
        ):
            outcomes = cls.parse_value(
                outcomes, parser=cls._adapter(dict[Outcome, str]).validate_python, path="outcomes"
            )
        return {**outcomes, ERROR_OUTCOME: "Error"}

    @classmethod
    def config(cls, value: Any) -> Any:
        """Parse config once using the inherited validation-error owner."""
        if cls.config_model is None:
            if value:
                raise ValidationError({"config": f"Step {cls.key!r} does not accept configuration."})
            return None
        return cls.parse_value(value, parser=cls._adapter(cls.config_model).validate_python, path="config")

    @classmethod
    def parse_input(cls, value: Any) -> Any:
        """Parse admitted input using the inherited validation-error owner."""
        return cls.parse_value(value, parser=cls._adapter(cls.input_model).validate_python, path="input")

    @classmethod
    def normalize_input(cls, value: Any) -> Any:
        """Return JSON input after applying its model's defaults and validation."""
        return cls._adapter(cls.input_model).dump_python(cls.parse_input(value), mode="json", by_alias=True)

    @staticmethod
    def done(output: Any = None, *, outcome: str = DONE_OUTCOME) -> Done:
        """Construct a completion without validating or serializing the body's values."""
        return Done(output=output, outcome=outcome)

    @classmethod
    def check(cls, settlement: Settlement, *, config: Any = None) -> Settlement:
        """Validate and serialize a body's settlement once, at the body boundary.

        Helpers construct plain values. The runner calls this inside the body's
        savepoint and exception handler, so invalid returns fail the attempt and
        roll back its writes. Pydantic receives the original values exactly once,
        preserving its validation aliases, validators and serialization behavior.
        """
        if isinstance(settlement, Done):
            outcome = cls.parse_value(settlement.outcome, parser=cls._adapter(Outcome).validate_python, path="outcome")
            if outcome == ERROR_OUTCOME or outcome not in cls.available_outcomes(config):
                raise ValidationError(f"Step {cls.key!r} does not offer success outcome {outcome!r}.")
            adapter = cls._adapter(cls.output_model)
            parsed = cls.parse_value(
                {} if settlement.output is None else settlement.output, parser=adapter.validate_python, path="output"
            )
            return Done(output=adapter.dump_python(parsed, mode="json", by_alias=True), outcome=outcome)
        if not isinstance(settlement, (Wait, Fail)):
            raise ValidationError("A step must return Done, Wait or Fail.")
        checked = cls.parse_value(
            asdict(settlement),
            parser=cls._adapter(_WaitOrFail).validate_python,
            path="settlement",
        )
        if isinstance(checked, Wait):
            state = {} if checked.state is None else checked.state
            checked = replace(checked, state=cls._adapter(Any).dump_python(state, mode="json"))
        return checked

    def run(self, ctx: Any) -> Settlement:
        """Execute domain work under the context actor and the run transaction."""
        raise NotImplementedError


def resolve_step(key: str) -> type[Step[Any, Any, Any]]:
    """Resolve one trusted registry key without maintaining a second registry."""
    step = resolve_impl_class("ANGEE_WORKFLOW_STEP_CLASSES", key, Step)
    if step.key != key:
        raise ImproperlyConfigured(f"Step registry key {key!r} disagrees with {step.key!r}.")
    if step.mode != "DATABASE":
        raise ImproperlyConfigured("Only DATABASE steps are supported.")
    if step.subject is not None:
        try:
            step.subject = apps.get_model(step.subject)._meta.label_lower
        except (LookupError, ValueError) as error:
            raise ImproperlyConfigured(f"Unknown step subject model {step.subject!r}.") from error
    if not timedelta(milliseconds=1) <= step.timeout <= timedelta(seconds=900):
        raise ImproperlyConfigured("A step timeout must be at least 1 millisecond and at most 900 seconds.")
    return step
