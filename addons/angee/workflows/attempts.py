"""Typed retained-attempt values for workflow execution."""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

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
    model_validator,
)

from angee.base.serialization import canonical_json


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


def workflow_result_terminal_match_error(count: int) -> str:
    """Return the canonical diagnostic for a non-unique terminal result match."""

    return f"Workflow result contract matched {count} terminal producers; expected one."


class ExternalOperationPolicy(StrEnum):
    """Actual provider guarantees used for an admitted external request."""

    UNSUPPORTED = "unsupported"
    IDEMPOTENT_REQUEST = "idempotent_request"
    RECONCILE_HANDLE = "reconcile_handle"


@dataclass(frozen=True, slots=True)
class ExternalOperationRequest:
    """One retained provider request passed to an external step implementation."""

    request_key: str
    attempt_id: int
    input_present: bool
    input: Any
    recovery_source_attempt_id: int | None = None
    uncertainty_acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryCapability:
    """Operation-owned recovery admission for an exact retained attempt."""

    mode: RecoveryMode | None
    unavailable_reason: str = ""
    requires_uncertainty_ack: bool = False
    uncertainty_reason: str = ""

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

    return canonical_json(left) == canonical_json(right)


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


DecisionInputSource = Literal["attempt_input", "owned_call_input"]


class AdmittedInputPath(BaseModel):
    """One exact value carried by the current attempt or its owned-call input."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: DecisionInputSource
    path: tuple[StrictStr | StrictInt, ...]
    proposal_gate_path: tuple[StrictStr | StrictInt, ...] = ()

    @model_validator(mode="after")
    def complete_path(self) -> Self:
        """Require a concrete typed path and reject empty object-field names."""

        if not self.path or any(isinstance(part, str) and not part for part in self.path):
            raise ValueError("Admitted input authority requires a nonempty typed path.")
        if any(isinstance(part, str) and not part for part in self.proposal_gate_path):
            raise ValueError("Proposal gate authority path cannot contain empty fields.")
        return self


class DecisionRecordAccess(BaseModel):
    """One exact record opened only while its owning Decision is pending."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: StrictStr
    id: StrictStr
    authority_input: AdmittedInputPath | None = None


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
    target_model: StrictStr = ""
    target_id: StrictStr = ""
    target_tab: StrictStr = Field(default="", max_length=100)
    # Select an ID from this attempt's admitted input. A one-hop proposal must
    # also declare where its producer consumed the original settled gate.
    target_authority_path: tuple[StrictStr | StrictInt, ...] = ()
    target_authority_gate_path: tuple[StrictStr | StrictInt, ...] = ()
    record_access: tuple[DecisionRecordAccess, ...] = ()

    @model_validator(mode="after")
    def complete_target(self) -> Self:
        """Require the optional related-record identity as one complete pair."""

        if bool(self.target_model) != bool(self.target_id):
            raise ValueError("Decision target_model and target_id must be supplied together.")
        if self.target_tab and not self.target_model:
            raise ValueError("Decision target_tab requires a related-record target.")
        if self.target_authority_path and not self.target_model:
            raise ValueError("Decision target authority requires a related-record target.")
        if self.target_authority_gate_path and not self.target_authority_path:
            raise ValueError("Decision proposal gate path requires target authority input.")
        return self

    @field_validator("payload", "decision_schema")
    @classmethod
    def finite_json(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Reject non-finite numbers that database JSON cannot preserve."""

        json.dumps(value, allow_nan=False)
        return value


class DecisionResolution(BaseModel):
    """Immutable typed terminal evidence projected by one awaited Decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decision_id: StrictStr
    action: StrictStr
    verdict: StrictStr
    resolution: dict[StrictStr, JsonValue]
    resolved_by: StrictStr
    resolved_at: AwareDatetime
    declaration_index: StrictInt = Field(ge=0)


class DecisionGateOutput(BaseModel):
    """Ordered complete terminal resolutions and the native policy outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    resolutions: tuple[DecisionResolution, ...]
    outcome: StrictStr


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
