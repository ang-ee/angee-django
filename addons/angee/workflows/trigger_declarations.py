"""Typed declarations and scheduler projections for workflow triggers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self, cast

from croniter import CroniterBadCronError, croniter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from angee.base.impl import model_config_form_spec

TriggerKindName = Literal["manual", "event", "schedule"]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
MAX_PREVIEW_OCCURRENCES = 10


class TriggerConfig(BaseModel):
    """Common limits shared by trigger declarations."""

    model_config = ConfigDict(extra="allow")

    cooldown_seconds: NonNegativeInt | None = None
    hourly_cap: PositiveInt | None = None

    @field_validator("cooldown_seconds", "hourly_cap", mode="before")
    @classmethod
    def empty_limit_is_absent(cls, value: Any) -> Any:
        """Accept the empty spelling used by existing JSON rows and forms."""

        return None if value in (None, "") else value

    def summary(self) -> str:
        """Return this validated rule's readable summary."""

        raise NotImplementedError


class ManualTriggerConfig(TriggerConfig):
    """Manual starts have only the common rate-limit declaration."""

    def summary(self) -> str:
        return "Manual start"


class EventTriggerConfig(TriggerConfig):
    """A change-feed model and its native Django lookup condition."""

    model: str = Field(min_length=1, title="Model")
    condition: dict[str, Any] | None = Field(default_factory=dict, json_schema_extra={"widget": "json"})

    @model_validator(mode="before")
    @classmethod
    def accept_model_label_alias(cls, value: Any) -> Any:
        """Read the historical ``model_label`` key without losing other JSON."""

        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        model = normalized.get("model")
        alias = normalized.pop("model_label", None)
        normalized["model"] = model if isinstance(model, str) and model.strip() else alias
        return normalized

    @field_validator("model", mode="before")
    @classmethod
    def normalize_model_label(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    def summary(self) -> str:
        return f"When {self.model} changes"


class ScheduleTriggerConfig(TriggerConfig):
    """Exactly one cron or fixed-second schedule."""

    cron: str | None = Field(default=None, title="Cron expression")
    interval_seconds: PositiveInt | None = Field(default=None, title="Interval seconds")

    @field_validator("cron", mode="before")
    @classmethod
    def empty_cron_is_absent(cls, value: Any) -> Any:
        return None if value is None or (isinstance(value, str) and not value.strip()) else value

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        if value is None:
            return None
        expression = value.strip()
        try:
            croniter(expression)
        except CroniterBadCronError as error:
            raise ValueError("Schedule cron is invalid.") from error
        return expression

    @field_validator("interval_seconds", mode="before")
    @classmethod
    def empty_interval_is_absent(cls, value: Any) -> Any:
        return None if value in (None, "") else value

    @model_validator(mode="after")
    def require_one_schedule(self) -> Self:
        if (self.cron is None) == (self.interval_seconds is None):
            raise ValueError("Schedule triggers require cron or interval_seconds, but not both.")
        return self

    def next_fire_at(self, *, after: datetime, now: datetime) -> datetime:
        """Return the scheduler-owned next occurrence strictly after ``now``."""

        if self.interval_seconds is not None:
            interval = timedelta(seconds=self.interval_seconds)
            if after > now:
                return after + interval
            elapsed_intervals = (now - after) // interval
            return after + interval * (elapsed_intervals + 1)
        return cast(datetime, croniter(cast(str, self.cron), max(after, now)).get_next(datetime))

    def preview(self, *, now: datetime, count: int = 3) -> tuple[datetime, ...]:
        """Project a small bounded set of upcoming scheduler timestamps."""

        if not 0 <= count <= MAX_PREVIEW_OCCURRENCES:
            raise ValueError(f"Preview count must be between 0 and {MAX_PREVIEW_OCCURRENCES}.")
        occurrences: list[datetime] = []
        after = now
        for _ in range(count):
            after = self.next_fire_at(after=after, now=after)
            occurrences.append(after)
        return tuple(occurrences)

    def summary(self) -> str:
        if self.interval_seconds is not None:
            return f"Every {self.interval_seconds} seconds"
        return f"Cron {self.cron}"

    @property
    def cadence(self) -> tuple[str | None, int | None]:
        """Return the normalized fields that determine scheduler occurrences."""

        return self.cron, self.interval_seconds


_CONFIG_MODELS: dict[TriggerKindName, type[TriggerConfig]] = {
    "manual": ManualTriggerConfig,
    "event": EventTriggerConfig,
    "schedule": ScheduleTriggerConfig,
}


def trigger_kind_names() -> tuple[TriggerKindName, ...]:
    """Return declaration kinds from the registry that owns their configs."""

    return tuple(_CONFIG_MODELS)


def trigger_config_model(kind: TriggerKindName) -> type[TriggerConfig]:
    """Return the single native Pydantic owner for one trigger kind."""

    return _CONFIG_MODELS[kind]


def validate_trigger_config(kind: TriggerKindName, config: object) -> TriggerConfig:
    """Validate persisted JSON while retaining supported extension keys."""

    return trigger_config_model(kind).model_validate(config)


def trigger_config_schema(kind: TriggerKindName) -> dict[str, Any]:
    """Expose the shared mechanical FormSpec projection for this declaration."""

    model = trigger_config_model(kind)
    return model_config_form_spec(model, owner=model.__name__)


def trigger_summary(kind: TriggerKindName, config: object) -> str:
    """Return a readable summary from the validated declaration."""

    return validate_trigger_config(kind, config).summary()


def schedule_preview(
    config: object,
    *,
    now: datetime,
    count: int = 3,
) -> tuple[datetime, ...]:
    """Project upcoming scheduler timestamps from the validated declaration."""

    return ScheduleTriggerConfig.model_validate(config).preview(now=now, count=count)


@dataclass(frozen=True, slots=True)
class ScheduleDraftPreview:
    """Validated authoring preview data, including expected draft errors."""

    occurrences: tuple[datetime, ...] = ()
    errors: tuple[str, ...] = ()


def schedule_draft_preview(config: object, *, now: datetime, count: int = 3) -> ScheduleDraftPreview:
    """Validate and preview one authoring draft without transport errors."""

    try:
        declaration = ScheduleTriggerConfig.model_validate(config)
    except ValidationError as error:
        return ScheduleDraftPreview(errors=tuple(str(item["msg"]) for item in error.errors()))
    return ScheduleDraftPreview(occurrences=declaration.preview(now=now, count=count))


__all__ = [
    "EventTriggerConfig",
    "ManualTriggerConfig",
    "ScheduleTriggerConfig",
    "TriggerConfig",
    "TriggerKindName",
    "ScheduleDraftPreview",
    "schedule_draft_preview",
    "schedule_preview",
    "trigger_kind_names",
    "trigger_config_model",
    "trigger_config_schema",
    "trigger_summary",
    "validate_trigger_config",
]
