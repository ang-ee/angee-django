"""Admission and the single conditional verdict transition."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import BooleanField, Exists, ExpressionWrapper, F, OuterRef, Q
from django.db.models.functions import Now
from pydantic import JsonValue, TypeAdapter
from rebac import actor_context, current_actor, generic_target, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyQuerySet
from angee.base.models.base import AngeeModel
from angee.base.models.identity import public_id_of
from angee.base.permissions import actor_user_id, require_rebac_queryset
from angee.base.querysets import AngeeQuerySet
from angee.base.refs import generic_pointer_model
from angee.decisions.contracts import DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.decisions.signals import decision_answered
from angee.graphql.publishing import mute_changes, publish_change
from angee.provenance.evidence import readable_records

if TYPE_CHECKING:
    from angee.decisions.models import Decision


def _user(actor: Any, *, active: bool = True) -> Any:
    identity = actor_user_id(to_subject_ref(actor if actor is not None else current_actor()))
    if identity is None:
        raise PermissionDenied("An active user is required.")
    users = get_user_model().system_queryset().filter(pk=identity)
    user = (users.filter(is_active=True) if active else users).first()
    if user is None:
        raise PermissionDenied("An active user is required.")
    return user


class DecisionQuerySet[T: Decision](AppendOnlyQuerySet[T], AngeeQuerySet[T]):
    def open_expression(self) -> ExpressionWrapper:
        return ExpressionWrapper(Q(verdict__isnull=True), output_field=BooleanField())

    def open(self) -> Any:
        return self.filter(verdict__isnull=True)

    def waiting_on(self, actor: Any) -> Any:
        """Open assignments, or unassigned questions this actor may answer.

        Retain the caller's read scope. An unassigned question delegates to the
        composed ``act`` permission rather than inventing a default assignee.
        """

        identity = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        if identity is None:
            return self.none()
        actionable = self.model.objects.with_actor(actor).with_action("act").scoped().filter(pk=OuterRef("pk"))
        return self.open().filter(Q(assignees=identity) | (Q(assignees__isnull=True) & Q(Exists(actionable))))

    def open_for(self, *records: Any) -> Any:
        return self.open().for_records(*records)

    def for_records(self, *records: Any) -> Any:
        """Select decisions retaining any of the supplied canonical records."""

        link = apps.get_model("decisions", "DecisionRecord")
        concerns = Q(pk__in=[])
        for record in records:
            concerns |= link.record_filter(record)
        return self.filter(pk__in=link.system_queryset().filter(concerns).order_by().values("decision_id"))

    def attention_expression(self, queryset: Any) -> Exists:
        model = generic_pointer_model(queryset.model)
        # Attention belongs to the readable record, regardless of who can answer
        # or read the question. Only the boolean crosses that permission boundary.
        return Exists(
            self.model.system_queryset()
            .open()
            .filter(
                records__content_type__app_label=model._meta.app_label,
                records__content_type__model=model._meta.model_name,
                records__object_id=OuterRef("pk"),
            )
        )


_DecisionManagerBase = AngeeManager.from_queryset(DecisionQuerySet)


class DecisionManager[T: Decision](_DecisionManagerBase[T]):
    def withdraw(self, decision: Any, *, actor: Any) -> Any:
        """An authorized asking owner withdraws a question in a named system context."""
        if not is_sudo():
            raise PermissionDenied("Withdrawal requires the authorized asking owner's system context.")
        # Cleanup must still close a retired principal's retained questions.
        actor = actor if actor is not None else current_actor()
        withdrawing = _user(actor, active=False) if actor is not None else None
        pk = decision.pk if isinstance(decision, models.Model) else decision
        with transaction.atomic(), system_context(reason="decisions.withdraw"):
            row = self.filter(pk=pk).lock_if_supported().get()
            if row.is_open:
                self.filter(pk=pk).open().owner_update(
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

    def ask(self, request: DecisionRequest, *, actor: Any) -> Any:
        """Admit one question after checking its participants' standing read access."""
        request = DecisionRequest.model_validate(request)
        if actor is None and not is_sudo():
            raise PermissionDenied("Actorless admission requires a named system context.")
        supplied = (
            *request.assignees,
            *((actor,) if actor is not None else ()),
            *((request.requester,) if request.requester is not None else ()),
        )
        ids = [identity for person in supplied if (identity := actor_user_id(to_subject_ref(person))) is not None]
        if len(ids) != len(supplied):
            raise PermissionDenied("An active user is required.")
        users = get_user_model().system_queryset().filter(pk__in=ids, is_active=True).in_bulk()
        if any(identity not in users for identity in ids):
            raise PermissionDenied("An active user is required.")
        asking = users[actor_user_id(to_subject_ref(actor))] if actor is not None else None
        assignees = tuple(users[actor_user_id(to_subject_ref(person))] for person in request.assignees)
        requester = users[actor_user_id(to_subject_ref(request.requester))] if request.requester is not None else None
        related_records: list[Any] = []
        try:
            DecisionProposal.model_validate(
                request.proposal.model_dump(mode="json"),
                context={"records": request.records, "actor": actor, "related_records": related_records},
            )
        except ValueError as error:
            raise ValidationError({"proposal": str(error)}) from error
        participants = tuple(person for person in (asking, requester, *assignees) if person is not None)
        references = (
            *request.context.records(),
            *(
                DecisionRecordReference(model=record._meta.label, id=public_id_of(record))
                for record in (*request.records, *related_records)
            ),
        )
        readable_records(references, participants)
        targets = {generic_target(record) for record in request.records}
        with transaction.atomic(), system_context(reason="decisions.ask"):
            # Capture admission only after its immutable concern links exist.
            with mute_changes():
                decision = self.create(
                    kind=request.kind,
                    requester=requester,
                    proposal=request.proposal.model_dump(mode="json"),
                    context=request.context.model_dump(mode="json"),
                )
            decision.assignees.set(assignees)
            link = apps.get_model("decisions", "DecisionRecord")
            link.objects.bulk_create(
                [
                    link(decision=decision, **target.lookups(link, "record"))
                    for target in sorted(targets, key=lambda target: (target.content_type.pk, target.object_id))
                ]
            )
            if assignees and not any(decision.with_actor(person).has_access("act") for person in assignees):
                raise ValidationError({"assignees": "At least one assignee must be allowed to answer."})
            publish_change(decision, action="create", update_fields=None)
        return decision.with_actor(asking) if asking is not None else decision

    def decide(
        self,
        decision: Any,
        *,
        actor: Any,
        chosen: Sequence[str],
        revision: int | None = None,
        values: dict[str, dict[str, JsonValue]] | None = None,
    ) -> Any:
        """Record one final verdict. Proposal actions belong to the asker."""
        answering = _user(actor)
        pk = decision.pk if isinstance(decision, models.Model) else decision
        with transaction.atomic():
            with system_context(reason="decisions.decide"):
                row = self.filter(pk=pk).lock_if_supported().get()
                row.require_access("act", answering)
                if revision is not None:
                    row.require_revision(revision)
                if not row.is_open:
                    raise ValidationError({"revision": "The decision has changed; reload it."})
                try:
                    values = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(
                        {} if values is None else values
                    )
                    links = {link.record_public_id: link for link in row.records.with_actor(answering).all()}
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
                                require_rebac_queryset(actions.target_model(reference))
                                .with_actor(answering)
                                .for_write()
                                .get(
                                    pk=reference.pk,
                                )
                            )
                            assert isinstance(record, AngeeModel)
                            record.require_access("write", answering)
                            with actor_context(answering):
                                actions.resolve(record, context={"actor": answering}, values=values[identity])
                except (ValueError, ObjectDoesNotExist) as error:
                    raise ValidationError({"values": str(error)}) from error
                row.validate_verdict(selected, actor=answering)
                if (
                    not self.filter(pk=pk, revision=row.revision)
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


class DecisionRecordQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Concern links are authored once with their decision."""


DecisionRecordManager = AngeeManager.from_queryset(DecisionRecordQuerySet)
