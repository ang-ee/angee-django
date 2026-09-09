"""Typed retained-attempt values for workflow execution."""

from __future__ import annotations

import copy
import json
import uuid
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


def map_child_input(value: JsonValue) -> JsonValue:
    """Project a raw Map item through the established Automatic input contract."""

    return copy.deepcopy(value) if isinstance(value, dict) else {"item": copy.deepcopy(value)}


class AttemptCause(StrEnum):
    """Reason a physical attempt exists for one logical step run."""

    INITIAL = "initial"
    CONTINUATION = "continuation"
    AUTOMATIC_RETRY = "automatic_retry"
    MANUAL_RETRY = "manual_retry"
    MAP_ENGINE = "map_engine"
    TEST_FIXTURE = "test_fixture"


class RecoveryMode(StrEnum):
    """How an implementation can safely recover one retained failure."""

    FRESH = "fresh"
    RECONCILE = "reconcile"


@dataclass(frozen=True, slots=True)
class RecoveryCapability:
    """Operation-owned recovery admission for an exact retained attempt."""

    mode: RecoveryMode | None
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.mode is not None


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    """Authorized summary of one exact retained recovery candidate."""

    attempt_id: str
    run_id: str
    workflow_id: str
    workflow_revision: int
    step_id: str
    step_key: str
    map_index: int | None
    capability: RecoveryCapability


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    """One explicit result artifact in declaration order."""

    target: Any
    label: str


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
    NOT_DUE = "not_due"
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


_STRICT_JSON: TypeAdapter[JsonValue] = TypeAdapter(
    JsonValue, config=ConfigDict(strict=True, allow_inf_nan=False)
)


def validate_json_presence(value: JsonPresence, *, label: str = "JSON value") -> JsonPresence:
    """Validate an exact absent/present JSON envelope without coercion."""

    if not isinstance(value, JsonPresence) or type(value.present) is not bool:
        raise ValueError(f"{label} must use JsonPresence with a boolean presence flag.")
    if not value.present:
        if value.value is not None:
            raise ValueError(f"Absent {label} cannot carry a value.")
        return value
    try:
        _STRICT_JSON.validate_python(value.value, strict=True)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Present {label} must contain an exact finite JSON value.") from error
    return value


def json_values_equal(left: Any, right: Any) -> bool:
    """Compare validated JSON values through their canonical native encoding."""

    return json.dumps(
        left, sort_keys=True, separators=(",", ":"), allow_nan=False
    ) == json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class AttemptInput(JsonPresence):
    """Resolved attempt input plus its durable source provenance."""

    provenance: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MapItemSource:
    """Exact retained Map expansion item captured by a child attempt."""

    expansion_attempt_id: int
    index: int
    value: JsonPresence


@dataclass(frozen=True, slots=True)
class MapExpansionPlan:
    """Definition-derived immutable facts for one retained Map expansion."""

    target_id: int | None
    target_key: str
    items: list[Any]
    error: str


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
    artifacts_present: bool = False
    artifacts: tuple[ArtifactSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class AttemptFinalization:
    """Outcome of recording and, when eligible, applying a physical result."""

    recorded: bool
    applied: bool
    timer_intents: tuple[DecisionTimerIntent, ...] = ()
    retry_intent: RetryIntent | None = None


@dataclass(frozen=True, slots=True)
class RetryIntent:
    """Dispatch identity for one durable automatic-retry successor."""

    attempt_id: int
    lease_token: uuid.UUID
    available_at: datetime


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
