"""Record-sync state vocabulary shared by models and the stream driver.

Keeping these declarations independent lets models compose the driver without
cycling back through model discovery.
"""

from enum import StrEnum

from django.db import models

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


class LinkStatus(models.TextChoices, StrEnum):
    """Observed identity and reconciliation state."""

    CURRENT = "current", "Current"
    OBSERVED = "observed", "Observed"
    UNAVAILABLE = "unavailable", "Unavailable"
    DISCREPANT = "discrepant", "Discrepant"
    WITHDRAWN = "withdrawn", "Withdrawn"
    TOMBSTONE = "tombstone", "Tombstone"


class DiscrepancyKind(models.TextChoices, StrEnum):
    """Recoverable record failures, independent of transport failures."""

    SEMANTIC = "semantic", "Semantic"
    CONFLICT = "conflict", "Conflict"
    MISSING_DEPENDENCY = "missing_dependency", "Missing dependency"
    REMOTE_REJECTED = "remote_rejected", "Remote rejected"


class DiscrepancyStatus(models.TextChoices, StrEnum):
    """Quarantine remains open until a successful rescan resolves it."""

    OPEN = "open", "Open"
    RETRY = "retry", "Retry"
    RESOLVED = "resolved", "Resolved"


class ConflictKeep(models.TextChoices, StrEnum):
    """The side retained by an explicit conflict resolution."""

    REMOTE = "remote", "Remote"
    LOCAL = "local", "Local"
