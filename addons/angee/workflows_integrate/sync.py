"""Closed identity and capability contracts for Bridge-owned workflow syncs."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as datetime_timezone
from typing import Any, Iterator, Literal

from django.core.exceptions import ValidationError
from django.db import connections


@dataclass(frozen=True, slots=True)
class IntegrationSyncDefinition:
    """One server-owned exact shipped workflow binding."""

    key: str
    version_id: int
    digest: str

    def __post_init__(self) -> None:
        key = self.key.strip() if isinstance(self.key, str) else ""
        digest = self.digest.strip() if isinstance(self.digest, str) else ""
        if not key or len(key) > 100 or not isinstance(self.version_id, int) or self.version_id <= 0:
            raise ValueError("An integration sync definition requires a stable key and version id.")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("An integration sync definition requires a lowercase SHA-256 digest.")


@dataclass(frozen=True, slots=True)
class IntegrationSyncOccurrence:
    """Stable cadence or manual-request identity for one source window."""

    kind: Literal["scheduled", "manual"]
    key: str
    occurred_at: datetime
    window_key: str

    def canonical(self) -> dict[str, str]:
        """Return the bounded JSON identity shared by delivery retries."""

        if self.kind not in ("scheduled", "manual"):
            raise ValidationError({"occurrence": "Sync occurrence kind is invalid."})
        key = self.key.strip() if isinstance(self.key, str) else ""
        window_key = self.window_key.strip() if isinstance(self.window_key, str) else ""
        if not key or len(key) > 255 or not window_key or len(window_key) > 255:
            raise ValidationError({"occurrence": "Sync occurrence and window keys must be bounded and non-empty."})
        if not isinstance(self.occurred_at, datetime) or self.occurred_at.tzinfo is None:
            raise ValidationError({"occurrence": "Sync occurrence time must be timezone-aware."})
        occurred_at = self.occurred_at.astimezone(datetime_timezone.utc).isoformat()
        return {"kind": self.kind, "key": key, "occurred_at": occurred_at, "window_key": window_key}


@dataclass(frozen=True, slots=True)
class IntegrationSyncTerminalOutcome:
    """Bridge telemetry projected from one terminal workflow run."""

    items: int | None = None
    error: Exception | None = None
    retryable: bool = True

    def __post_init__(self) -> None:
        if (self.items is None) == (self.error is None):
            raise ValueError("A sync terminal outcome requires exactly one item count or error.")
        if self.items is not None and (
            not isinstance(self.items, int) or isinstance(self.items, bool) or self.items < 0
        ):
            raise ValueError("A sync terminal item count must be a non-negative integer.")


def workflow_run_execution_ref(run: Any) -> str:
    """Return the local opaque execution reference for one persisted Run."""

    if run.pk is None:
        raise ValueError("A workflow sync execution reference requires a persisted run.")
    return f"workflow-run:{int(run.pk)}"


def workflow_run_id_from_execution_ref(reference: str) -> int | None:
    """Parse only references owned by this composition addon."""

    prefix = "workflow-run:"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        return None
    value = reference.removeprefix(prefix)
    return int(value) if value.isdigit() and int(value) > 0 else None


def sync_cycle_dedup_key(bridge: Any, occurrence: IntegrationSyncOccurrence) -> str:
    """Hash only the Bridge and issued occurrence, never mutable pinned facts."""

    canonical = occurrence.canonical()
    payload = {
        "bridge": {"model": bridge._meta.label_lower, "id": int(bridge.pk)},
        "occurrence": {"kind": canonical["kind"], "key": canonical["key"]},
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return f"integration-sync:{digest}"


@dataclass(slots=True)
class _LaunchCapability:
    """Connection-local authority installed only by the Bridge dispatch owner."""

    alias: str
    connection_id: int
    atomic_id: int
    bridge_model: type[Any]
    bridge_id: int
    actor_id: int
    definition: IntegrationSyncDefinition
    dedup_key: str
    occurrence_id: str
    envelope: dict[str, Any]
    locked_bridge: Any = None
    disposition: str = ""
    used: bool = False

    def validate_context(self, *, alias: str) -> None:
        connection = connections[alias]
        if (
            self.used
            or alias != self.alias
            or id(connection) != self.connection_id
            or not connection.in_atomic_block
            or id(connection.atomic_blocks[0]) != self.atomic_id
        ):
            raise ValidationError("Integration sync launch capability is unavailable or already used.")
        self.used = True


_launch_capability: ContextVar[_LaunchCapability | None] = ContextVar(
    "workflows_integrate_launch_capability", default=None
)


@contextmanager
def integration_sync_launch_capability(capability: _LaunchCapability) -> Iterator[None]:
    """Install one exact, transaction-bound system launch capability."""

    token = _launch_capability.set(capability)
    try:
        yield
    finally:
        _launch_capability.reset(token)


def current_integration_sync_launch_capability() -> _LaunchCapability | None:
    """Return the private launch capability for the current execution context."""

    return _launch_capability.get()


__all__ = [
    "IntegrationSyncDefinition",
    "IntegrationSyncOccurrence",
    "IntegrationSyncTerminalOutcome",
    "sync_cycle_dedup_key",
    "workflow_run_execution_ref",
    "workflow_run_id_from_execution_ref",
]
