"""Persisted decision outcomes and closure reasons."""

from django.db import models


class Verdict(models.TextChoices):
    """An answer's classification, independent of unanswered closure."""

    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    ESCALATED = "escalated", "Escalated"


class ClosedReason(models.TextChoices):
    """Why a decision stopped accepting answers."""

    RESOLVED = "resolved", "Resolved"
    EXPIRED = "expired", "Expired"
    CANCELED = "canceled", "Canceled"
    SUPERSEDED = "superseded", "Superseded"
    SIBLING_SETTLED = "sibling_settled", "Sibling settled"
    INVALID_ATTEMPTS = "invalid_attempts", "Invalid attempts"

    @classmethod
    def unanswered_values(cls) -> tuple[str, ...]:
        """Return closures that settle a group without applying its answers."""
        return tuple(str(reason) for reason in (cls.EXPIRED, cls.CANCELED, cls.SUPERSEDED, cls.INVALID_ATTEMPTS))


OPEN_DECISION = models.Q(verdict=Verdict.PENDING, closed_reason__isnull=True)
