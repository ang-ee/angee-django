"""Admission and the single conditional verdict transition."""

from collections.abc import Sequence
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import BooleanField, Exists, ExpressionWrapper, F, OuterRef, Q
from django.db.models.functions import Now
from rebac import current_actor, system_context, to_subject_ref
from rebac.actors import is_sudo
from rebac.relation_loading import relation_actor

from angee.base.actors import actor_user_id
from angee.base.evidence import readable_records
from angee.base.identity import public_id_of
from angee.base.mixins import AppendOnlyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import canonical_record_model, canonical_record_target
from angee.base.scoping import lock_if_supported, system_queryset
from angee.decisions.contracts import DEFAULT_REQUESTER, DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.decisions.signals import decision_answered
from angee.graphql.publishing import publish_change


def _user(actor: Any) -> Any:
    identity = actor_user_id(to_subject_ref(actor if actor is not None else current_actor()))
    user = system_queryset(get_user_model()).filter(pk=identity, is_active=True).first()
    if user is None:
        raise PermissionDenied("An active user is required.")
    return user


class DecisionQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    def open_expression(self) -> ExpressionWrapper:
        return ExpressionWrapper(Q(verdict__isnull=True), output_field=BooleanField())

    def open(self) -> Any:
        return self.filter(verdict__isnull=True)

    def open_for(self, record: Any) -> Any:
        content_type, object_id = canonical_record_target(record)
        return self.open().filter(records__content_type=content_type, records__object_id=object_id)

    def attention_expression(self, queryset: Any) -> Exists:
        model = canonical_record_model(queryset.model)
        actor = relation_actor(queryset)
        decisions = self.model.objects.with_actor(actor) if actor is not None else self.model.objects.none()
        return Exists(decisions.open().filter(
            records__content_type__app_label=model._meta.app_label,
            records__content_type__model=model._meta.model_name,
            records__object_id=OuterRef("pk"),
        ))

    def records_with_open_decisions(self, queryset: Any) -> Any:
        return queryset.filter(self.attention_expression(queryset))


class DecisionManager(AngeeManager.from_queryset(DecisionQuerySet)):  # type: ignore[misc]
    def ask(self, request: DecisionRequest, *, actor: Any) -> Any:
        """Admit one question after checking its participants' standing read access."""
        request = DecisionRequest.model_validate(request)
        if actor is None and not is_sudo():
            raise PermissionDenied("Actorless admission requires a named system context.")
        asking = _user(actor) if actor is not None else None
        assignees = tuple(_user(person) for person in request.assignees)
        requester = asking if request.requester is DEFAULT_REQUESTER else (
            _user(request.requester) if request.requester is not None else None
        )
        participants = tuple(person for person in (asking, requester, *assignees) if person is not None)
        references = (*request.context.records(), *(
            DecisionRecordReference(model=record._meta.label, id=public_id_of(record)) for record in request.records
        ))
        readable_records(references, participants)
        targets = {canonical_record_target(record) for record in request.records}
        with transaction.atomic(), system_context(reason="decisions.ask"):
            decision = self.create(
                kind=request.kind, requester=requester,
                proposal=request.proposal.model_dump(mode="json"),
                context=request.context.model_dump(mode="json"),
            )
            decision.assignees.set(assignees)
            link = apps.get_model("decisions", "DecisionRecord")
            link.objects.bulk_create([
                link(decision=decision, content_type=ct, object_id=pk)
                for ct, pk in sorted(targets, key=lambda target: (target[0].pk, target[1]))
            ])
            if not any(decision.with_actor(person).has_access("act") for person in assignees):
                raise ValidationError({"assignees": "At least one assignee must be allowed to answer."})
        return decision.with_actor(asking) if asking is not None else decision

    def decide(
        self, decision: Any, *, actor: Any, chosen: Sequence[str], revision: int | None = None,
    ) -> Any:
        """Record one final verdict. Proposal actions belong to the asker."""
        answering = _user(actor)
        pk = decision.pk if isinstance(decision, models.Model) else decision
        with transaction.atomic(), system_context(reason="decisions.decide"):
            row = lock_if_supported(self.filter(pk=pk)).get()
            row.require_access("act", answering)
            if revision is not None:
                row.require_revision(revision)
            if not row.is_open:
                raise ValidationError({"revision": "The decision has changed; reload it."})
            try:
                selected = DecisionProposal.model_validate(row.proposal).choose(chosen)
            except ValueError as error:
                raise ValidationError({"chosen": str(error)}) from error
            if not self.filter(pk=pk, revision=row.revision).open().owner_update(
                verdict=[alternative.key for alternative in selected], answered_by=answering, answered_at=Now(),
                revision=F("revision") + 1, updated_at=Now(),
            ):
                raise ValidationError({"revision": "The decision has changed; reload it."})
            row.refresh_from_db()
            publish_change(row, action="update", update_fields=None)
            transaction.on_commit(lambda: decision_answered.send_robust(sender=type(row), decision=row))
        return row.with_actor(answering)


class DecisionRecordQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Concern links are authored once with their decision."""


DecisionRecordManager = AngeeManager.from_queryset(DecisionRecordQuerySet)
