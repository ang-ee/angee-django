"""Record-sync state vocabulary shared by models and the stream driver.

Keeping these declarations independent lets models compose the driver without
cycling back through model discovery.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

if TYPE_CHECKING:
    from angee.integrate.mixins import SyncStateMixin
    from angee.integrate.models import RecordLink, SyncStream

UNSET = object()
"""Omitted record binding; ``None`` explicitly clears the target."""


class StreamKind(models.TextChoices, StrEnum):
    """Whether a stream carries append-only events or mutable replicas."""

    EVENT_FEED = "event_feed", "Event feed"
    RECORD_REPLICA = "record_replica", "Record replica"


class StreamDirection(models.TextChoices, StrEnum):
    """The sides a stream may write."""

    PULL = "pull", "Pull"
    PUSH = "push", "Push"
    BIDIRECTIONAL = "bidirectional", "Bidirectional"


class StreamPhase(models.TextChoices, StrEnum):
    """A new epoch verifies a baseline before accepting deltas."""

    BASELINE = "baseline", "Baseline"
    DELTA = "delta", "Delta"


class StreamState(models.TextChoices, StrEnum):
    """Reopening a closed generation starts a fresh baseline."""

    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class LinkStatus(models.TextChoices, StrEnum):
    """Observed identity and reconciliation state."""

    CURRENT = "current", "Current"
    OBSERVED = "observed", "Observed"
    UNAVAILABLE = "unavailable", "Unavailable"
    FAILED = "failed", "Failed"
    WITHDRAWN = "withdrawn", "Withdrawn"
    TOMBSTONE = "tombstone", "Tombstone"


class SyncFailureKind(models.TextChoices, StrEnum):
    """Recoverable record failures, independent of transport failures."""

    SEMANTIC = "semantic", "Semantic"
    CONFLICT = "conflict", "Conflict"
    MISSING_DEPENDENCY = "missing_dependency", "Missing dependency"
    REMOTE_REJECTED = "remote_rejected", "Remote rejected"


class OutboxState(models.TextChoices, StrEnum):
    """A link's outstanding write; null means there is no pending write."""

    PENDING = "pending", "Pending"
    REFUSED = "refused", "Refused"
    ERROR = "error", "Error"


class SyncFailureStatus(models.TextChoices, StrEnum):
    """Quarantine remains open until a successful rescan resolves it."""

    OPEN = "open", "Open"
    RETRY = "retry", "Retry"
    RESOLVED = "resolved", "Resolved"


class ConflictKeep(models.TextChoices, StrEnum):
    """The side retained by an explicit conflict resolution."""

    REMOTE = "remote", "Remote"
    LOCAL = "local", "Local"


class SyncFailure(BaseModel):
    """A state projection whose reads and transitions inherit its row's policy.

    IDs address an append-only inbound slot or the current link outbox. Closed
    inbound slots stay; replaced outbox evidence remains in native link history.
    """

    model_config = ConfigDict(frozen=True)
    id: str
    stream_id: str
    link_id: str | None = None
    kind: SyncFailureKind
    code: str
    source_hash: str = ""
    mapping_version: int = 1
    details: dict[str, Any] = Field(default_factory=dict)
    status: SyncFailureStatus = SyncFailureStatus.OPEN
    attempts: int = 0
    retry_at: datetime | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    _owner: SyncStateMixin = PrivateAttr()
    _slot: int | None = PrivateAttr()

    @classmethod
    def from_state(cls, owner: SyncStateMixin, slot: int | None, data: dict[str, Any]) -> SyncFailure:
        """Bind one typed view to the row that owns its state transitions."""
        stream = owner.failure_stream
        row = cls.model_validate(
            {
                **data,
                "id": f"{owner.public_id}:{'outbox' if slot is None else slot}",
                "stream_id": stream.public_id,
                "link_id": None if owner == stream else str(owner.public_id),
            }
        )
        row._owner, row._slot = owner, slot
        return row

    @property
    def is_open(self) -> bool:
        return self.status != SyncFailureStatus.RESOLVED

    @property
    def pk(self) -> str:
        """Identify the projected row in page results and failure lists."""
        return self.id

    @property
    def slot(self) -> int | None:
        return self._slot

    @property
    def stream(self) -> SyncStream:
        return self._owner.failure_stream

    @property
    def link(self) -> RecordLink | None:
        return cast("RecordLink", self._owner) if self.link_id is not None else None

    @property
    def link_pk(self) -> Any:
        return self.link.pk if self.link is not None else None

    def resolve(self) -> SyncFailure:
        return self._owner.resolve_failure(self._slot)

    def retry(self) -> SyncFailure:
        return self._owner.retry_failure(self._slot)

    def resolve_conflict(self, *, keep: ConflictKeep) -> SyncFailure:
        if self.link is None:
            raise ValidationError("Conflict resolution requires a linked conflict.")
        return self.link.resolve_conflict(self._slot, keep=keep)
