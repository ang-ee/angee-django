"""One question, its immutable proposal, and its final verdict."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import models, router, transaction
from django.db.models import F
from django.db.models.functions import Now
from django.utils.text import capfirst
from pydantic import JsonValue, TypeAdapter
from rebac import actor_context, current_actor, database_context, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.models.locks import OptimisticLockMixin
from angee.base.refs import RecordRef, record_view_refs
from angee.base.signals import publish_change
from angee.decisions.contracts import DecisionProposal
from angee.decisions.managers import DecisionManager, DecisionRecordManager
from angee.decisions.signals import decision_answered
from angee.iam.models.append_only import ProtectedAppendOnlyModel
from angee.iam.models.base import ProtectedDataModel, ProtectedModel
from angee.iam.models.derived import ProtectedDerivedFrom
from angee.iam.permissions import PermissionProof, actor_user_id, require_rebac_queryset


def _user(actor: Any, *, using: str, active: bool = True) -> Any:
    identity = actor_user_id(to_subject_ref(actor if actor is not None else current_actor()))
    if identity is None:
        raise PermissionDenied("An active user is required.")
    users = get_user_model().system_queryset().using(using).filter(pk=identity)
    user = (users.filter(is_active=True) if active else users).first()
    if user is None:
        raise PermissionDenied("An active user is required.")
    return user



class Decision(OptimisticLockMixin, ProtectedAppendOnlyModel, ProtectedDataModel, concrete=True):
    """An immutable question; answering records choices without applying them."""

    sqid_prefix = "dcn_"
    kind = models.CharField(max_length=200)
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    assignees = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+")
    proposal = models.JSONField()
    context = models.JSONField(default=dict)
    verdict = models.JSONField(null=True, blank=True)
    verdict_values = models.JSONField(null=True, blank=True)
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    answered_at = models.DateTimeField(null=True, blank=True)
    objects = DecisionManager()

    @classmethod
    def requester_querysets(cls) -> tuple[models.QuerySet, ...]:
        """Owner records annotated with ``_decision_id``, in contribution order."""
        return ()

    def requesting_records(self) -> Iterable[models.Model]:
        """Owner records retaining this question, contributed by asking capabilities."""
        for rows in type(self).requester_querysets():
            yield from rows.using(self._state.db).filter(_decision_id=self.pk).order_by("pk").distinct()

    def change_related_records(self) -> tuple[RecordRef, ...]:
        """Publish the question's retained concern identities with its verdict."""
        with system_context(reason="decisions.change_concerns"):
            links = (
                self.records.model.system_queryset().using(self._state.db)
                .filter(decision_id=self.pk).select_related("content_type")
            )
            return record_view_refs(*(link.record_ref for link in links), using=self._state.db)

    class Meta:
        abstract = True
        rebac_resource_type = "decisions/decision"
        constraints = [models.CheckConstraint(
            condition=models.Q(verdict__isnull=True, verdict_values__isnull=True,
                               answered_by__isnull=True, answered_at__isnull=True)
            | (models.Q(verdict__isnull=False, answered_at__isnull=False)
               & (models.Q(verdict=[]) | models.Q(answered_by__isnull=False))
               & (~models.Q(verdict=[]) | models.Q(verdict_values__isnull=True))),
            name="decisions_verdict_consistent",
        )]

    @property
    def is_open(self) -> bool:
        """No verdict means the question is still open."""
        return self.verdict is None

    @property
    def kind_label(self) -> str:
        return capfirst(self.kind.replace("_", " ").replace("-", " "))

    @property
    def verdict_label(self) -> str:
        """Read the chosen labels from this question's frozen alternatives."""
        if self.verdict is None:
            return ""
        if not self.verdict:
            return "Withdrawn"
        return "; ".join(alternative["label"] for alternative in self.proposal["alternatives"]
                         if alternative["key"] in self.verdict)

    def __str__(self) -> str:
        return self.kind_label

    def withdraw(self, *, actor: Any) -> Any:
        """An authorized asking owner withdraws a question in a named system context."""
        if not is_sudo():
            raise PermissionDenied("Withdrawal requires the authorized asking owner's system context.")
        # Cleanup must still close a retired principal's retained questions.
        actor = actor if actor is not None else current_actor()
        using = self._state.db or router.db_for_write(type(self), instance=self)
        withdrawing = _user(actor, using=using, active=False) if actor is not None else None
        rows = type(self).objects.db_manager(using)
        with database_context(using), transaction.atomic(using=using), system_context(reason="decisions.withdraw"):
            row = rows.filter(pk=self.pk).lock_if_supported().get()
            if row.is_open:
                rows.filter(pk=self.pk).open().owner_update(
                    verdict=[],
                    answered_by=withdrawing,
                    answered_at=Now(),
                    revision=F("revision") + 1,
                    updated_at=Now(),
                )
                row.refresh_from_db()
                publish_change(row, action="update", update_fields=None)
                decision_answered.send(sender=type(row), decision=row)
        return row.with_actor(withdrawing) if withdrawing is not None else row

    def decide(
        self,
        *,
        actor: Any,
        chosen: Sequence[str],
        revision: int | None = None,
        values: object = None,
        act_proof: PermissionProof | None = None,
    ) -> Any:
        """Record one final verdict; direct callers prove act unless given a matching guard proof.

        The guard checks before the row lock, so external grant changes can race it.
        Revision and open checks still run under the lock.
        """
        using = self._state.db or router.db_for_write(type(self), instance=self)
        answering = _user(actor, using=using)
        rows = type(self).objects.db_manager(using)
        with database_context(using), transaction.atomic(using=using):
            with system_context(reason="decisions.decide"):
                row = rows.filter(pk=self.pk).lock_if_supported().get()
                if act_proof is None:
                    row.require_access("act", answering)
                elif not act_proof.matches(self, answering, "act"):
                    raise PermissionDenied("The act proof does not match this decision and actor.")
                else:
                    row.with_actor(answering)
                if revision is not None:
                    row.require_revision(revision)
                if not row.is_open:
                    raise ValidationError({"revision": "The decision has changed; reload it."})
                try:
                    values = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(
                        {} if values is None else values
                    )
                    links = (
                        {link.record_public_id: link for link in row.records.with_actor(answering).all()}
                        if values
                        else {}
                    )
                    selected = DecisionProposal.model_validate(row.proposal).choose(
                        chosen,
                        values=values,
                        record_models={
                            identity: model
                            for identity, link in links.items()
                            if identity in values and (model := link.content_type.model_class()) is not None
                        },
                    )
                except ValueError as error:
                    raise ValidationError({"chosen": str(error)}) from error
                try:
                    for alternative in selected:
                        for identity, actions in alternative.actions.items():
                            if identity not in values:
                                continue
                            link = links.get(identity)
                            reference = link.record if link is not None else None
                            if reference is None:
                                raise ValueError("The record for a chosen action is missing or inaccessible.")
                            record = (
                                require_rebac_queryset(actions.target_model(reference)).using(using)
                                .with_actor(answering)
                                .for_write()
                                .get(
                                    pk=reference.pk,
                                )
                            )
                            assert isinstance(record, ProtectedModel)
                            record.require_access("write", answering)
                            with actor_context(answering):
                                actions.resolve(record, context={"actor": answering}, values=values[identity])
                except (ValueError, ObjectDoesNotExist) as error:
                    raise ValidationError({"values": str(error)}) from error
                row.validate_verdict(selected, actor=answering)
                if (
                    not rows.filter(pk=self.pk, revision=row.revision)
                    .open()
                    .owner_update(
                        verdict=[alternative.key for alternative in selected],
                        answered_by=answering,
                        answered_at=Now(),
                        verdict_values=values or None,
                        revision=F("revision") + 1,
                        updated_at=Now(),
                    )
                ):
                    raise ValidationError({"revision": "The decision has changed; reload it."})
                row.refresh_from_db()
                publish_change(row, action="update", update_fields=None)
            answered = row.with_actor(answering)
            with actor_context(answering):
                decision_answered.send(sender=type(row), decision=answered)
        return answered

    def validate_verdict(self, chosen: tuple[Any, ...], *, actor: Any) -> None:
        """Allow composed policy to refuse an answer before its verdict is retained."""


class DecisionRecord(ProtectedDerivedFrom, concrete=True):
    """A concern link: decision, canonical content type and object id, with no payload."""

    sqid_prefix = "dcr_"
    decision = models.ForeignKey("decisions.Decision", on_delete=models.CASCADE, related_name="records")
    objects = DecisionRecordManager()

    class Meta:
        abstract = True
        rebac_resource_type = "decisions/record"
        constraints = [
            models.UniqueConstraint(fields=("decision", "content_type", "object_id"), name="decisions_record_unique")
        ]
        indexes = [models.Index(fields=("content_type", "object_id"), name="decisions_record_target")]
