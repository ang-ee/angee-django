"""Sealed proposal rounds and their structured response documents.

Round and Proposal are supporting coordination/document objects, not planning
hierarchies. A Round owns disclosure and decision; a Proposal owns its response
lifecycle. Direct relationship tuples are the seal: responder/editor tuples are
verb-managed, while only Round audience roles are exposed through the declared
record-share surface.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar, Self, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.dateparse import parse_date
from rebac import (
    RelationshipTuple,
    current_actor,
    delete_relationship,
    delete_relationships,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.actors import to_subject_ref
from rebac.models import active_relationship_model
from rebac.types import RelationshipFilter, SubjectRef

from angee.base.actors import actor_user_id
from angee.base.fields import FractionalRankField, StateField
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel, AngeeManager
from angee.base.scoping import bind_actor
from angee.base.transitions import StateTransitions, save_state, transition
from angee.messaging.models import ThreadedModelMixin
from angee.money.fields import MoneyField

_TRACK_SYSTEM_ACTOR = SubjectRef.of("proposals/system", "track")
"""Non-user attribution for Project rows created only by ``create_track``."""


class RoundOpeningPolicy(models.TextChoices):
    """Which responder-owned surfaces disclosure opens."""

    FACILITATOR_ONLY = "facilitator_only", "Facilitator only"
    ANSWERS = "answers", "Answers"
    ANSWERS_AND_TRACKS = "answers_and_tracks", "Answers and tracks"


class RoundStatus(models.TextChoices):
    """Code-owned Round lifecycle."""

    COLLECTING = "collecting", "Collecting"
    OPENED = "opened", "Opened"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class RoundOutcome(models.TextChoices):
    """Terminal close outcomes."""

    AWARDED = "awarded", "Awarded"
    NO_AWARD = "no_award", "No award"


class ProposalState(models.TextChoices):
    """Single Proposal document lifecycle."""

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    ACCEPTED = "accepted", "Accepted"
    PARTIALLY_ACCEPTED = "partially_accepted", "Partially accepted"
    DECLINED = "declined", "Declined"
    WITHDRAWN = "withdrawn", "Withdrawn"


class ProposalConfidence(models.TextChoices):
    """Closed confidence vocabulary for comparison."""

    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


def _adopt(instance: models.Model, source: models.Model, fields: Iterable[str]) -> None:
    """Copy persisted scalar/FK state without tripping guarded state descriptors."""

    for name in fields:
        field = source._meta.get_field(name)
        instance.__dict__[field.attname] = source.__dict__[field.attname]
        if field.is_relation:
            instance._state.fields_cache.pop(name, None)


def _receipt_user_id(fallback: Any | None = None) -> Any | None:
    """Return the ambient actor's attribution user, or one explicit fallback."""

    return actor_user_id(current_actor()) or fallback


def _user(user_id: Any) -> models.Model:
    """Resolve one attribution/user id through Django's configured user model."""

    return apps.get_model(settings.AUTH_USER_MODEL)._base_manager.get(pk=user_id)


def _relationship(resource: models.Model, relation: str, user: models.Model) -> RelationshipTuple:
    """Build one direct user relationship using each model's owned identity."""

    return RelationshipTuple(
        resource=to_object_ref(resource),
        relation=relation,
        subject=to_subject_ref(user),
    )


def _relationship_key(value: RelationshipTuple) -> tuple[str, str, str, str, str, str]:
    """Return the portable natural key of one relationship tuple."""

    return (
        str(value.resource.resource_type),
        str(value.resource.resource_id),
        str(value.relation),
        str(value.subject.subject_type),
        str(value.subject.subject_id),
        str(value.subject.optional_relation or ""),
    )


class ImmutableFieldsMixin(models.Model):
    """Reject identity/receipt changes except through an owning model verb."""

    immutable_fields: ClassVar[tuple[str, ...]] = ()

    class Meta:
        """Django options for the addon-local invariant mixin."""

        abstract = True

    def allow_immutable_save(self, *field_names: str) -> None:
        """Allow the next save to change named immutable attnames."""

        allowed = set(getattr(self, "_proposals_allowed_immutable_fields", set()))
        allowed.update(field_names)
        self._proposals_allowed_immutable_fields = allowed

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist after comparing immutable facts with the committed row."""

        allowed = set(getattr(self, "_proposals_allowed_immutable_fields", set()))
        try:
            if self.pk is not None and not self._state.adding:
                checked = tuple(name for name in self.immutable_fields if name not in allowed)
                if checked:
                    with system_context(reason=f"proposals.{self._meta.model_name}.immutable_fields"):
                        persisted = type(self)._base_manager.filter(pk=self.pk).values(*checked).first()
                    if persisted is not None:
                        changed = [name for name in checked if persisted[name] != getattr(self, name)]
                        if changed:
                            raise ValidationError(
                                {name.removesuffix("_id"): "This identity or receipt is immutable." for name in changed}
                            )
            super().save(*args, **kwargs)
        finally:
            self._proposals_allowed_immutable_fields = set()


class Round(ImmutableFieldsMixin, AuditMixin, ThreadedModelMixin, AngeeDataModel):
    """A bounded solicitation on one project or task; its chatter is shared."""

    runtime = True
    sqid_prefix = "rnd_"
    rebac_grantable = {
        "reader": "share",
        "evaluator": "share",
        "requester": "share",
    }
    immutable_fields = (
        "task_id",
        "project_id",
        "facilitator_id",
        "opened_at",
        "opened_by_id",
        "closed_at",
        "closed_by_id",
    )

    task = models.ForeignKey(
        "projects.Task",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="proposal_rounds",
    )
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="proposal_rounds",
    )
    name = models.CharField(max_length=240)
    facilitator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="facilitated_proposal_rounds",
    )
    requester_party = models.ForeignKey(
        "parties.Party",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requested_proposal_rounds",
    )
    opening_policy = StateField(
        choices_enum=RoundOpeningPolicy,
        default=RoundOpeningPolicy.FACILITATOR_ONLY,
    )
    status = StateField(choices_enum=RoundStatus, default=RoundStatus.COLLECTING)
    status_transitions = StateTransitions(
        status,
        {
            RoundStatus.COLLECTING: (RoundStatus.OPENED, RoundStatus.CANCELLED),
            RoundStatus.OPENED: (RoundStatus.CLOSED, RoundStatus.CANCELLED),
        },
    )
    outcome = StateField(choices_enum=RoundOutcome, null=True, blank=True)
    last_call_at = models.DateTimeField()
    submission_deadline = models.DateTimeField()
    opened_at = models.DateTimeField(null=True, blank=True, editable=False)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="opened_proposal_rounds",
    )
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="closed_proposal_rounds",
    )

    class Meta:
        """Django options for solicitation rounds."""

        abstract = True
        ordering = ("status", "submission_deadline", "sqid")
        rebac_resource_type = "proposals/round"
        rebac_id_attr = "sqid"
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(task__isnull=False, project__isnull=True)
                    | models.Q(task__isnull=True, project__isnull=False)
                ),
                name="ck_proposals_round_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=models.Q(last_call_at__lte=models.F("submission_deadline")),
                name="ck_proposals_round_last_call_deadline",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="closed", outcome__isnull=False)
                    | (~models.Q(status="closed") & models.Q(outcome__isnull=True))
                ),
                name="ck_proposals_round_outcome_only_closed",
            ),
        )
        indexes = (
            models.Index(fields=("status", "submission_deadline")),
            models.Index(fields=("facilitator", "status")),
            models.Index(
                fields=("task", "status"),
                condition=models.Q(task__isnull=False),
                name="ix_proposals_round_task_status",
            ),
            models.Index(
                fields=("project", "status"),
                condition=models.Q(project__isnull=False),
                name="ix_prp_round_project_status",
            ),
        )

    def __str__(self) -> str:
        """Return the Round's human name."""

        return self.name

    def apply_create_defaults(self) -> Mapping[str, tuple[models.Model, ...]]:
        """Expose the exactly-one target to the unsaved-row create preflight."""

        self._validate_target()
        if self.task_id is not None:
            return {"task": (self.task,)}
        return {"project": (self.project,)}

    def clean(self) -> None:
        """Validate target, deadline, outcome, and receipt coherence."""

        super().clean()
        self._validate_target()
        self._validate_dates()
        self._validate_lifecycle_receipts()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist while keeping target/deadline and opened-policy facts coherent."""

        self._validate_target()
        self._validate_dates()
        if self.pk is not None and not self._state.adding:
            with system_context(reason="proposals.round.opening_policy"):
                persisted = type(self)._base_manager.filter(pk=self.pk).values(
                    "status", "opening_policy"
                ).first()
            if (
                persisted is not None
                and persisted["status"] != RoundStatus.COLLECTING
                and persisted["opening_policy"] != self.opening_policy
            ):
                raise ValidationError({"opening_policy": "Opening policy is immutable after opening."})
        super().save(*args, **kwargs)

    def deletion_error(self) -> str | None:
        """Return why this Round cannot be deleted under the untouched-draft rule."""

        if self.status != RoundStatus.COLLECTING or self.outcome is not None:
            return "Only a collecting round can be deleted."
        with system_context(reason="proposals.round.delete_guard"):
            proposals = list(
                apps.get_model("proposals", "Proposal")._base_manager.filter(round_id=self.pk).order_by("pk")
            )
        if any(proposal.deletion_error() is not None for proposal in proposals):
            return "A round can be deleted only while every proposal is an untouched draft."
        return None

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only a collecting Round whose proposals are untouched drafts."""

        error = self.deletion_error()
        if error:
            raise ValidationError(error)
        return super().delete(*args, **kwargs)

    def open(self) -> Self:
        """Open disclosure exactly once, locking Round before Proposal rows."""

        if self.pk is None:
            raise ValidationError("A saved round is required.")
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.open").lock_if_supported().get(pk=self.pk)
            proposals = list(
                apps.get_model("proposals", "Proposal")
                .objects.sudo(reason="proposals.round.open.proposals")
                .lock_if_supported()
                .filter(round_id=locked.pk)
                .order_by("pk")
            )
            expected = locked._opening_relationships(proposals)
            if locked.status == RoundStatus.OPENED:
                locked._assert_open_postcondition(proposals, expected)
            elif locked.status == RoundStatus.COLLECTING:
                locked._reconcile_opening_relationships(proposals, expected)
                if locked.opening_policy == RoundOpeningPolicy.ANSWERS_AND_TRACKS:
                    for proposal in proposals:
                        if proposal.state == ProposalState.SUBMITTED and proposal.track_id is not None:
                            proposal._publish_track_locked(proposals)
                locked._mark_opened()
            else:
                locked.status_transitions.not_allowed(locked.status, RoundStatus.OPENED)
        _adopt(self, locked, ("status", "opened_at", "opened_by", "updated_at", "updated_by"))
        return self

    def close(
        self,
        outcome: str | RoundOutcome,
        *,
        accepted: Iterable[models.Model] = (),
        partial: Iterable[models.Model] = (),
    ) -> Self:
        """Close with an exact decision over every submitted Proposal."""

        if self.pk is None:
            raise ValidationError("A saved round is required.")
        try:
            outcome_value = RoundOutcome(getattr(outcome, "value", outcome))
        except ValueError as error:
            raise ValidationError({"outcome": "Choose awarded or no award."}) from error
        accepted_ids = self._selection_ids(accepted, "accepted")
        partial_ids = self._selection_ids(partial, "partial")
        overlap = accepted_ids & partial_ids
        if overlap:
            raise ValidationError("Accepted and partially accepted proposals must be disjoint.")
        if outcome_value == RoundOutcome.NO_AWARD and (accepted_ids or partial_ids):
            raise ValidationError({"outcome": "No-award closure cannot select proposals."})
        if outcome_value == RoundOutcome.AWARDED and not (accepted_ids or partial_ids):
            raise ValidationError({"outcome": "Awarded closure requires an accepted proposal."})

        proposal_model = apps.get_model("proposals", "Proposal")
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.close").lock_if_supported().get(pk=self.pk)
            proposals = list(
                proposal_model.objects.sudo(reason="proposals.round.close.proposals")
                .lock_if_supported()
                .filter(round_id=locked.pk)
                .order_by("pk")
            )
            by_id = {proposal.pk: proposal for proposal in proposals}
            unknown = sorted((accepted_ids | partial_ids) - set(by_id))
            if unknown:
                raise ValidationError("Every selected proposal must belong to this round.")
            if locked.status == RoundStatus.CLOSED:
                locked._assert_close_postcondition(
                    proposals,
                    outcome=outcome_value,
                    accepted_ids=accepted_ids,
                    partial_ids=partial_ids,
                )
            elif locked.status != RoundStatus.OPENED:
                locked.status_transitions.not_allowed(locked.status, RoundStatus.CLOSED)
            else:
                selected = accepted_ids | partial_ids
                if any(by_id[pk].state != ProposalState.SUBMITTED for pk in selected):
                    raise ValidationError("Only submitted proposals can be selected.")
                for proposal in proposals:
                    if proposal.state != ProposalState.SUBMITTED:
                        continue
                    if proposal.pk in accepted_ids:
                        proposal._mark_accepted(locked.facilitator_id)
                    elif proposal.pk in partial_ids:
                        proposal._mark_partially_accepted(locked.facilitator_id)
                    else:
                        proposal._mark_declined(locked.facilitator_id)
                locked._mark_closed(outcome_value)
        _adopt(
            self,
            locked,
            ("status", "outcome", "closed_at", "closed_by", "updated_at", "updated_by"),
        )
        return self

    def cancel(self) -> Self:
        """Cancel a collecting or opened Round, preserving a null outcome."""

        if self.pk is None:
            raise ValidationError("A saved round is required.")
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.cancel").lock_if_supported().get(pk=self.pk)
            if locked.status == RoundStatus.CANCELLED:
                if locked.outcome is not None or locked.closed_at is None or locked.closed_by_id is None:
                    raise ValidationError("Cancelled round receipts do not match the requested postcondition.")
            else:
                locked._mark_cancelled()
        _adopt(
            self,
            locked,
            ("status", "outcome", "closed_at", "closed_by", "updated_at", "updated_by"),
        )
        return self

    def transfer_facilitation(self, user: models.Model) -> Self:
        """Transfer facilitation and reconcile every private track editor grant."""

        if self.pk is None or user.pk is None:
            raise ValidationError("A saved round and user are required.")
        proposal_model = apps.get_model("proposals", "Proposal")
        project_model = apps.get_model("projects", "Project")
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.transfer").lock_if_supported().get(pk=self.pk)
            proposals = list(
                proposal_model.objects.sudo(reason="proposals.round.transfer.proposals")
                .lock_if_supported()
                .filter(round_id=locked.pk)
                .order_by("pk")
            )
            if locked.status in {RoundStatus.CLOSED, RoundStatus.CANCELLED}:
                raise ValidationError({"facilitator": "Terminal rounds cannot transfer facilitation."})
            old_id = locked.facilitator_id
            if old_id == user.pk:
                return self
            old_user = _user(old_id)
            locked.facilitator = user
            locked.allow_immutable_save("facilitator_id")
            locked.save(update_fields=("facilitator", "updated_at"))
            for proposal in proposals:
                if proposal.track_id is None:
                    continue
                track = project_model._base_manager.get(pk=proposal.track_id)
                if proposal.responder_id != old_id:
                    delete_relationship(_relationship(track, "editor", old_user))
                write_relationships([_relationship(track, "editor", user)])
                if track.lead_id == old_id and proposal.responder_id is None:
                    track.lead = user
                    track.sudo(reason="proposals.round.transfer.track_lead").save(
                        update_fields=("lead", "updated_at")
                    )
        _adopt(self, locked, ("facilitator", "updated_at", "updated_by"))
        return self

    @transition(status, source=RoundStatus.COLLECTING, target=RoundStatus.OPENED, on_success=save_state)
    def _mark_opened(self) -> None:
        """Record the immutable opening receipt."""

        self.opened_at = timezone.now()
        self.opened_by_id = _receipt_user_id(self.facilitator_id)
        self.allow_immutable_save("opened_at", "opened_by_id")
        self._transition_fields = {"opened_at", "opened_by"}

    @transition(status, source=RoundStatus.OPENED, target=RoundStatus.CLOSED, on_success=save_state)
    def _mark_closed(self, outcome: RoundOutcome) -> None:
        """Record one immutable close outcome and receipt."""

        self.outcome = outcome
        self.closed_at = timezone.now()
        self.closed_by_id = _receipt_user_id(self.facilitator_id)
        self.allow_immutable_save("closed_at", "closed_by_id")
        self._transition_fields = {"outcome", "closed_at", "closed_by"}

    @transition(
        status,
        source=(RoundStatus.COLLECTING, RoundStatus.OPENED),
        target=RoundStatus.CANCELLED,
        on_success=save_state,
    )
    def _mark_cancelled(self) -> None:
        """Record cancellation as terminal closure without an award outcome."""

        self.outcome = None
        self.closed_at = timezone.now()
        self.closed_by_id = _receipt_user_id(self.facilitator_id)
        self.allow_immutable_save("closed_at", "closed_by_id")
        self._transition_fields = {"outcome", "closed_at", "closed_by"}

    def _validate_target(self) -> None:
        if (self.task_id is None) == (self.project_id is None):
            raise ValidationError("A round must target exactly one task or project.")

    def _validate_dates(self) -> None:
        if (
            self.last_call_at is not None
            and self.submission_deadline is not None
            and self.last_call_at > self.submission_deadline
        ):
            raise ValidationError({"last_call_at": "Last call must not follow the submission deadline."})

    def _validate_lifecycle_receipts(self) -> None:
        if self.status == RoundStatus.COLLECTING and any(
            value is not None
            for value in (self.outcome, self.opened_at, self.opened_by_id, self.closed_at, self.closed_by_id)
        ):
            raise ValidationError("A collecting round cannot carry lifecycle receipts.")
        if self.status in {RoundStatus.OPENED, RoundStatus.CLOSED} and (
            self.opened_at is None or self.opened_by_id is None
        ):
            raise ValidationError("An opened round requires its opening receipt.")
        if self.status in {RoundStatus.CLOSED, RoundStatus.CANCELLED} and (
            self.closed_at is None or self.closed_by_id is None
        ):
            raise ValidationError("A terminal round requires its close receipt.")
        if (self.status == RoundStatus.CLOSED) != (self.outcome is not None):
            raise ValidationError({"outcome": "Only a closed round carries an outcome."})

    def _selection_ids(self, proposals: Iterable[models.Model], field: str) -> set[Any]:
        values: set[Any] = set()
        for proposal in proposals:
            if proposal.pk is None:
                raise ValidationError({field: "Selected proposals must be saved."})
            values.add(proposal.pk)
        return values

    def _responder_users(
        self,
        proposals: Iterable[models.Model],
        *,
        states: set[str] | None = None,
    ) -> tuple[models.Model, ...]:
        ids = sorted(
            {
                proposal.responder_id
                for proposal in proposals
                if proposal.responder_id is not None
                and (states is None or proposal.state in states)
            }
        )
        users = apps.get_model(settings.AUTH_USER_MODEL)._base_manager.in_bulk(ids)
        return tuple(users[user_id] for user_id in ids if user_id in users)

    def _opening_relationships(self, proposals: list[models.Model]) -> list[RelationshipTuple]:
        if self.opening_policy == RoundOpeningPolicy.FACILITATOR_ONLY:
            return []
        recipients = self._responder_users(
            proposals,
            states={ProposalState.SUBMITTED},
        )
        return [
            _relationship(proposal, "reader", user)
            for proposal in proposals
            if proposal.state == ProposalState.SUBMITTED
            for user in recipients
        ]

    def _reconcile_opening_relationships(
        self,
        proposals: list[models.Model],
        expected: list[RelationshipTuple],
    ) -> None:
        for proposal in proposals:
            resource = to_object_ref(proposal)
            delete_relationships(
                RelationshipFilter(
                    resource_type=resource.resource_type,
                    resource_id=resource.resource_id,
                    relation="reader",
                )
            )
        if expected:
            write_relationships(expected)

    def _assert_open_postcondition(
        self,
        proposals: list[models.Model],
        expected: list[RelationshipTuple],
    ) -> None:
        if self.opened_at is None or self.opened_by_id is None or self.outcome is not None:
            raise ValidationError("Opened round receipts do not match the requested postcondition.")
        expected_keys = {_relationship_key(value) for value in expected}
        proposal_refs = [to_object_ref(proposal) for proposal in proposals]
        if not proposal_refs:
            actual_keys: set[tuple[str, str, str, str, str, str]] = set()
        else:
            rows = active_relationship_model().objects.filter(
                resource_type=proposal_refs[0].resource_type,
                resource_id__in=[ref.resource_id for ref in proposal_refs],
                relation="reader",
            )
            actual_keys = {
                (
                    str(row.resource_type),
                    str(row.resource_id),
                    str(row.relation),
                    str(row.subject_type),
                    str(row.subject_id),
                    str(row.optional_subject_relation or ""),
                )
                for row in rows
            }
        if actual_keys != expected_keys:
            raise ValidationError("Opened round grants do not match the requested postcondition.")
        self._assert_open_track_postcondition(proposals)

    def _assert_open_track_postcondition(self, proposals: list[models.Model]) -> None:
        """Require every answers-and-tracks grant owned by the opening ceremony."""

        if self.opening_policy != RoundOpeningPolicy.ANSWERS_AND_TRACKS:
            return
        recipients = self._responder_users(
            proposals,
            states={ProposalState.SUBMITTED},
        )
        tracked = [
            proposal
            for proposal in proposals
            if proposal.state == ProposalState.SUBMITTED and proposal.track_id is not None
        ]
        expected = {
            _relationship_key(_relationship(proposal.track, "reader", user))
            for proposal in tracked
            for user in recipients
        }
        if not expected:
            return
        refs = [to_object_ref(proposal.track) for proposal in tracked]
        rows = active_relationship_model().objects.filter(
            resource_type=refs[0].resource_type,
            resource_id__in=[ref.resource_id for ref in refs],
            relation="reader",
        )
        actual = {
            (
                str(row.resource_type),
                str(row.resource_id),
                str(row.relation),
                str(row.subject_type),
                str(row.subject_id),
                str(row.optional_subject_relation or ""),
            )
            for row in rows
        }
        if not expected.issubset(actual):
            raise ValidationError("Opened round track grants do not match the requested postcondition.")

    def _assert_close_postcondition(
        self,
        proposals: list[models.Model],
        *,
        outcome: RoundOutcome,
        accepted_ids: set[Any],
        partial_ids: set[Any],
    ) -> None:
        if self.outcome != outcome or self.closed_at is None or self.closed_by_id is None:
            raise ValidationError("Closed round receipts do not match the requested postcondition.")
        actual_accepted = {proposal.pk for proposal in proposals if proposal.state == ProposalState.ACCEPTED}
        actual_partial = {
            proposal.pk for proposal in proposals if proposal.state == ProposalState.PARTIALLY_ACCEPTED
        }
        if actual_accepted != accepted_ids or actual_partial != partial_ids:
            raise ValidationError("Closed round decisions do not match the requested postcondition.")
        if any(proposal.state == ProposalState.SUBMITTED for proposal in proposals):
            raise ValidationError("Closed round left a submitted proposal undecided.")


class Topic(ImmutableFieldsMixin, AuditMixin, AngeeDataModel):
    """One stable, ordered comparison subject within a Round."""

    runtime = True
    sqid_prefix = "top_"
    immutable_fields = ("round_id", "key")

    round = models.ForeignKey(
        "proposals.Round",
        on_delete=models.CASCADE,
        related_name="topics",
    )
    key = models.CharField(max_length=80)
    name = models.CharField(max_length=200)
    hint = models.TextField(blank=True, default="")
    sort_order = FractionalRankField()

    class Meta:
        """Django options for ordered comparison topics."""

        abstract = True
        ordering = ("round", "sort_order", "sqid")
        rebac_resource_type = "proposals/topic"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                models.F("round"),
                Lower("key"),
                name="uq_proposals_topic_round_key_ci",
            ),
            models.UniqueConstraint(
                fields=("round", "sort_order"),
                name="uq_proposals_topic_round_rank",
            ),
        )
        indexes = (models.Index(fields=("round", "sort_order", "id")),)

    def __str__(self) -> str:
        """Return the topic name."""

        return self.name

    def clean(self) -> None:
        """Normalize the immutable machine key."""

        self.key = str(self.key or "").strip().lower()
        if not self.key:
            raise ValidationError({"key": "Topic key is required."})
        super().clean()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the normalized stable key."""

        self.key = str(self.key or "").strip().lower()
        if not self.key:
            raise ValidationError({"key": "Topic key is required."})
        super().save(*args, **kwargs)


class ProposalManager(AngeeManager):
    """Own replay-safe message capture into Proposal and Answer rows."""

    CAPTURE_PARSER_VERSION = "proposals.capture.v1"
    CAPTURE_FIELDS = (
        "cost",
        "currency",
        "staffing",
        "timeframe_start",
        "timeframe_end",
        "confidence",
        "valid_until",
    )

    def capture_from_message(
        self,
        message: models.Model,
        round: models.Model,
        party: models.Model | None = None,
    ) -> models.Model:
        """Interpret one reply atomically and submit only after all rows succeed."""

        if message.pk is None or round.pk is None:
            raise ValidationError("A saved message and round are required for capture.")
        actor = current_actor()
        with transaction.atomic():
            locked_round = (
                type(round)
                .objects.sudo(reason="proposals.capture.round")
                .lock_if_supported()
                .get(pk=round.pk)
            )
            if locked_round.status != RoundStatus.COLLECTING:
                raise ValidationError({"round": "Replies cannot be captured after opening."})
            locked_message = (
                type(message)
                .objects.sudo(reason="proposals.capture.message")
                .lock_if_supported()
                .get(pk=message.pk)
            )
            resolved_party = party or self._resolved_sender_party(locked_message)
            if resolved_party is None:
                raise ValidationError({"party": "Capture requires a party or a resolved sender party."})
            interpretation = self._interpret(locked_message, locked_round)
            payload_hash = self._payload_hash(locked_message)
            proposal = self._locked_capture_proposal(
                message=locked_message,
                round=locked_round,
                party=resolved_party,
            )
            if proposal.state != ProposalState.DRAFT:
                if (
                    proposal.state == ProposalState.SUBMITTED
                    and proposal.capture_payload_hash == payload_hash
                    and proposal.capture_parser_version == self.CAPTURE_PARSER_VERSION
                ):
                    bind_actor(proposal, actor)
                    return proposal
                raise ValidationError("Captured proposal does not match the replayed interpretation.")

            self._apply_interpretation(
                proposal,
                message=locked_message,
                interpretation=interpretation,
                payload_hash=payload_hash,
            )
            proposal._submit_locked(fallback_user_id=locked_message.created_by_id or locked_round.facilitator_id)
        bind_actor(proposal, actor)
        return proposal

    def _locked_capture_proposal(
        self,
        *,
        message: models.Model,
        round: models.Model,
        party: models.Model,
    ) -> models.Model:
        """Resolve an existing capture shell or create/reload its unique row."""

        candidates = list(
            self.sudo(reason="proposals.capture.lookup")
            .lock_if_supported()
            .filter(
                models.Q(source_message_id=message.pk)
                | models.Q(round_id=round.pk, party_id=party.pk)
            )
            .order_by("pk")
        )
        if len(candidates) > 1:
            raise ValidationError("Message and party resolve to different proposal shells.")
        if candidates:
            proposal = candidates[0]
            if proposal.round_id != round.pk or proposal.party_id != party.pk:
                raise ValidationError("The captured message is already bound to another proposal.")
            if proposal.source_message_id not in (None, message.pk):
                raise ValidationError("The party's proposal already has a different source message.")
            return proposal

        proposal = self.model(round=round, party=party, source_message=message)
        proposal.full_clean(validate_unique=False, validate_constraints=False)
        try:
            with transaction.atomic():
                proposal.sudo(reason="proposals.capture.create").save()
        except IntegrityError:
            proposal = (
                self.sudo(reason="proposals.capture.concurrent_reload")
                .lock_if_supported()
                .filter(
                    models.Q(source_message_id=message.pk)
                    | models.Q(round_id=round.pk, party_id=party.pk)
                )
                .order_by("pk")
                .first()
            )
            if proposal is None:
                raise
            if proposal.round_id != round.pk or proposal.party_id != party.pk:
                raise ValidationError("A concurrent capture claimed the message for another proposal.")
        return proposal

    def _interpret(self, message: models.Model, round: models.Model) -> dict[str, Any]:
        """Return the deterministic v1 interpretation envelope for one Message."""

        metadata = message.metadata if isinstance(message.metadata, Mapping) else {}
        raw = metadata.get("proposal_capture", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ValidationError({"message": "proposal_capture metadata must be an object."})
        allowed = {*self.CAPTURE_FIELDS, "answers"}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValidationError({"message": f"Unknown proposal capture fields: {', '.join(unknown)}."})

        answers_raw = raw.get("answers", {})
        if not isinstance(answers_raw, Mapping):
            raise ValidationError({"message": "proposal_capture.answers must be an object."})
        topics = {
            topic.key.casefold(): topic
            for topic in apps.get_model("proposals", "Topic")
            ._base_manager.filter(round_id=round.pk)
            .order_by("sort_order", "pk")
        }
        answers: dict[Any, str] = {}
        for supplied_key, body in answers_raw.items():
            key = str(supplied_key).strip().casefold()
            topic = topics.get(key)
            if topic is None:
                raise ValidationError({"message": f"Unknown round topic {supplied_key!r}."})
            answers[topic.pk] = str(body or "").strip()
        if not answers and str(message.preview or "").strip() and topics:
            first = next(iter(topics.values()))
            answers[first.pk] = str(message.preview).strip()

        fields = {name: raw[name] for name in self.CAPTURE_FIELDS if name in raw}
        if not answers and not fields:
            raise ValidationError({"message": "The reply contains no proposal fields or answers."})
        return {"answers": answers, "fields": fields}

    def _apply_interpretation(
        self,
        proposal: models.Model,
        *,
        message: models.Model,
        interpretation: Mapping[str, Any],
        payload_hash: str,
    ) -> None:
        """Upsert interpreted fields and Answers before the final submit edge."""

        fields = dict(cast(Mapping[str, Any], interpretation["fields"]))
        currency_value = fields.pop("currency", None)
        if "cost" in fields:
            fields["cost"] = Decimal(str(fields["cost"])) if fields["cost"] not in (None, "") else None
        if currency_value not in (None, ""):
            currency_model = apps.get_model("money", "Currency")
            fields["currency"] = currency_model._base_manager.get(code__iexact=str(currency_value).strip())
        elif "cost" in fields:
            fields["currency"] = None
        for name in ("timeframe_start", "timeframe_end", "valid_until"):
            if name in fields and fields[name] not in (None, ""):
                parsed = fields[name] if isinstance(fields[name], date) else parse_date(str(fields[name]))
                if parsed is None:
                    raise ValidationError({name: "Use an ISO date."})
                fields[name] = parsed
        for name, value in fields.items():
            setattr(proposal, name, value)
        if proposal.source_message_id is None:
            proposal.source_message = message
            proposal.allow_immutable_save("source_message_id")
        proposal.capture_payload_hash = payload_hash
        proposal.capture_parser_version = self.CAPTURE_PARSER_VERSION
        proposal.full_clean(validate_unique=False, validate_constraints=False)
        proposal.save(
            update_fields=(
                *fields,
                "source_message",
                "capture_payload_hash",
                "capture_parser_version",
                "updated_at",
            )
        )

        answer_model = apps.get_model("proposals", "Answer")
        topics = apps.get_model("proposals", "Topic")._base_manager.in_bulk(interpretation["answers"])
        for topic_id, body in cast(Mapping[Any, str], interpretation["answers"]).items():
            answer = answer_model._base_manager.filter(proposal_id=proposal.pk, topic_id=topic_id).first()
            if answer is None:
                answer = answer_model(proposal=proposal, topic=topics[topic_id], body=body)
            else:
                answer.body = body
            answer.full_clean(validate_unique=False, validate_constraints=False)
            answer.sudo(reason="proposals.capture.answer").save()

    def _resolved_sender_party(self, message: models.Model) -> models.Model | None:
        """Resolve the sender through the parties-owned handle matching seam."""

        if message.sender_id is None:
            return None
        handle = apps.get_model("parties", "Handle")._base_manager.get(pk=message.sender_id)
        if handle.party_id is None:
            apps.get_model("parties", "PartyHandle").objects.suggest_for(handle)
            handle.refresh_from_db(fields=("party",))
        return handle.party

    def _payload_hash(self, message: models.Model) -> str:
        """Hash the portable source payload interpreted by this parser version."""

        canonical = json.dumps(
            {
                "parser_version": self.CAPTURE_PARSER_VERSION,
                "preview": str(message.preview or ""),
                "metadata": message.metadata if isinstance(message.metadata, Mapping) else {},
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Proposal(ImmutableFieldsMixin, AuditMixin, AngeeDataModel):
    """One responder's structured response; its private work lives on ``track``."""

    runtime = True
    sqid_prefix = "prp_"
    immutable_fields = (
        "round_id",
        "responder_id",
        "party_id",
        "source_message_id",
        "track_id",
        "submitted_at",
        "submitted_by_id",
        "decided_at",
        "decided_by_id",
    )

    round = models.ForeignKey(
        "proposals.Round",
        on_delete=models.CASCADE,
        related_name="proposals",
    )
    responder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="proposal_responses",
    )
    party = models.ForeignKey(
        "parties.Party",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="proposals",
    )
    source_message = models.ForeignKey(
        "messaging.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="captured_proposal",
    )
    state = StateField(choices_enum=ProposalState, default=ProposalState.DRAFT)
    state_transitions = StateTransitions(
        state,
        {
            ProposalState.DRAFT: ProposalState.SUBMITTED,
            ProposalState.SUBMITTED: (
                ProposalState.ACCEPTED,
                ProposalState.PARTIALLY_ACCEPTED,
                ProposalState.DECLINED,
                ProposalState.WITHDRAWN,
            ),
        },
    )
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="submitted_proposals",
    )
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="decided_proposals",
    )
    track = models.OneToOneField(
        "projects.Project",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.PROTECT,
        related_name="source_proposal",
    )
    cost = MoneyField(null=True, blank=True, currency_field="currency")
    currency = models.ForeignKey(
        "money.Currency",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="proposals",
    )
    staffing = models.CharField(max_length=240, blank=True, default="")
    timeframe_start = models.DateField(null=True, blank=True)
    timeframe_end = models.DateField(null=True, blank=True)
    confidence = StateField(choices_enum=ProposalConfidence, null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    capture_payload_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    capture_parser_version = models.CharField(max_length=64, blank=True, default="", editable=False)

    objects = ProposalManager()

    class Meta:
        """Django options for sealed response documents."""

        abstract = True
        ordering = ("round", "state", "sqid")
        rebac_resource_type = "proposals/proposal"
        rebac_id_attr = "sqid"
        constraints = (
            models.CheckConstraint(
                condition=models.Q(responder__isnull=False) | models.Q(party__isnull=False),
                name="ck_proposals_proposal_identity",
            ),
            models.UniqueConstraint(
                fields=("round", "responder"),
                condition=models.Q(responder__isnull=False),
                name="uq_proposals_proposal_round_responder",
            ),
            models.UniqueConstraint(
                fields=("round", "party"),
                condition=models.Q(party__isnull=False),
                name="uq_proposals_proposal_round_party",
            ),
            models.UniqueConstraint(
                fields=("source_message",),
                condition=models.Q(source_message__isnull=False),
                name="uq_proposals_proposal_source_message",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cost__isnull=True, currency__isnull=True)
                    | models.Q(cost__isnull=False, currency__isnull=False)
                ),
                name="ck_proposals_proposal_money_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(cost__isnull=True) | models.Q(cost__gte=0),
                name="ck_proposals_proposal_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(timeframe_start__isnull=True)
                    | models.Q(timeframe_end__isnull=True)
                    | models.Q(timeframe_start__lte=models.F("timeframe_end"))
                ),
                name="ck_proposals_proposal_timeframe",
            ),
        )
        indexes = (models.Index(fields=("round", "state")),)

    def __str__(self) -> str:
        """Return a compact responder/party label."""

        return f"Proposal {self.public_id}"

    def clean(self) -> None:
        """Validate identity, money, timeframe, and lifecycle receipts."""

        super().clean()
        if self.responder_id is None and self.party_id is None:
            raise ValidationError("A proposal requires a responder or party.")
        if (self.cost is None) != (self.currency_id is None):
            raise ValidationError({"cost": "Cost and currency must be set together."})
        if self.cost is not None and self.cost < 0:
            raise ValidationError({"cost": "Cost cannot be negative."})
        if (
            self.timeframe_start is not None
            and self.timeframe_end is not None
            and self.timeframe_start > self.timeframe_end
        ):
            raise ValidationError({"timeframe_start": "Timeframe start must not follow its end."})
        self._validate_lifecycle_receipts()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Create shells only while collecting and reconcile verb-managed tuples."""

        if not self._state.adding:
            super().save(*args, **kwargs)
            self._reconcile_shell_access()
            return
        with transaction.atomic():
            locked_round = (
                apps.get_model("proposals", "Round")
                .objects.sudo(reason="proposals.proposal.create.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            if locked_round.status != RoundStatus.COLLECTING:
                raise ValidationError({"round": "Proposal shells cannot be added after opening."})
            self.round = locked_round
            super().save(*args, **kwargs)
            self._reconcile_shell_access()

    def deletion_error(self) -> str | None:
        """Return why this Proposal is no longer an untouched draft."""

        if self.state != ProposalState.DRAFT or self.submitted_at is not None or self.decided_at is not None:
            return "Only an untouched draft proposal can be deleted."
        if self.source_message_id is not None or self.track_id is not None:
            return "Captured or tracked proposals cannot be deleted."
        if any(
            value not in (None, "")
            for value in (
                self.cost,
                self.currency_id,
                self.staffing,
                self.timeframe_start,
                self.timeframe_end,
                self.confidence,
                self.valid_until,
                self.capture_payload_hash,
                self.capture_parser_version,
            )
        ):
            return "Only an untouched draft proposal can be deleted."
        if self.pk is not None:
            with system_context(reason="proposals.proposal.delete_guard"):
                if apps.get_model("proposals", "Answer")._base_manager.filter(proposal_id=self.pk).exists():
                    return "A proposal with answers cannot be deleted."
                if apps.get_model("proposals", "Review")._base_manager.filter(proposal_id=self.pk).exists():
                    return "A proposal with reviews cannot be deleted."
        return None

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only an untouched draft shell."""

        error = self.deletion_error()
        if error:
            raise ValidationError(error)
        return super().delete(*args, **kwargs)

    def submit(self) -> Self:
        """Submit while locking Round first and atomically revoking editor."""

        if self.pk is None:
            raise ValidationError("A saved proposal is required.")
        with transaction.atomic():
            round_model = apps.get_model("proposals", "Round")
            locked_round = (
                round_model.objects.sudo(reason="proposals.proposal.submit.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            locked = (
                type(self).objects.sudo(reason="proposals.proposal.submit")
                .lock_if_supported()
                .filter(round_id=locked_round.pk, pk=self.pk)
                .order_by("pk")
                .get()
            )
            if locked.state == ProposalState.SUBMITTED:
                locked._assert_submitted_postcondition()
            elif locked.state != ProposalState.DRAFT:
                locked.state_transitions.not_allowed(locked.state, ProposalState.SUBMITTED)
            elif locked_round.status != RoundStatus.COLLECTING:
                raise ValidationError({"round": "Proposals cannot be submitted after opening."})
            else:
                locked._submit_locked(fallback_user_id=locked.responder_id or locked_round.facilitator_id)
        _adopt(self, locked, ("state", "submitted_at", "submitted_by", "updated_at", "updated_by"))
        return self

    def withdraw(self) -> Self:
        """Withdraw one submitted Proposal under the Round-first lock order."""

        if self.pk is None:
            raise ValidationError("A saved proposal is required.")
        with transaction.atomic():
            round_model = apps.get_model("proposals", "Round")
            locked_round = (
                round_model.objects.sudo(reason="proposals.proposal.withdraw.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            locked = (
                type(self).objects.sudo(reason="proposals.proposal.withdraw")
                .lock_if_supported()
                .filter(round_id=locked_round.pk, pk=self.pk)
                .order_by("pk")
                .get()
            )
            if locked.state == ProposalState.WITHDRAWN:
                if locked.decided_at is None or locked.decided_by_id is None:
                    raise ValidationError("Withdrawn proposal receipts do not match the requested postcondition.")
            elif locked_round.status == RoundStatus.CLOSED:
                raise ValidationError({"round": "Proposals cannot be withdrawn after closure."})
            else:
                locked._mark_withdrawn(locked.responder_id or locked_round.facilitator_id)
        _adopt(self, locked, ("state", "decided_at", "decided_by", "updated_at", "updated_by"))
        return self

    def identify_party(self, party: models.Model) -> Self:
        """Set the external identity once under Round-first row locks."""

        if self.pk is None or party.pk is None:
            raise ValidationError("A saved proposal and party are required.")
        with transaction.atomic():
            round_model = apps.get_model("proposals", "Round")
            locked_round = (
                round_model.objects.sudo(reason="proposals.proposal.identify.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            locked = (
                type(self).objects.sudo(reason="proposals.proposal.identify")
                .lock_if_supported()
                .filter(round_id=locked_round.pk, pk=self.pk)
                .order_by("pk")
                .get()
            )
            if locked.party_id is not None:
                if locked.party_id != party.pk:
                    raise ValidationError({"party": "Proposal party is already identified."})
            else:
                locked.party = party
                locked.allow_immutable_save("party_id")
                locked.save(update_fields=("party", "updated_at"))
        _adopt(self, locked, ("party", "updated_at", "updated_by"))
        return self

    def create_track(self) -> models.Model:
        """Create the one private Project and grant facilitator plus responder editors."""

        if self.pk is None:
            raise ValidationError("A saved proposal is required.")
        actor = current_actor()
        project_model = apps.get_model("projects", "Project")
        with transaction.atomic():
            round_model = apps.get_model("proposals", "Round")
            locked_round = (
                round_model.objects.sudo(reason="proposals.proposal.create_track.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            locked = (
                type(self).objects.sudo(reason="proposals.proposal.create_track")
                .lock_if_supported()
                .filter(round_id=locked_round.pk, pk=self.pk)
                .order_by("pk")
                .get()
            )
            if locked.track_id is not None:
                track = project_model._base_manager.get(pk=locked.track_id)
            else:
                track = project_model(
                    title=f"{locked_round.name} — proposal track",
                    body="Private proposal delivery track.",
                    lead_id=locked.responder_id or locked_round.facilitator_id,
                )
                bind_actor(track, _TRACK_SYSTEM_ACTOR)
                with system_context(reason="proposals.proposal.create_track.project"):
                    track.sudo(reason="proposals.proposal.create_track.project").save()
                users = [_user(locked_round.facilitator_id)]
                if locked.responder_id is not None and locked.responder_id != locked_round.facilitator_id:
                    users.append(_user(locked.responder_id))
                write_relationships([_relationship(track, "editor", user) for user in users])
                locked.track = track
                locked.allow_immutable_save("track_id")
                locked.save(update_fields=("track", "updated_at"))
        bind_actor(track, actor)
        self.__dict__[self._meta.get_field("track").attname] = track.pk
        self._state.fields_cache["track"] = track
        return track

    def publish_track(self) -> models.Model:
        """Publish the private track to this Round's responders, idempotently."""

        if self.pk is None:
            raise ValidationError("A saved proposal is required.")
        with transaction.atomic():
            round_model = apps.get_model("proposals", "Round")
            locked_round = (
                round_model.objects.sudo(reason="proposals.proposal.publish_track.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            proposals = list(
                type(self).objects.sudo(reason="proposals.proposal.publish_track.proposals")
                .lock_if_supported()
                .filter(round_id=locked_round.pk)
                .order_by("pk")
            )
            locked = next((proposal for proposal in proposals if proposal.pk == self.pk), None)
            if locked is None:
                raise ValidationError("Proposal does not belong to the locked round.")
            locked._validate_publish_caller(locked_round)
            track = locked._publish_track_locked(proposals)
        return track

    def _validate_publish_caller(self, round: models.Model) -> None:
        """Allow facilitators always and the owning responder only after opening."""

        actor_id = actor_user_id(current_actor())
        if actor_id is not None and str(actor_id) == str(round.facilitator_id):
            return
        if (
            actor_id is not None
            and str(actor_id) == str(self.responder_id)
            and round.status == RoundStatus.OPENED
        ):
            return
        raise ValidationError(
            {
                "track": (
                    "Only the facilitator may publish before opening; the proposal "
                    "responder may publish while the round is opened."
                )
            }
        )

    @transition(state, source=ProposalState.DRAFT, target=ProposalState.SUBMITTED, on_success=save_state)
    def _mark_submitted(self, fallback_user_id: Any | None = None) -> None:
        """Record immutable submission receipts."""

        self.submitted_at = timezone.now()
        self.submitted_by_id = _receipt_user_id(fallback_user_id)
        self.allow_immutable_save("submitted_at", "submitted_by_id")
        self._transition_fields = {"submitted_at", "submitted_by"}

    @transition(state, source=ProposalState.SUBMITTED, target=ProposalState.ACCEPTED, on_success=save_state)
    def _mark_accepted(self, fallback_user_id: Any | None = None) -> None:
        """Accept this submitted Proposal."""

        self._set_decision_receipt(fallback_user_id)

    @transition(
        state,
        source=ProposalState.SUBMITTED,
        target=ProposalState.PARTIALLY_ACCEPTED,
        on_success=save_state,
    )
    def _mark_partially_accepted(self, fallback_user_id: Any | None = None) -> None:
        """Partially accept this submitted Proposal."""

        self._set_decision_receipt(fallback_user_id)

    @transition(state, source=ProposalState.SUBMITTED, target=ProposalState.DECLINED, on_success=save_state)
    def _mark_declined(self, fallback_user_id: Any | None = None) -> None:
        """Decline this submitted Proposal."""

        self._set_decision_receipt(fallback_user_id)

    @transition(state, source=ProposalState.SUBMITTED, target=ProposalState.WITHDRAWN, on_success=save_state)
    def _mark_withdrawn(self, fallback_user_id: Any | None = None) -> None:
        """Withdraw this submitted Proposal."""

        self._set_decision_receipt(fallback_user_id)

    def _set_decision_receipt(self, fallback_user_id: Any | None = None) -> None:
        self.decided_at = timezone.now()
        self.decided_by_id = _receipt_user_id(fallback_user_id)
        self.allow_immutable_save("decided_at", "decided_by_id")
        self._transition_fields = {"decided_at", "decided_by"}

    def _submit_locked(self, fallback_user_id: Any | None = None) -> None:
        if self.state == ProposalState.SUBMITTED:
            self._assert_submitted_postcondition()
            return
        if self.state != ProposalState.DRAFT:
            self.state_transitions.not_allowed(self.state, ProposalState.SUBMITTED)
        # Persist while the responder's editor tuple still authorizes this
        # actor-scoped row. The enclosing transaction then revokes that tuple;
        # neither edge becomes visible without the other.
        self._mark_submitted(fallback_user_id)
        if self.responder_id is not None:
            delete_relationship(_relationship(self, "editor", _user(self.responder_id)))

    def _assert_submitted_postcondition(self) -> None:
        if self.submitted_at is None or self.submitted_by_id is None or self.decided_at is not None:
            raise ValidationError("Submitted proposal receipts do not match the requested postcondition.")
        if self.responder_id is not None:
            editor = _relationship(self, "editor", _user(self.responder_id))
            key = _relationship_key(editor)
            rows = active_relationship_model().objects.filter(
                resource_type=key[0],
                resource_id=key[1],
                relation=key[2],
                subject_type=key[3],
                subject_id=key[4],
                optional_subject_relation=key[5],
            )
            if rows.exists():
                raise ValidationError("Submitted proposal still grants responder editor access.")

    def _reconcile_shell_access(self) -> None:
        if self.pk is None or self.responder_id is None:
            return
        responder = _user(self.responder_id)
        round = apps.get_model("proposals", "Round")._base_manager.get(pk=self.round_id)
        write_relationships([_relationship(round, "reader", responder)])
        editor = _relationship(self, "editor", responder)
        if self.state == ProposalState.DRAFT:
            write_relationships([editor])
        else:
            delete_relationship(editor)

    def _publish_track_locked(self, proposals: list[models.Model]) -> models.Model:
        if self.track_id is None:
            raise ValidationError({"track": "Create the proposal track before publishing it."})
        project_model = apps.get_model("projects", "Project")
        track = project_model._base_manager.get(pk=self.track_id)
        self._validate_track_task_queues(track)
        recipients = self.round._responder_users(
            proposals,
            states=self._track_recipient_states(),
        )
        if recipients:
            write_relationships([_relationship(track, "reader", user) for user in recipients])
        return track

    def _track_recipient_states(self) -> set[str]:
        """Return proposal states whose responders may receive a published track."""

        return {
            ProposalState.SUBMITTED,
            ProposalState.ACCEPTED,
            ProposalState.PARTIALLY_ACCEPTED,
            ProposalState.DECLINED,
            ProposalState.WITHDRAWN,
        }

    def _track_is_published(self, proposals: list[models.Model]) -> bool:
        """Detect publication through the responder-reader tuples the verb writes."""

        if self.track_id is None:
            return False
        recipients = self.round._responder_users(
            proposals,
            states=self._track_recipient_states(),
        )
        subject_ids = {to_subject_ref(user).subject_id for user in recipients}
        if not subject_ids:
            return False
        track_ref = to_object_ref(self.track)
        return active_relationship_model().objects.filter(
            resource_type=track_ref.resource_type,
            resource_id=track_ref.resource_id,
            relation="reader",
            subject_type=to_subject_ref(recipients[0]).subject_type,
            subject_id__in=subject_ids,
        ).exists()

    def _allowed_track_queue_ids(self, queue_model: type[models.Model]) -> set[Any]:
        """Return facilitator/responder personal queues allowed while sealed."""

        allowed_queue_ids: set[Any] = set()
        for user_id in {self.round.facilitator_id, self.responder_id} - {None}:
            queue = queue_model.objects.personal_for(_user(user_id), provision=False)
            if queue is not None:
                allowed_queue_ids.add(queue.pk)
        return allowed_queue_ids

    def _validate_track_task_queues(self, track: models.Model) -> None:
        """Require queue-less or facilitator/responder personal work before publication."""

        task_model = apps.get_model("projects", "Task")
        try:
            queue_field = task_model._meta.get_field("queue")
        except FieldDoesNotExist:
            return
        queue_model = queue_field.related_model
        allowed_queue_ids = self._allowed_track_queue_ids(queue_model)
        invalid = (
            task_model._base_manager.filter(project_id=track.pk)
            .exclude(queue_id__isnull=True)
            .exclude(queue_id__in=allowed_queue_ids)
            .order_by("pk")
            .exists()
        )
        if invalid:
            raise ValidationError(
                {
                    "track": (
                        "Track tasks must be queue-less or in a responder/facilitator "
                        "personal queue before publication."
                    )
                }
            )


    def _validate_lifecycle_receipts(self) -> None:
        if self.state == ProposalState.DRAFT and any(
            value is not None
            for value in (self.submitted_at, self.submitted_by_id, self.decided_at, self.decided_by_id)
        ):
            raise ValidationError("A draft proposal cannot carry lifecycle receipts.")
        if self.state != ProposalState.DRAFT and (
            self.submitted_at is None or self.submitted_by_id is None
        ):
            raise ValidationError("A non-draft proposal requires its submission receipt.")
        terminal = {
            ProposalState.ACCEPTED,
            ProposalState.PARTIALLY_ACCEPTED,
            ProposalState.DECLINED,
            ProposalState.WITHDRAWN,
        }
        if self.state in terminal and (self.decided_at is None or self.decided_by_id is None):
            raise ValidationError("A terminal proposal requires its decision receipt.")
        if self.state == ProposalState.SUBMITTED and (self.decided_at is not None or self.decided_by_id is not None):
            raise ValidationError("A submitted proposal cannot carry a decision receipt.")


class TaskProposalContainment(models.Model):
    """Same-row save participant keeping unpublished proposal work private."""

    extends = "projects.Task"
    runtime = False

    class Meta:
        """Abstract zero-column donor folded into the composed Task model."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Reject shared queues for tasks on an unpublished proposal track."""

        try:
            queue_field = self._meta.get_field("queue")
        except FieldDoesNotExist:
            super().save(*args, **kwargs)
            return
        update_fields = kwargs.get("update_fields")
        containment_may_change = self._state.adding or update_fields is None or bool(
            {
                "project",
                "project_id",
                queue_field.name,
                queue_field.attname,
                "stage",
                "stage_id",
                "cycle",
                "cycle_id",
            }.intersection(update_fields)
        )
        queue_id = getattr(self, queue_field.attname)
        if queue_id is None:
            for source_name in ("stage", "cycle"):
                if getattr(self, f"{source_name}_id", None) is not None:
                    queue_id = getattr(getattr(self, source_name), "queue_id", None)
                    if queue_id is not None:
                        break
        if containment_may_change and self.project_id is not None and queue_id is not None:
            proposal_model = apps.get_model("proposals", "Proposal")
            proposal = (
                proposal_model._base_manager.select_related("round", "track")
                .filter(track_id=self.project_id)
                .first()
            )
            if proposal is not None:
                proposals = list(
                    proposal_model._base_manager.filter(round_id=proposal.round_id).order_by("pk")
                )
                if (
                    not proposal._track_is_published(proposals)
                    and queue_id
                    not in proposal._allowed_track_queue_ids(queue_field.related_model)
                ):
                    raise ValidationError(
                        {
                            "queue": (
                                "Unpublished proposal track tasks must stay queue-less or in a "
                                "facilitator/responder personal queue until publication."
                            )
                        }
                    )
        super().save(*args, **kwargs)


class Answer(ImmutableFieldsMixin, AuditMixin, AngeeDataModel):
    """One Proposal response aligned to one Topic in the same Round."""

    runtime = True
    sqid_prefix = "ans_"
    immutable_fields = ("proposal_id", "topic_id")

    proposal = models.ForeignKey(
        "proposals.Proposal",
        on_delete=models.CASCADE,
        related_name="answers",
    )
    topic = models.ForeignKey(
        "proposals.Topic",
        on_delete=models.CASCADE,
        related_name="answers",
    )
    body = models.TextField(blank=True, default="")

    class Meta:
        """Django options for topic-aligned answers."""

        abstract = True
        ordering = ("topic", "proposal", "sqid")
        rebac_resource_type = "proposals/answer"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("proposal", "topic"),
                name="uq_proposals_answer_proposal_topic",
            ),
        )
        indexes = (models.Index(fields=("topic", "proposal")),)

    def clean(self) -> None:
        """Reject an Answer whose Proposal and Topic belong to different Rounds."""

        super().clean()
        if self.proposal_id is not None and self.topic_id is not None:
            proposal_round_id = getattr(self.proposal, "round_id", None)
            topic_round_id = getattr(self.topic, "round_id", None)
            if proposal_round_id != topic_round_id:
                raise ValidationError({"topic": "Answer topic must belong to the proposal's round."})

    def __str__(self) -> str:
        """Return the topic-aligned response label."""

        return f"{self.proposal_id}:{self.topic_id}"


class Review(ImmutableFieldsMixin, AuditMixin, AngeeDataModel):
    """One evaluator's private assessment of a Proposal."""

    runtime = True
    sqid_prefix = "rvw_"
    immutable_fields = ("proposal_id", "reviewer_id")

    proposal = models.ForeignKey(
        "proposals.Proposal",
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="proposal_reviews",
    )
    body = models.TextField(blank=True, default="")

    class Meta:
        """Django options for evaluator assessments."""

        abstract = True
        ordering = ("reviewer", "proposal", "sqid")
        rebac_resource_type = "proposals/review"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("proposal", "reviewer"),
                name="uq_proposals_review_proposal_reviewer",
            ),
        )
        indexes = (models.Index(fields=("reviewer", "proposal")),)

    def __str__(self) -> str:
        """Return the reviewer/proposal assessment label."""

        return f"{self.reviewer_id}:{self.proposal_id}"
