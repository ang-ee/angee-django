"""Question admission and the decision selections."""

from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q
from rebac import check_permission, generic_target, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyQuerySet
from angee.base.models.identity import public_id_of
from angee.base.permissions import actor_user_id
from angee.base.querysets import AngeeQuerySet
from angee.base.refs import generic_pointer_model
from angee.base.signals import mute_changes, publish_change
from angee.decisions.contracts import DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.provenance.evidence import readable_records

if TYPE_CHECKING:
    from angee.decisions.models import Decision


class DecisionQuerySet[T: Decision](AppendOnlyQuerySet[T], AngeeQuerySet[T]):
    def open_expression(self) -> ExpressionWrapper:
        return ExpressionWrapper(Q(verdict__isnull=True), output_field=BooleanField())

    def open(self) -> Any:
        return self.filter(verdict__isnull=True)

    def waiting_on(self, actor: Any) -> Any:
        """Open questions this actor may act on, assigned to them or unassigned."""

        identity = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        if identity is None:
            return self.none()
        return self.with_actor(actor).with_action("act").open().filter(
            Q(assignees=identity) | Q(assignees__isnull=True),
        )

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
            if assignees and not any(check_permission("act", decision, actor=person).allowed for person in assignees):
                raise ValidationError({"assignees": "At least one assignee must be allowed to answer."})
            publish_change(decision, action="create", update_fields=None)
        return decision.with_actor(asking) if asking is not None else decision


class DecisionRecordQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Concern links are authored once with their decision."""


DecisionRecordManager = AngeeManager.from_queryset(DecisionRecordQuerySet)
