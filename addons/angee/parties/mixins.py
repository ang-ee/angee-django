"""Roster decisions and parties-specific statement row behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django.apps import apps
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from angee.base.actors import instance_actor
from angee.base.identity import EvidenceReference, instance_from_public_id
from angee.base.mixins import Statement, StatementsMixin
from angee.base.scoping import read_scoped_queryset


class PartyAspectMixin(StatementsMixin):
    """Lock a Party's identity owner before its dependent aspect subject."""

    party: Any

    class Meta:
        abstract = True

    def lock_statement_subject(self, members: Mapping[str, Any], *, using: str) -> Any:
        for manager in self.party.statement_managers():
            manager.db_manager(using).lock_subject(manager.instance, {})
        return super().lock_statement_subject(members, using=using)


class MembershipDecisionMixin(models.Model):
    """Plain roster decisions, independent of assertion provenance."""

    confidence = models.FloatField(default=1.0, validators=(MinValueValidator(0), MaxValueValidator(1)))
    is_confirmed = models.BooleanField(default=False)
    is_dismissed = models.BooleanField(default=False)

    class Meta:
        abstract = True

    def confirm(self) -> None:
        """Accept this membership, then re-resolve any derived owner.

        Confirmation is the person's decision and writes only the review flags; the
        producer's facts (its confidence) stay as asserted — a confirmed row already
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
    """Handle review verbs on the retained assertion, admitted by its Party."""

    handle: Any
    EVIDENCE_LIMIT: ClassVar[int] = 20

    class Meta:
        abstract = True

    @property
    def is_confirmed(self) -> bool:
        return self.rank == self.StatementRank.PREFERRED and self.snak == self.StatementSnak.VALUE

    @property
    def is_dismissed(self) -> bool:
        return self.rank == self.StatementRank.PREFERRED and self.snak == self.StatementSnak.NO_VALUE

    def confirm(self) -> Any:
        return self.subject.with_actor(instance_actor(self)).confirm(
            aspect=self.aspect,
            handle=self.handle,
            values={},
        )

    def dismiss(self) -> Any:
        return self.subject.with_actor(instance_actor(self)).dismiss(
            aspect=self.aspect,
            handle=self.handle,
        )

    @property
    def evidence_truncated(self) -> bool:
        return len(self.evidence) > self.EVIDENCE_LIMIT

    def readable_evidence(self, actor: Any) -> list[EvidenceReference]:
        """Bound inspection and output; each origin retains its own read gate."""
        self.subject.require_access("read", actor)
        visible = []
        for value in self.evidence[: self.EVIDENCE_LIMIT]:
            ref = EvidenceReference.model_validate(value)
            model = apps.get_model(ref.model)
            if instance_from_public_id(model, ref.id, queryset=read_scoped_queryset(model, actor)) is not None:
                visible.append(ref)
        return visible
