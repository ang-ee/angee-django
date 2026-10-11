"""Roster decisions and parties-specific statement row behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
        with transaction.atomic():
            self.confidence = 1.0
            self.is_confirmed, self.is_dismissed = True, False
            self.save(update_fields=("confidence", "is_confirmed", "is_dismissed", "updated_at"))

    def dismiss(self) -> None:
        with transaction.atomic():
            self.is_confirmed, self.is_dismissed = False, True
            self.save(update_fields=("is_confirmed", "is_dismissed", "updated_at"))


class PartyStatementMixin(Statement):
    """Handle review verbs on the retained assertion, admitted by its Party."""

    handle: Any

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
            evidence=[ref.model_dump(mode="json") for ref in self.readable_evidence(instance_actor(self))[0]],
        )

    def dismiss(self) -> Any:
        return self.subject.with_actor(instance_actor(self)).dismiss(
            aspect=self.aspect,
            handle=self.handle,
            evidence=[ref.model_dump(mode="json") for ref in self.readable_evidence(instance_actor(self))[0]],
        )

    def readable_evidence(self, actor: Any, *, limit: int = 20) -> tuple[list[EvidenceReference], bool]:
        """Bound inspection and output; each origin retains its own read gate."""
        self.subject.require_access("read", actor)
        bounded = max(1, min(int(limit), 20))
        visible = []
        for value in self.evidence[: bounded + 1]:
            ref = EvidenceReference.model_validate(value)
            model = apps.get_model(ref.model)
            if instance_from_public_id(model, ref.id, queryset=read_scoped_queryset(model, actor)) is not None:
                visible.append(ref)
        return visible[:bounded], len(self.evidence) > bounded
