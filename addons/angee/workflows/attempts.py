"""Typed retained-attempt values for workflow execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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


@dataclass(frozen=True, slots=True)
class AttemptFinalization:
    """Outcome of recording and, when eligible, applying a physical result."""

    recorded: bool
    applied: bool


@dataclass(frozen=True, slots=True)
class LeaseRevocation:
    """Outcome of an idempotent lease-revocation request."""

    revoked: bool
    already_recorded: bool
