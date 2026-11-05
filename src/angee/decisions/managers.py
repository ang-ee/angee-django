"""Admission of immutable decision proposals and concerns."""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import router, transaction
from rebac import database_context, generic_target, is_sudo, system_context, to_subject_ref

from angee.base.models.identity import public_id_of
from angee.base.signals import mute_changes, publish_change
from angee.decisions.contracts import DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.decisions.querysets import DecisionQuerySet, DecisionRecordQuerySet
from angee.iam.managers import ProtectedManager
from angee.iam.permissions import actor_user_id
from angee.provenance.evidence import readable_records

if TYPE_CHECKING:
    from angee.decisions.models import Decision


_DecisionManagerBase = ProtectedManager.from_queryset(DecisionQuerySet)


class DecisionManager[T: Decision](_DecisionManagerBase[T]):
    def ask(self, request: DecisionRequest, *, actor: Any) -> Any:
        """Admit one question after checking its participants' standing read access."""
        request = DecisionRequest.model_validate(request)
        using = self._db or request.records[0]._state.db or router.db_for_write(self.model)
        rows = self.db_manager(using)
        if any(record._state.db != using for record in request.records):
            raise ValidationError({"records": "A decision and its concerns must use the same database."})
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
        users = get_user_model().system_queryset().using(using).filter(pk__in=ids, is_active=True).in_bulk()
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
        readable_records(references, participants, using=using)
        targets = {generic_target(record, using=using) for record in request.records}
        with database_context(using), transaction.atomic(using=using), system_context(reason="decisions.ask"):
            # Capture admission only after its immutable concern links exist.
            with mute_changes():
                decision = rows.create(
                    kind=request.kind,
                    requester=requester,
                    proposal=request.proposal.model_dump(mode="json"),
                    context=request.context.model_dump(mode="json"),
                )
            decision.assignees.set(assignees)
            link = apps.get_model("decisions", "DecisionRecord")
            link.objects.db_manager(using).bulk_create(
                [
                    link(decision=decision, **target.lookups(link, "record"))
                    for target in sorted(targets, key=lambda target: (target.content_type.pk, target.object_id))
                ]
            )
            if assignees and not any(copy(decision).with_actor(person).has_access("act") for person in assignees):
                raise ValidationError({"assignees": "At least one assignee must be allowed to answer."})
            publish_change(decision, action="create", update_fields=None)
        return decision.with_actor(asking) if asking is not None else decision



DecisionRecordManager = ProtectedManager.from_queryset(DecisionRecordQuerySet)
