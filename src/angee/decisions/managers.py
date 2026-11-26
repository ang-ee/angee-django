"""Question admission and the decision selections."""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q
from django.db.models.functions import Now
from rebac import (
    check_permission,
    generic_target,
    system_context,
    to_object_ref,
    to_subject_ref,
    user_subject_type,
)
from rebac.actors import is_sudo
from rebac.backends import backend

from angee.base.managers import AngeeManager
from angee.base.models.append_only import AppendOnlyQuerySet
from angee.base.models.identity import public_id_of
from angee.base.permissions import actor_user_id
from angee.base.querysets import AngeeQuerySet
from angee.base.refs import generic_pointer_model
from angee.base.signals import mute_changes, publish_change
from angee.decisions.contracts import DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.decisions.signals import decision_asked
from angee.iam.roles import platform_admin_role, subject_has_role
from angee.provenance.evidence import readable_records

if TYPE_CHECKING:
    from angee.decisions.models import Decision

logger = logging.getLogger(__name__)


class DecisionQuerySet[T: Decision](AppendOnlyQuerySet[T], AngeeQuerySet[T]):
    def open_expression(self) -> ExpressionWrapper:
        return ExpressionWrapper(Q(verdict__isnull=True), output_field=BooleanField())

    def open(self) -> Any:
        return self.filter(verdict__isnull=True)

    def overdue_expression(self) -> ExpressionWrapper:
        """Open with a due instant that has passed; derived when read, never stored. False when undated."""
        return ExpressionWrapper(
            Q(verdict__isnull=True, due_at__isnull=False, due_at__lt=Now()), output_field=BooleanField(),
        )

    def overdue(self) -> Any:
        return self.filter(self.overdue_expression())

    def waiting_on(self, actor: Any) -> Any:
        """Open questions this actor may act on, assigned to them or unassigned."""

        identity = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        if identity is None:
            return self.none()
        return self.with_actor(actor).with_action("act").open().filter(
            Q(assignees=identity) | Q(assignees__isnull=True),
        )

    def deciders(self) -> dict[Any, list[Any]]:
        """The active people each of these questions can be put to, keyed by primary key: who may read and answer it.

        The active people who hold both ``act`` and ``read`` on the question, through
        REBAC's reverse lookups of each: the administrator arm and every owner's
        ``owner_eligible`` arm count, while a holder of ``act`` who may not read the
        question (an owner arm that grants answering alone, as intake's sharers hold)
        is left out, as are service users and inactive accounts (``active_people``).
        Unlike :meth:`waiting_on` it has no "assigned to me or unassigned" clause, so
        an administrator is among the deciders of a question assigned to others. Each
        list is in primary-key order; a question nobody can be put to maps to an empty
        list. :meth:`put_to` is the same rule read from the person's side. Two reverse
        lookups per question, read as decisions' own named system work: it suits one
        question or a few, not every open question at once.
        """

        with system_context(reason="decisions.deciders"):
            holders = {
                decision.pk: _holders(decision, "act") & _holders(decision, "read")
                for decision in self.only("pk").order_by("pk")
            }
            return _active_people(holders)

    def administrators(self) -> dict[Any, list[Any]]:
        """The platform administrators among each of these questions' :meth:`deciders`, keyed by primary key.

        Deciders who are platform administrators, so an administrator is never put a
        question they cannot answer: ``act`` takes an owner's ``owner_managed`` off the
        administrator arm (intake's access questions), and then the question is none of
        theirs. :meth:`for_administrator` is the same rule read from the person's side.
        Each decider is judged by :func:`_is_platform_admin`, once per call.
        """

        deciders = self.deciders()
        judged: dict[Any, bool] = {}
        for people in deciders.values():
            for person in people:
                if person.pk not in judged:
                    judged[person.pk] = _is_platform_admin(person)
        return {pk: [person for person in people if judged[person.pk]] for pk, people in deciders.items()}

    def for_administrator(self, person: Any) -> Any:
        """The open questions here put to ``person`` as an administrator: :meth:`put_to`, if they are one.

        :meth:`administrators` read from the person's side: ``person`` is among a
        question's administrators exactly when the question is in
        ``for_administrator(person)``, so anyone but a platform administrator among the
        question's deciders is put none.
        """

        if person is None:
            return self.none()
        if not _is_platform_admin(person):
            return self.with_actor(person).none()
        return self.put_to(person)

    def put_to(self, person: Any) -> Any:
        """The open questions here that can be put to ``person``: those they hold both ``act`` and ``read`` on.

        :meth:`deciders` read from the person's side, the same rule: ``person`` is one of
        a question's deciders exactly when the question is in ``put_to(person)``, so a
        service user, an inactive account or no person at all is put no question. Unlike
        :meth:`waiting_on` it has no "assigned to me or unassigned" clause. Rows come
        back in ``person``'s ``act`` scope, filtered by their ``read`` scope; a few
        statements whatever the number of questions, which suits every open question
        at once.
        """

        identity = actor_user_id(to_subject_ref(person)) if person is not None else None
        if identity is None:
            return self.none()
        rows = self.with_actor(person)
        if identity not in _active_people_among({identity}):
            return rows.none()
        return rows.with_action("act").open().filter(pk__in=self.model.objects.with_actor(person).values("pk"))

    def open_for(self, *records: Any) -> Any:
        return self.open().for_records(*records)

    def held_by_requesting_records_filter(self) -> Q:
        """A requesting record retains the question, such as a workflow step's: one correlated EXISTS per contributor.

        That asker owns the lifecycle of the questions it retains.
        """

        held = Q(pk__in=[])
        for rows in self.model.requester_querysets():
            held |= Q(Exists(rows.filter(_decision_id=OuterRef("pk")).order_by()))
        return held

    def for_records(self, *records: Any) -> Any:
        """Select decisions retaining any of the supplied canonical records."""

        return self.filter(self.for_records_filter(*records))

    @staticmethod
    def for_records_filter(*records: Any) -> Q:
        """Structural concern membership, independent of the decision's read policy."""

        link = apps.get_model("decisions", "DecisionRecord")
        concerns = Q(pk__in=[])
        for record in records:
            concerns |= link.record_filter(record)
        return Q(pk__in=link._base_manager.filter(concerns).order_by().values("decision_id"))

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


def _holders(decision: Any, action: str) -> set[Any]:
    """The user ids REBAC's reverse lookup finds holding ``action`` on ``decision``; the caller is system work."""

    return {
        identity for subject in backend().lookup_subjects(
            resource=to_object_ref(decision), action=action, subject_type=user_subject_type(),
        )
        if (identity := actor_user_id(subject)) is not None
    }


def _active_people_among(identities: set[Any]) -> dict[Any, Any]:
    """The active people (``active_people``) among ``identities``, by primary key: the one spelling of who counts."""

    return get_user_model().system_queryset().active_people().filter(pk__in=identities).in_bulk()


def _active_people(holders: dict[Any, set[Any]]) -> dict[Any, list[Any]]:
    """Each question's holders who are active people, in primary-key order."""

    people = _active_people_among(set().union(*holders.values()))
    return {pk: [people[identity] for identity in sorted(ids) if identity in people] for pk, ids in holders.items()}


def _is_platform_admin(person: Any) -> bool:
    """Whether ``person`` reaches IAM's platform-admin role.

    Composes IAM's ``platform_admin_role`` and ``subject_has_role``, since IAM's
    ``is_platform_admin`` reads the ambient actor rather than its argument. This waits
    for IAM's lane, which makes ``is_platform_admin(user)`` read its argument and adds
    ``platform_admin_holders()``.
    """

    role = platform_admin_role()
    return role is not None and subject_has_role(to_subject_ref(person), role)


_DecisionManagerBase = AngeeManager.from_queryset(DecisionQuerySet)


class DecisionManager[T: Decision](_DecisionManagerBase[T]):
    def remind_due(self, *, now: datetime, limit: int = 200) -> int:
        """Remind up to ``limit`` open questions whose ``remind_after`` has passed; return how many were sent.

        Rows another sweep holds are skipped, and each reminder runs in its own
        savepoint, so one failing receiver neither starves nor rolls back the others;
        its question stays unreminded for the next sweep. A question a requesting record
        retains keeps its asker's own timing (a workflow step wakes by its deadline).
        """
        if not 1 <= limit <= 1000:
            raise ValueError("Reminder limit must be between 1 and 1000.")
        rows = self.model.system_queryset()
        due = (
            rows.open()
            .filter(remind_after__lte=now, reminded_at__isnull=True)
            .exclude(rows.held_by_requesting_records_filter())
            .lock_if_supported(skip_locked=True)
            .only("pk")
            .order_by("pk")
        )
        reminded = 0
        with transaction.atomic():
            for decision in due[:limit]:
                try:
                    with transaction.atomic():
                        reminded += decision.remind()
                except Exception as error:
                    logger.warning("Reminder for decision %s failed (%s).", decision.pk, type(error).__name__)
        return reminded

    def ask(self, request: DecisionRequest, *, actor: Any) -> Any:
        """Admit one question after checking its participants' standing read access.

        ``decision_asked`` fires once the question is admitted, inside this transaction
        and outside decisions' own system context; a refused ask sends nothing.
        """
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
        with transaction.atomic():
            with system_context(reason="decisions.ask"):
                # Capture admission only after its immutable concern links exist.
                with mute_changes():
                    decision = self.create(
                        kind=request.kind,
                        requester=requester,
                        proposal=request.proposal.model_dump(mode="json"),
                        context=request.context.model_dump(mode="json"),
                        due_at=request.due_at,
                        remind_after=request.remind_after,
                    )
                decision.assignees.set(assignees)
                link = apps.get_model("decisions", "DecisionRecord")
                link.objects.bulk_create(
                    [
                        link(decision=decision, **target.lookups(link, "record"))
                        for target in sorted(targets, key=lambda target: (target.content_type.pk, target.object_id))
                    ]
                )
                if assignees and not any(
                    check_permission("act", decision, actor=person).allowed for person in assignees
                ):
                    raise ValidationError({"assignees": "At least one assignee must be allowed to answer."})
                publish_change(decision, action="create", update_fields=None)
            asked = decision.with_actor(asking) if asking is not None else decision
            decision_asked.send(sender=type(decision), decision=asked)
        return asked

    def supersede(self, decision: Any, request: DecisionRequest, *, actor: Any, reason: str) -> Any:
        """Replace one open question with ``request`` in one transaction; the asking owner's system context.

        The replaced question closes withdrawn by ``actor`` with ``reason`` and links
        its replacement as ``superseded_by``, so one question stays open and a stale
        answer to the old one meets the revision refusal. A question a requesting record
        retains (a workflow step's) belongs to that asker and is refused. Escalation is
        a supersede naming the escalation's assignees; no question changes in place.
        """
        if not is_sudo():
            raise PermissionDenied("Superseding requires the authorized asking owner's system context.")
        if not reason.strip():
            raise ValidationError({"reason": "Name the reason the question is replaced."})
        with transaction.atomic(), system_context(reason="decisions.supersede"):
            rows = self.filter(pk=decision.pk)
            row = rows.lock_if_supported().get()
            if not row.is_open:
                raise ValidationError({"revision": "The decision has changed; reload it."})
            if rows.filter(rows.held_by_requesting_records_filter()).exists():
                raise ValidationError({"decision": "Its requesting owner retains this question; replace it there."})
            replacement = self.ask(request, actor=actor)
            _row, _withdrawing, closed = row._withdraw(actor=actor, reason=reason, superseded_by=replacement)
            if not closed:
                raise ValidationError({"revision": "The decision has changed; reload it."})
        return replacement


class DecisionRecordQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Concern links are authored once with their decision."""


DecisionRecordManager = AngeeManager.from_queryset(DecisionRecordQuerySet)
