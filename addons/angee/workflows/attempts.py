"""Typed retained-attempt values for workflow execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
)


class AttemptCause(StrEnum):
    """Reason a physical attempt exists for one logical step run."""

    INITIAL = "initial"
    CONTINUATION = "continuation"
    AUTOMATIC_RETRY = "automatic_retry"
    MANUAL_RETRY = "manual_retry"
    MAP_ENGINE = "map_engine"


class AttemptResultKind(StrEnum):
    """Closed result variants that the attempt owner can project."""

    DONE = "done"
    WAIT = "wait"
    SUSPEND = "suspend"
    ERROR = "error"
    NO_RESULT = "no_result"
    PREPARATION_ERROR = "preparation_error"
    TRANSIENT_ERROR = "transient_error"


class AttemptStatus(StrEnum):
    """Display status derived from independent lease and result facts."""

    ALLOCATED = "allocated"
    CLAIMED = "claimed"
    RUNNING = "running"
    REVOKED = "revoked"
    COMPLETED = "completed"
    LATE_RESULT = "late_result"


class LeaseRevocationReason(StrEnum):
    """Why an attempt lease stopped being eligible to mutate logical state."""

    CANCELED = "canceled"
    HEARTBEAT_LOST = "heartbeat_lost"
    SUPERSEDED = "superseded"


class InvocationAdmission(StrEnum):
    """Outcome of attempting to admit one physical delivery."""

    FIRST_START = "first_start"
    ALREADY_STARTED = "already_started"
    FENCED = "fenced"


class DecisionTimerKind(StrEnum):
    """Deferred timer action requested by an applicable suspension."""

    ESCALATE = "escalate"
    EXPIRE = "expire"


@dataclass(frozen=True, slots=True)
class JsonPresence:
    """A JSON value whose presence is distinct from a present null."""

    present: bool = False
    value: Any = None


@dataclass(frozen=True, slots=True)
class AttemptInput(JsonPresence):
    """Resolved attempt input plus its durable source provenance."""

    provenance: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AttemptClaim:
    """A logical claim and whether this call created its durable attempt."""

    attempt: Any
    newly_claimed: bool


class DecisionSpec(BaseModel):
    """Declaration for one awaited decision slot returned by an invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")

    assignees: tuple[StrictStr, ...]
    action: StrictStr
    payload: dict[StrictStr, JsonValue] = Field(default_factory=dict)
    priority: StrictInt = 0
    requester: StrictStr = ""
    escalation: tuple[StrictStr, ...] = ()
    max_attempts: StrictInt | None = Field(default=None, gt=0)
    expires_at: AwareDatetime | None = None
    escalate_at: AwareDatetime | None = None
    decision_schema: dict[StrictStr, JsonValue] = Field(default_factory=dict)

    @field_validator("payload", "decision_schema")
    @classmethod
    def finite_json(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Reject non-finite numbers that database JSON cannot preserve."""

        json.dumps(value, allow_nan=False)
        return value


@dataclass(frozen=True, slots=True)
class AttemptResult:
    """A retained physical result with a closed legacy projection."""

    kind: AttemptResultKind
    output_present: bool = False
    output: Any = None
    checkpoint_present: bool = False
    checkpoint: Any = None
    error: str | None = None
    stacktrace: str | None = None
    outcome: str = ""
    waiting_kind: str = ""
    requested_until: datetime | None = None
    decisions: tuple[DecisionSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class AttemptFinalization:
    """Outcome of recording and, when eligible, applying a physical result."""

    recorded: bool
    applied: bool
    timer_intents: tuple[DecisionTimerIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionTimerIntent:
    """Post-commit timer work emitted by atomic decision creation."""

    kind: DecisionTimerKind
    decision_id: int
    attempt: int
    when: datetime


_DECISION_SPECS = TypeAdapter(tuple[DecisionSpec, ...])


def serialize_decision_specs(specs: tuple[DecisionSpec, ...]) -> list[dict[str, Any]]:
    """Validate and encode decision declarations into reversible JSON values."""

    return _DECISION_SPECS.dump_python(_DECISION_SPECS.validate_python(specs), mode="json")


def deserialize_decision_specs(value: Any) -> tuple[DecisionSpec, ...]:
    """Decode retained decision declarations through their typed owner."""

    return _DECISION_SPECS.validate_json(json.dumps(value, allow_nan=False))


@dataclass(frozen=True, slots=True)
class LeaseRevocation:
    """Outcome of an idempotent lease-revocation request."""

    revoked: bool
    already_recorded: bool
