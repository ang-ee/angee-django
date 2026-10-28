"""Roster decisions and parties-specific statement row behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from angee.base.models import (
    TimestampMixin,
)
from angee.provenance.statements import (
    Statement,
    StatementsMixin,
)

if TYPE_CHECKING:
    from angee.parties.models import Party


class PartyAspectMixin(StatementsMixin):
    """Lock a Party's identity owner before its dependent aspect subject."""

    party: Any

    class Meta:
        abstract = True

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Any:
        """Lock the owning Party before this dependent aspect subject."""

        self.party.lock_subject()
        return super().lock_subject(members)


class MembershipDecisionMixin(TimestampMixin):
    """Plain roster decisions, independent of assertion provenance."""

    confidence = models.FloatField(default=1.0, validators=(MinValueValidator(0), MaxValueValidator(1)))
    is_confirmed = models.BooleanField(default=False)
    is_dismissed = models.BooleanField(default=False)

    class Meta:
        abstract = True

    def confirm(self) -> None:
        """Accept this membership, then re-resolve any derived owner.

        Confirmation is the person's decision and writes only the review flags; the
        source's facts (its confidence) stay as asserted — a confirmed row already
        outranks any score.
        """

        with transaction.atomic():
            self.is_confirmed, self.is_dismissed = True, False
            self.save(update_fields=("is_confirmed", "is_dismissed", "updated_at"))

    def dismiss(self) -> None:
        with transaction.atomic():
            self.is_confirmed, self.is_dismissed = False, True
            self.save(update_fields=("is_confirmed", "is_dismissed", "updated_at"))


class PartyStatementMixin(Statement):
    """Party stances break equal-trust ties toward the party with a human-given name."""

    statement_subject_model: type[Party]

    class Meta:
        abstract = True

    def tie_break(self) -> tuple[Any, ...]:
        return (not self.subject.has_real_name,)
