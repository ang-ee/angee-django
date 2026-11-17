"""One question, its immutable proposal, and its final verdict."""

from collections.abc import Iterable, Sequence
from typing import Any, Self

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import F
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.text import capfirst
from pydantic import JsonValue, TypeAdapter
from rebac import actor_context, current_actor, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.models.append_only import AppendOnlyModel
from angee.base.models.base import AngeeDataModel, AngeeModel
from angee.base.models.derived import DerivedFrom
from angee.base.models.locks import OptimisticLockMixin
from angee.base.permissions import actor_user_id, require_rebac_queryset
from angee.base.refs import RecordRef, record_view_refs
from angee.base.signals import publish_change
from angee.decisions.contracts import DecisionProposal, DecisionRecordReference
from angee.decisions.managers import DecisionManager, DecisionRecordManager
from angee.decisions.signals import decision_answered, decision_reminder_due
from angee.provenance.evidence import (
    EvidenceReference,
    readable_evidence,
    readable_evidence_records,
    readable_records,
)


def _user(actor: Any, *, active: bool = True) -> Any:
    identity = actor_user_id(to_subject_ref(actor if actor is not None else current_actor()))
    if identity is None:
        raise PermissionDenied("An active user is required.")
    users = get_user_model().system_queryset().filter(pk=identity)
    user = (users.filter(is_active=True) if active else users).first()
    if user is None:
        raise PermissionDenied("An active user is required.")
    return user


class Decision(OptimisticLockMixin, AppendOnlyModel, AngeeDataModel, concrete=True):
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
    answered_for = models.JSONField(null=True, blank=True)
    answer_evidence = models.JSONField(null=True, blank=True)
    answer_note = models.TextField(blank=True, default="")
    withdrawal_reason = models.TextField(blank=True, default="")
    superseded_by = models.OneToOneField(
        "decisions.Decision", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    due_at = models.DateTimeField(null=True, blank=True)
    remind_after = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)
    objects = DecisionManager()

    @classmethod
    def requester_querysets(cls) -> tuple[models.QuerySet, ...]:
        """Structural base-manager rows annotated with ``_decision_id``, in contribution order.

        These queries supply membership predicates, never the viewer's read.
        """
        return ()

    def requesting_records(self) -> Iterable[models.Model]:
        """Owner records retaining this question, contributed by asking capabilities."""
        for rows in type(self).requester_querysets():
            yield from rows.filter(_decision_id=self.pk).order_by("pk").distinct()

    def change_related_records(self) -> tuple[RecordRef, ...]:
        """Publish the question's retained concern identities and their inherited views with its verdict."""
        with system_context(reason="decisions.change_concerns"):
            links = self.records.model.system_queryset().filter(decision_id=self.pk).select_related("content_type")
            return record_view_refs(*(link.record_ref for link in links))

    class Meta:
        abstract = True
        rebac_resource_type = "decisions/decision"
        constraints = [models.CheckConstraint(
            condition=models.Q(verdict__isnull=True, verdict_values__isnull=True, answered_by__isnull=True,
                               answered_at__isnull=True, answered_for__isnull=True, answer_evidence__isnull=True,
                               answer_note="", withdrawal_reason="", superseded_by__isnull=True)
            | (models.Q(verdict__isnull=False, answered_at__isnull=False)
               & (models.Q(verdict=[])
                  | models.Q(answered_by__isnull=False, superseded_by__isnull=True, withdrawal_reason=""))
               & (~models.Q(verdict=[])
                  | models.Q(verdict_values__isnull=True, answered_for__isnull=True, answer_evidence__isnull=True,
                             answer_note=""))),
            name="decisions_verdict_consistent",
        )]
        indexes = [models.Index(
            fields=("remind_after",),
            condition=models.Q(verdict__isnull=True, reminded_at__isnull=True, remind_after__isnull=False),
            name="decisions_reminder_due",
        )]

    @property
    def is_open(self) -> bool:
        """No verdict means the question is still open."""
        return self.verdict is None

    @property
    def is_overdue(self) -> bool:
        """Open with a due instant that has passed, as ``DecisionQuerySet.overdue`` selects."""
        return self.is_open and self.due_at is not None and self.due_at < timezone.now()

    @property
    def kind_label(self) -> str:
        return capfirst(self.kind.replace("_", " ").replace("-", " "))

    @property
    def verdict_label(self) -> str:
        """Read the chosen labels from this question's frozen alternatives."""
        if self.verdict is None:
            return ""
        if not self.verdict:
            return "Superseded" if self.superseded_by_id is not None else "Withdrawn"
        return "; ".join(alternative["label"] for alternative in self.proposal["alternatives"]
                         if alternative["key"] in self.verdict)

    def __str__(self) -> str:
        return self.kind_label

    def withdraw(self, *, actor: Any, reason: str = "") -> Self:
        """An authorized asking owner withdraws this question in a named system context.

        ``reason`` is kept as ``withdrawal_reason``. Withdrawing a closed question
        changes nothing. Decisions cannot tell askers apart: any named system context
        is taken as the asking owner's.
        """
        if not is_sudo():
            raise PermissionDenied("Withdrawal requires the authorized asking owner's system context.")
        row, withdrawing, _closed = self._withdraw(actor=actor, reason=reason)
        return row.with_actor(withdrawing) if withdrawing is not None else row

    def _withdraw(self, *, actor: Any, reason: str, superseded_by: Any = None) -> tuple[Self, Any, bool]:
        """Close this question with an empty verdict if it is still open; callers hold the asker's system context."""
        # Cleanup must still close a retired principal's retained questions.
        actor = actor if actor is not None else current_actor()
        withdrawing = _user(actor, active=False) if actor is not None else None
        rows = type(self).objects
        with transaction.atomic(), system_context(reason="decisions.withdraw"):
            row = rows.filter(pk=self.pk).lock_if_supported().get()
            closed = row.is_open and bool(
                rows.filter(pk=self.pk).open().owner_update(
                    verdict=[],
                    answered_by=withdrawing,
                    answered_at=Now(),
                    withdrawal_reason=reason.strip(),
                    superseded_by=superseded_by,
                    revision=F("revision") + 1,
                    updated_at=Now(),
                )
            )
            if closed:
                row.refresh_from_db()
                publish_change(row, action="update", update_fields=None)
                decision_answered.send(sender=type(row), decision=row)
        return row, withdrawing, closed

    def remind(self) -> bool:
        """Stamp this open question's one reminder and announce it; True when this call did both.

        ``reminded_at`` is written once, so a repeated or concurrent reminder, or one on
        a closed question, writes and sends nothing. The revision stays: a reminder
        changes no answer. The stamp is decisions' own named system work, so callers need
        no elevation of their own. ``decision_reminder_due`` fires inside the
        transaction but outside that system context: a receiver error rolls the stamp
        back, and receivers carry no authority from decisions.
        """
        rows = type(self).objects
        with transaction.atomic():
            with system_context(reason="decisions.remind"):
                row = rows.filter(pk=self.pk).lock_if_supported().get()
                stamped = row.is_open and row.reminded_at is None and bool(
                    rows.filter(pk=self.pk, reminded_at__isnull=True).open().owner_update(
                        reminded_at=Now(), updated_at=Now(),
                    )
                )
                if stamped:
                    row.refresh_from_db()
                    publish_change(row, action="update", update_fields=None)
            if stamped:
                decision_reminder_due.send(sender=type(row), decision=row)
        return stamped

    def decide(
        self,
        *,
        actor: Any,
        chosen: Sequence[str],
        revision: int | None = None,
        values: object = None,
        on_behalf_of: object = None,
        evidence: object = (),
        note: str = "",
    ) -> Self:
        """Record one final verdict on this question. Proposal actions belong to the asker.

        ``values`` maps a concern's public id to its field values; it is validated here.
        ``on_behalf_of`` names the record the answer is given for, any model, and
        ``evidence`` the records that show how it was obtained; each is an
        ``EvidenceReference`` (model, public id, optional pointer) and nothing more.
        Every participant (the answerer, requester and active assignees) must read each
        record, as asking requires of the question's context, and the stored
        ``answered_for`` and ``answer_evidence`` are rebuilt from the resolved records,
        so no caller-written label is kept. ``note`` is the answerer's own account,
        stored as ``answer_note``. ``answered_by`` stays the actor, whose ``act``
        permission still decides.
        """
        answering = _user(actor)
        errors: dict[str, str] = {}
        represented: EvidenceReference | None = None
        cited: tuple[EvidenceReference, ...] = ()
        try:
            represented = TypeAdapter(EvidenceReference | None).validate_python(on_behalf_of)
        except ValueError as error:
            errors["on_behalf_of"] = str(error)
        try:
            cited = TypeAdapter(tuple[EvidenceReference, ...]).validate_python(evidence)
        except ValueError as error:
            errors["evidence"] = str(error)
        if not isinstance(note, str):
            errors["note"] = "Write the note as text."
        if errors:
            raise ValidationError(errors)
        rows = type(self).objects
        with transaction.atomic():
            with system_context(reason="decisions.decide"):
                row = rows.filter(pk=self.pk).lock_if_supported().get()
                row.require_access("act", answering)
                if revision is not None:
                    row.require_revision(revision)
                if not row.is_open:
                    raise ValidationError({"revision": "The decision has changed; reload it."})
                answered_for, answer_evidence = row._resolved_answer_references(represented, cited, answering)
                try:
                    validated = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(
                        {} if values is None else values
                    )
                    links = {link.record_public_id: link for link in row.records.with_actor(answering).all()}
                    selected = DecisionProposal.model_validate(row.proposal).choose(
                        chosen,
                        values=validated,
                        record_models={
                            identity: model
                            for identity, link in links.items()
                            if identity in validated and (model := link.content_type.model_class()) is not None
                        },
                    )
                except ValueError as error:
                    raise ValidationError({"chosen": str(error)}) from error
                try:
                    for alternative in selected:
                        for identity, actions in alternative.actions.items():
                            if identity not in validated:
                                continue
                            link = links.get(identity)
                            reference = link.record if link is not None else None
                            if reference is None:
                                raise ValueError("The record for a chosen action is missing or inaccessible.")
                            record = (
                                require_rebac_queryset(actions.target_model(reference), actor=answering)
                                .for_write()
                                .get(pk=reference.pk)
                            )
                            assert isinstance(record, AngeeModel)
                            record.require_access("write", answering)
                            with actor_context(answering):
                                actions.resolve(record, context={"actor": answering}, values=validated[identity])
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
                        verdict_values=validated or None,
                        answered_for=answered_for,
                        answer_evidence=answer_evidence,
                        answer_note=note.strip(),
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

    def _resolved_answer_references(
        self, represented: EvidenceReference | None, cited: tuple[EvidenceReference, ...], answering: Any,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        """Admit the answer's records for every participant and rebuild their references from those records."""
        references = (*((represented,) if represented is not None else ()), *cited)
        if not references:
            return None, None
        readable_records(references, self._participants(answering))
        rebuilt = [
            DecisionRecordReference.of(record, pointer=reference.pointer).model_dump(mode="json")
            for reference, record in readable_evidence_records(references, answering)
        ]
        if len(rebuilt) != len(references):
            raise PermissionDenied("The answerer must read every referenced record.")
        if represented is None:
            return None, rebuilt
        return rebuilt[0], rebuilt[1:] or None

    def readable_answered_for(self, reader: Any) -> dict[str, Any] | None:
        """The represented record's reference when ``reader`` may read that record, else None."""
        if self.answered_for is None:
            return None
        reference = DecisionRecordReference.model_validate(self.answered_for)
        return self.answered_for if readable_evidence([reference], reader) else None

    def readable_answer_evidence(self, reader: Any) -> list[dict[str, Any]]:
        """The answer's evidence references whose records ``reader`` may read, in stored order."""
        references = [DecisionRecordReference.model_validate(item) for item in self.answer_evidence or ()]
        return [reference.model_dump(mode="json") for reference in readable_evidence(references, reader)]

    def _participants(self, answering: Any) -> tuple[Any, ...]:
        """The answerer, then this question's active requester and assignees; read in the verb's system context."""
        named = {
            pk for pk in (self.requester_id, *self.assignees.values_list("pk", flat=True))
            if pk is not None and pk != answering.pk
        }
        return (answering, *get_user_model().system_queryset().filter(pk__in=named, is_active=True).order_by("pk"))

    def validate_verdict(self, chosen: tuple[Any, ...], *, actor: Any) -> None:
        """Allow composed policy to refuse an answer before its verdict is retained."""


class DecisionRecord(DerivedFrom, concrete=True):
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
