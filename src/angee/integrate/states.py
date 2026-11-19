"""Record-sync state vocabulary shared by models and the stream driver.

Keeping these declarations independent lets models compose the driver without
cycling back through model discovery.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from angee.base.models.identity import public_id_of

if TYPE_CHECKING:
    from angee.integrate.mixins import SyncStateMixin
    from angee.integrate.models import RecordLink, SyncStream

UNSET = object()
"""Omitted record binding; ``None`` explicitly clears the target."""

EXTERNAL_KEY_LENGTH = 512
"""The longest remote identity: a ``RecordLink.external_key``, and so a ``SyncStream.partition``.

A child stream's partition is its container's external key, one fact, so both
columns take this one length; the driver refuses a longer record identity.
"""

LOCAL_VERSION_LENGTH = 244
"""The longest local version a push adapter yields (``LocalChange.local_version``); the driver refuses a longer one.

A link holds it behind the mapping version that projected it
(``RecordLink.local_version``): a positive integer of at most ten digits and a
colon, within that column's 255 characters.
"""


class StreamKind(models.TextChoices):
    """Whether a stream carries append-only events or mutable replicas."""

    EVENT_FEED = "event_feed", "Event feed"
    RECORD_REPLICA = "record_replica", "Record replica"


class StreamDirection(models.TextChoices):
    """The sides a stream may write."""

    PULL = "pull", "Pull"
    PUSH = "push", "Push"
    BIDIRECTIONAL = "bidirectional", "Bidirectional"


class StreamPhase(models.TextChoices):
    """A new epoch verifies a baseline before accepting deltas."""

    BASELINE = "baseline", "Baseline"
    DELTA = "delta", "Delta"


class StreamState(models.TextChoices):
    """Reopening a closed generation starts a fresh baseline.

    A retired generation's container link stopped observing (tombstoned or
    withdrawn), so it is no longer drained; it reopens only once its container is
    live again (``SyncStreamManager.bump_generation``).
    """

    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    RETIRED = "retired", "Retired"


class LinkStatus(models.TextChoices):
    """Observed identity and reconciliation state."""

    CURRENT = "current", "Current"
    OBSERVED = "observed", "Observed"
    UNAVAILABLE = "unavailable", "Unavailable"
    FAILED = "failed", "Failed"
    WITHDRAWN = "withdrawn", "Withdrawn"
    TOMBSTONE = "tombstone", "Tombstone"


class SyncFailureKind(models.TextChoices):
    """Recoverable record failures, independent of transport failures."""

    SEMANTIC = "semantic", "Semantic"
    CONFLICT = "conflict", "Conflict"
    MISSING_DEPENDENCY = "missing_dependency", "Missing dependency"
    REMOTE_REJECTED = "remote_rejected", "Remote rejected"


class OutboxState(models.TextChoices):
    """A link's outstanding write; null means there is no pending write."""

    PENDING = "pending", "Pending"
    REFUSED = "refused", "Refused"
    ERROR = "error", "Error"


class SyncFailureStatus(models.TextChoices):
    """Quarantine remains open until a successful rescan resolves it."""

    OPEN = "open", "Open"
    RETRY = "retry", "Retry"
    RESOLVED = "resolved", "Resolved"


class ConflictKeep(models.TextChoices):
    """The side retained by an explicit conflict resolution."""

    REMOTE = "remote", "Remote"
    LOCAL = "local", "Local"


@dataclass(frozen=True, slots=True)
class MappingContribution:
    """One declarer's part of a composed stream mapping (``StreamDefinition.mapping_contributions``).

    Parts apply in ``sequence`` order, ties broken by ``key``. Integrate orders
    them and the stream retains only their keys and versions in that order, so
    the order never depends on how a declarer collected its parts, and
    renumbering that keeps the order changes nothing.
    """

    key: str
    version: int
    sequence: int = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class StreamDefinition:
    """The declaration-owned facts for one independently ordered partition."""

    key: str
    partition: str = ""
    kind: StreamKind = StreamKind.EVENT_FEED
    direction: StreamDirection = StreamDirection.PULL
    cursor: dict[str, Any] = field(default_factory=dict)
    reconcile_interval: timedelta | None = None
    absence_threshold: int = 2
    tombstone_retention: timedelta | None = None
    config: dict[str, Any] = field(default_factory=dict)
    snapshot: bool = False
    authoritative: bool = False
    mapping_version: int = 1
    """The version of a mapping one declarer owns; increase it to replay existing records.

    A lower version is a rollback that keeps progress, on a stream that never
    composed its mapping. A stream whose mapping is composed from several
    declarers declares ``mapping_contributions`` instead and leaves this at its
    default; any other version beside them is refused.
    """
    mapping_contributions: tuple[MappingContribution, ...] = field(default=(), metadata={"stream_field": False})
    """A mapping composed from several declarers, one :class:`MappingContribution` each.

    :meth:`mapping_parts` validates and orders them. The stream then owns the
    version (``SyncStreamManager.current``): any change
    of the ordered keys and versions replays every record once, under a version
    none of its records was applied with. The stream retains the ordered keys and
    versions, not this declaration.
    """
    holds_containers: bool = False
    """Whether this stream's records are containers of other partitions; a changed value updates the stream."""
    container: RecordLink | None = None
    """The parent stream's link whose record this partition belongs to.

    The partition is the container's external key. An unavailable container
    pauses this stream; one that stops observing retires it.
    """

    @classmethod
    def from_stream(cls, stream: SyncStream) -> StreamDefinition:
        """Read declaration facts from a persisted generation without another field inventory.

        A field marked ``stream_field=False`` is no stream column of the same
        shape: the stream manager writes what the stream keeps of it.
        """

        return cls(**{name: _independent(getattr(stream, name)) for name in _stream_facts(cls)})

    def stream_fields(self) -> dict[str, Any]:
        """Return independent values suitable for creating a stream generation."""

        return {name: _independent(getattr(self, name)) for name in _stream_facts(self)}

    def mapping_parts(self) -> tuple[tuple[str, int], ...]:
        """Validate the declared mapping; return its contributions as ``(key, version)`` parts in applied order.

        Integrate owns the order, by sequence then key, so a list collected in
        registration order never depends on load order, and renumbering that
        keeps the order is no change. A version is an integer of at least 1,
        never a boolean or a float. Beside contributions only the default plain
        version passes; an explicit 1 is indistinguishable from it.
        """

        def is_version(value: Any) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and value >= 1

        if not is_version(self.mapping_version):
            raise ValidationError("A stream mapping version must be a positive integer.")
        parts = self.mapping_contributions
        if not isinstance(parts, tuple | list) or not all(
            isinstance(part, MappingContribution)
            and isinstance(part.key, str)
            and part.key
            and is_version(part.version)
            and isinstance(part.sequence, int)
            for part in parts
        ):
            raise ValidationError(
                "Mapping contributions are MappingContribution parts with a nonempty key, a positive integer "
                "version and an integer sequence."
            )
        if len({part.key for part in parts}) != len(parts):
            raise ValidationError("Mapping contribution keys must be distinct.")
        if parts and self.mapping_version != 1:
            raise ValidationError("A stream declares a mapping version or mapping contributions, not both.")
        return tuple((part.key, part.version) for part in sorted(parts, key=lambda part: (part.sequence, part.key)))


def _stream_facts(definition: StreamDefinition | type[StreamDefinition]) -> tuple[str, ...]:
    """The declaration fields a stream generation stores under the same name and shape."""

    return tuple(item.name for item in fields(definition) if item.metadata.get("stream_field", True))


def _independent(value: Any) -> Any:
    """Copy a declaration value; a row such as the container keeps its identity."""

    return value if isinstance(value, models.Model) else deepcopy(value)


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
                "id": f"{public_id_of(owner)}:{'outbox' if slot is None else slot}",
                "stream_id": stream.public_id,
                "link_id": None if owner == stream else public_id_of(owner),
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

    @property
    def message(self) -> str:
        """The adapter's human explanation, with its stable code as a fallback."""

        message = self.details.get("message")
        return message if isinstance(message, str) and message else self.code

    @property
    def source_label(self) -> str:
        """Identify the failed remote record, or the whole stream for stream failures."""

        return self.link.source_label if self.link is not None else str(self.stream)

    @property
    def source_url(self) -> str | None:
        """Open the provider's own record when this failure belongs to a link."""

        return self.link.source_url if self.link is not None else None

    def resolve(self) -> SyncFailure:
        return self._owner.resolve_failure(self._slot)

    def retry(self) -> SyncFailure:
        return self._owner.retry_failure(self._slot)

    def resolve_conflict(self, *, keep: ConflictKeep) -> SyncFailure:
        if self.link is None:
            raise ValidationError("Conflict resolution requires a linked conflict.")
        return self.link.resolve_conflict(self._slot, keep)
