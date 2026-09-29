"""Sealed proposal rounds and their structured response documents.

Round and Proposal are supporting coordination/document objects, not planning
hierarchies. A Round owns disclosure and decision; a Proposal owns its response
lifecycle. Shells admit responders; immutable receipts disclose answers and tracks.
Deliberate shares remain independent of these derived permissions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Self, cast

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.functions import JSONObject, Lower
from django.db.models.lookups import Exact
from django.utils import timezone
from django.utils.dateparse import parse_date
from rebac import (
    PermissionDenied,
    actor_context,
    current_actor,
    system_context,
    to_object_ref,
)
from rebac.actors import to_subject_ref
from rebac.backends import backend
from rebac.models import active_relationship_model
from rebac.relationships import delete_relationships
from rebac.types import RelationshipFilter, SubjectRef

from angee.base.actors import actor_user_id, instance_actor, subject_reaches_user
from angee.base.errors import DomainError, RecordAccessSubjectRefused
from angee.base.fields import FractionalRankField, StateField
from angee.base.mixins import AuditMixin, CreationKeyConflict, ImmutableFieldsMixin, OptimisticLockMixin, OwnerQuerySet
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet, role_anchor
from angee.base.refs import canonical_record_model
from angee.base.scoping import bind_actor, system_queryset
from angee.base.transitions import StateTransitions, save_state, transition
from angee.base.validation import validate_model
from angee.iam.identity import user_label_expression, user_label_queryset
from angee.messaging.models import ThreadedModelMixin
from angee.money.fields import MoneyField
from angee.projects.access import bind
from angee.proposals.inputs import RoundTemplate

_TRACK_SYSTEM_ACTOR = SubjectRef.of("proposals/system", "track")
"""Non-user attribution for Project rows created only by ``create_track``."""

_CLARIFICATION_SYSTEM_ACTOR = SubjectRef.of("proposals/system", "clarification")
"""Anonymous attribution shared by clarification content and nested history."""


class ClarificationWidenBlocked(DomainError, ValidationError):
    """Sharing an asker's message would reveal an identity the round keeps hidden."""

    code = "HIDDEN_ASKER_IN_THREAD"

    def __init__(self) -> None:
        ValidationError.__init__(
            self,
            {"visibility": ValidationError(
                "A message by this question's hidden asker cannot be shared.", code=self.code,
            )},
        )


class PublishedQuestion(DomainError, ValidationError):
    """Responders invited to a shared question must retain its shared audience."""

    code = "PUBLISHED_QUESTION"

    def __init__(self) -> None:
        ValidationError.__init__(
            self,
            {"visibility": ValidationError("A published question cannot be narrowed.", code=self.code)},
        )


class RoundOpeningPolicy(models.TextChoices):
    """Which responder-owned surfaces disclosure opens."""

    FACILITATOR_ONLY = "facilitator_only", "Facilitator only"
    ANSWERS = "answers", "Answers"
    ANSWERS_AND_TRACKS = "answers_and_tracks", "Answers and tracks"
    DRAFTS_AND_TRACKS = "drafts_and_tracks", "Drafts and tracks"


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


class RosterVisibility(models.TextChoices):
    """Responder roster projection detail."""

    HIDDEN = "hidden", "Hidden"
    NAMED = "named", "Named"
    STATUS = "status", "Status"


class ClarificationAskers(models.TextChoices):
    """Whether peers may read the asker column."""

    HIDDEN = "hidden", "Hidden"
    NAMED = "named", "Named"


class AnswerVisibility(models.TextChoices):
    """Successively narrower inherited answer audiences."""

    ROUND = "round", "Round"
    RESPONDER = "responder", "Responder"
    SEALED = "sealed", "Managers only"


OPENING_POLICY_ORDER = (
    RoundOpeningPolicy.FACILITATOR_ONLY,
    RoundOpeningPolicy.ANSWERS,
    RoundOpeningPolicy.ANSWERS_AND_TRACKS,
    RoundOpeningPolicy.DRAFTS_AND_TRACKS,
)
ANSWER_VISIBILITY_ORDER = (AnswerVisibility.ROUND, AnswerVisibility.RESPONDER, AnswerVisibility.SEALED)


class QuestionAudience(models.TextChoices):
    """Audience choices when asking or narrowing an unpassed question."""

    DEFAULT = "default", "Section default"
    MANAGERS = "managers", "Managers"


class PassAudience(models.TextChoices):
    """Audience choices for a question's first pass."""

    DEFAULT = "default", "Section default"
    ASKER = "asker", "Asker"


@dataclass(frozen=True)
class RosterEntry:
    """An authorized name and optional track status, never a proposal row."""

    user_id: Any
    name: str
    track_status: str | None = None


class ClarificationWaitingSubquery(models.Subquery):
    """Collect the ordered recipient projection as JSON on supported databases.

    Django owns subquery correlation and JSON object construction. Its PostgreSQL
    ArraySubquery has no SQLite compiler; this domain projection uses the two
    databases' native JSON aggregates through Django's expression compiler seam.
    """

    output_field = models.JSONField()
    template = "(SELECT COALESCE(jsonb_agg(_recipient), '[]'::jsonb) FROM (%(subquery)s) recipients)"

    def as_sqlite(self, compiler: Any, connection: Any, **extra_context: Any) -> Any:
        return super().as_sql(
            compiler,
            connection,
            template="(SELECT json_group_array(json(_recipient)) FROM (%(subquery)s) recipients)",
            **extra_context,
        )


class RoundQuerySet(AngeeQuerySet):
    """Round collection scopes shared by record projections."""

    def active_for_project(self, project: Any) -> Any:
        """Select the newest live round targeting this project, its source task or track."""
        targets = models.Q(project=project) | models.Q(proposals__track=project)
        if project.converted_from_id is not None:
            targets |= models.Q(task_id=project.converted_from_id)
        return self.filter(targets, status__in=(RoundStatus.COLLECTING, RoundStatus.OPENED)).distinct().order_by(
            "-created_at", "-pk",
        )


class RoundManager(AngeeManager.from_queryset(RoundQuerySet)):  # type: ignore[misc]
    """Provision rounds by composing the native topic and admission owners."""

    def provision(
        self,
        target: Any,
        template: RoundTemplate | Mapping[str, Any],
        facilitator: models.Model,
        responders: Iterable[models.Model],
        team: models.Model | None = None,
        *,
        requester_party: models.Model | None = None,
        configuration: Mapping[str, Any] | None = None,
    ) -> Any:
        """Resume a round with the same target, name and declared settings."""

        template = validate_model(RoundTemplate, template, field="template")
        target_field = {"projects.project": "project", "projects.task": "task"}.get(target._meta.label_lower)
        if target_field is None:
            raise ValidationError({"target": "Choose a project or task."})
        self.check_create({target_field: (target,)})
        actor = current_actor()
        with transaction.atomic():
            system_queryset(type(target), lock=("self",)).get(pk=target.pk)
            candidate = self.model(**{target_field: target}, facilitator=facilitator, team=team)
            project = candidate.target_project()
            values = template.round_values(project)
            setup_values = self.model.setup_values(**(configuration or {}))
            if requester_party is not None:
                setup_values["requester_party_id"] = requester_party.pk
            rows = self.sudo(reason="proposals.round.provision").filter(
                **{target_field: target},
                name=template.name,
                closed_at__isnull=True,
            )
            round = rows.first()
            if round is None:
                round = self.model(
                    **{target_field: target}, facilitator=facilitator, team=team, **values, **setup_values,
                )
                round.sudo(reason="proposals.round.provision").save()
            elif not round.with_actor(actor).has_access("write"):
                raise PermissionDenied("Round write access is required to resume provisioning.")
            elif any(getattr(round, name) != value for name, value in values.items()) or (
                round.facilitator_id != facilitator.pk or round.team_id != (team.pk if team else None)
            ):
                raise ValidationError({"template": "The existing round has different settings."})
            else:
                changed = []
                for name, value in setup_values.items():
                    previous = getattr(round, name)
                    if previous == value:
                        continue
                    if previous is not None:
                        raise ValidationError({"round": "The existing round has different setup choices."})
                    setattr(round, name, value)
                    changed.append(name)
                if changed:
                    round.save(update_fields=(*changed, "updated_at"))
            topic_model = apps.get_model("proposals", "Topic")
            with system_context(reason="proposals.round.provision.topics"):
                topics = {topic.key: topic for topic in topic_model._base_manager.filter(round=round)}
                if set(topics) - {topic.key for topic in template.topics}:
                    raise ValidationError({"template": "The existing round has different topic keys."})
                rank = None
                for topic in template.topics:
                    rank = FractionalRankField.get_append_rank(rank)
                    values = dict(name=topic.name, hint=topic.hint, sort_order=rank)
                    existing = topics.get(topic.key)
                    if existing is None:
                        topic_model.objects.create(round=round, key=topic.key, **values)
                    elif any(getattr(existing, key) != value for key, value in values.items()):
                        raise ValidationError({"template": "The existing topic differs from the template."})
            round.sudo(reason="proposals.round.provision.admission")
            for responder in responders:
                round.admit(responder, track=template.tracks)
        return round.with_actor(actor)

    def for_track(self, project_id: Any) -> Any | None:
        """Return the round whose admitted shell owns ``project_id`` as its track, else ``None``.

        Track items keep the round's holder ceiling and manager-only sharing; an
        ordinary project or a question on the round's target project has no round here.
        """

        if project_id is None:
            return None
        return system_queryset(self.model).filter(proposals__track_id=project_id).order_by("pk").first()


def _adopt(instance: models.Model, source: models.Model, fields: Iterable[str]) -> None:
    """Copy persisted scalar/FK state without tripping guarded state descriptors."""

    if isinstance(source, OptimisticLockMixin):
        fields = (*fields, "revision")
    for name in fields:
        field = source._meta.get_field(name)
        instance.__dict__[field.attname] = source.__dict__[field.attname]
        if field.is_relation:
            instance._state.fields_cache.pop(name, None)


def _receipt_user_id(fallback: Any | None = None) -> Any | None:
    """Return the ambient actor's attribution user, or one explicit fallback."""

    return actor_user_id(current_actor()) or fallback


class Round(OptimisticLockMixin, ImmutableFieldsMixin, AuditMixin, ThreadedModelMixin, AngeeDataModel):
    """A bounded solicitation on one project or task; its chatter is shared."""

    @classmethod
    def setup_values(cls) -> dict[str, Any]:
        """Terminal hook for addon-owned round setup fields."""

        return {}

    @classmethod
    def setup_complete_condition(cls, actor: Any) -> models.Q:
        """A persisted round is provisioned; routing addons may require more."""

        return models.Q()

    runtime = True
    sqid_prefix = "rnd_"
    thread_team_field = "team"
    rebac_grantable = {
        "reader": "share",
        "evaluator": "share",
        "proposal_viewer": "share",
    }
    immutable_fields = (
        "task_id",
        "project_id",
        "facilitator_id",
        "opening_policy",
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
    team = models.ForeignKey(
        "spaces.Group", null=True, blank=True, on_delete=models.SET_NULL, related_name="proposal_rounds"
    )
    roster_visibility = StateField(choices_enum=RosterVisibility, default=RosterVisibility.HIDDEN)
    clarification_askers = StateField(choices_enum=ClarificationAskers, default=ClarificationAskers.HIDDEN)
    opens_after = models.ForeignKey(
        "projects.Milestone",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="opening_rounds",
    )
    clarifications_shared_until = models.ForeignKey(
        "projects.Milestone",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="clarification_rounds",
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

    objects = RoundManager()

    class Meta:
        """Django options for solicitation rounds."""

        abstract = True
        ordering = ("status", "submission_deadline", "sqid")
        rebac_resource_type = "proposals/round"
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

    def target_project(self) -> Any:
        """Resolve the planning project for either kind of target."""
        with system_context(reason="proposals.round.target_project"):
            return self.project if self.project_id is not None else self.task.project

    @staticmethod
    def opening_phase_condition() -> models.Q:
        """The phase boundary expressed once for list projections and verbs."""
        return (
            models.Q(opens_after__isnull=True)
            | models.Q(project__current_milestone__sort_order__gt=models.F("opens_after__sort_order"))
            | models.Q(
                project__isnull=True,
                task__project__current_milestone__sort_order__gt=models.F("opens_after__sort_order"),
            )
        )

    @staticmethod
    def admission_condition() -> models.Q:
        """The lifecycle states that accept new proposal shells."""
        return models.Q(status=RoundStatus.COLLECTING) | models.Q(
            status=RoundStatus.OPENED,
            opening_policy=RoundOpeningPolicy.DRAFTS_AND_TRACKS,
        )

    @classmethod
    def can_open_expression(cls, actor: Any) -> models.Expression:
        """Combine the native write scope, lifecycle and phase in the list query."""
        if actor is None:
            return models.Value(False)
        rows = cls.objects.with_actor(actor).with_action("write").scoped_for_aggregate()
        return models.Exists(
            rows.filter(
                cls.opening_phase_condition(),
                pk=models.OuterRef("pk"),
                status=RoundStatus.COLLECTING,
            )
        )

    @classmethod
    def can_admit_expression(cls, actor: Any) -> models.Expression:
        """Combine the write scope and shell admission state in one expression."""
        if actor is None:
            return models.Value(False)
        rows = cls.objects.with_actor(actor).with_action("write").scoped_for_aggregate()
        return models.Exists(rows.filter(cls.admission_condition(), pk=models.OuterRef("pk")))

    def opening_phase_ready(self) -> bool:
        """Use the same phase predicate as the list projection."""
        return system_queryset(type(self)).filter(type(self).opening_phase_condition(), pk=self.pk).exists()

    def can_open(self) -> bool:
        """Report the opening verb's permission, lifecycle and phase eligibility."""
        return (
            system_queryset(type(self))
            .filter(pk=self.pk)
            .annotate(
                ready=type(self).can_open_expression(instance_actor(self)),
            )
            .values_list("ready", flat=True)
            .get()
        )

    def can_admit(self) -> bool:
        """Report whether this actor may admit a new shell now."""
        return (
            system_queryset(type(self))
            .filter(pk=self.pk)
            .annotate(
                ready=type(self).can_admit_expression(instance_actor(self)),
            )
            .values_list("ready", flat=True)
            .get()
        )

    def current_responders(self) -> models.QuerySet:
        """Return unretired shell holders, including those of a closed round."""
        return (
            system_queryset(apps.get_model(settings.AUTH_USER_MODEL))
            .filter(
                proposal_responses__round_id=self.pk,
                proposal_responses__retired_at__isnull=True,
            )
            .order_by("pk")
        )

    @classmethod
    def roster_prefetch(cls) -> models.Prefetch:
        """Batch scalar roster facts without reading each peer's proposal."""
        rows = (
            system_queryset(apps.get_model("proposals", "Proposal"))
            .filter(
                retired_at__isnull=True,
                responder__isnull=False,
            )
            .only("round_id", "responder_id")
            .annotate(
                _roster_name=user_label_expression("responder__"),
                _roster_track_status=models.F("track__status"),
            )
            .order_by("responder_id")
        )
        return models.Prefetch("proposals", queryset=rows, to_attr="_roster_entries")

    def roster(self, *, permitted: bool | None = None, status: bool | None = None) -> list[RosterEntry]:
        """Use batched permission answers and prefetched scalar facts on lists."""
        if permitted is None:
            permitted = self.has_access("see_roster")
        if not permitted:
            return []
        if status is None:
            status = self.has_access("roster_status")
        if "_roster_entries" not in self.__dict__:
            models.prefetch_related_objects([self], type(self).roster_prefetch())
        return [
            RosterEntry(row.responder_id, row._roster_name, row._roster_track_status if status else None)
            for row in self._roster_entries
        ]

    def requester_user_id(self) -> Any | None:
        """Resolve the requester through the same person relation as the graph."""
        if self.requester_party_id is None:
            return None
        return (
            system_queryset(apps.get_model("parties", "Person"))
            .filter(
                pk=self.requester_party_id,
            )
            .values_list("user_id", flat=True)
            .first()
        )

    def validate_build_subject(self, subject: Any) -> None:
        """Refuse holders that would admit the requester to build content."""
        if subject_reaches_user(subject, self.requester_user_id()):
            raise RecordAccessSubjectRefused()

    def admit(self, user: models.Model, party: models.Model | None = None, track: bool = False) -> Any:
        """Create the unique shell and optionally its owning track, idempotently."""
        if not self.has_access("write"):
            raise PermissionDenied("Round write access is required.")
        actor = instance_actor(self)
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.admit").lock_if_supported().get(pk=self.pk)
            with system_context(reason="proposals.round.admit.shell"):
                proposal_model = apps.get_model("proposals", "Proposal")
                proposal = proposal_model._base_manager.filter(round=locked, responder=user).first()
                if proposal is None:
                    proposal = proposal_model(round=locked, responder=user, party=party)
                    proposal.save()
                elif proposal.retired_at is not None:
                    raise ValidationError({"responder": "A retired responder cannot be admitted again."})
                elif party is not None and proposal.party_id != party.pk:
                    raise ValidationError({"party": "The shell has a different party."})
                if track:
                    proposal.create_track()
        return proposal.with_actor(actor)

    def remove_responder(self, user: models.Model, expected_revision: int | None = None) -> dict[str, Any]:
        """Retire one shell and clean its direct access, reporting wider shares."""
        if not self.has_access("manage"):
            raise PermissionDenied("Round management is required.")
        groups = set(
            str(pk)
            for pk in apps.get_model("iam", "Group")
            .objects.with_actor(user)
            .with_action("member")
            .values_list("pk", flat=True)
        )
        with transaction.atomic(), system_context(reason="proposals.round.remove_responder"):
            locked = type(self).objects.lock_if_supported().get(pk=self.pk)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            proposal = (
                apps.get_model("proposals", "Proposal")
                .objects.lock_if_supported()
                .filter(
                    round=locked,
                    responder=user,
                )
                .first()
            )
            if proposal is None:
                raise ValidationError({"responder": "This user is not admitted to the round."})
            result: dict[str, Any] = {"removed": [], "reported": []}
            if proposal.retired_at is not None:
                return result
            proposal.retired_at = timezone.now()
            proposal.retired_by_id = _receipt_user_id()
            proposal.allow_immutable_save("retired_at", "retired_by_id")
            proposal.save(update_fields=("retired_at", "retired_by", "updated_at"))
            task_model = apps.get_model("projects", "Task")
            answer_model = apps.get_model("proposals", "Answer")
            collections = [
                (type(self).objects.filter(pk=self.pk), False),
                (apps.get_model("proposals", "Proposal").objects.filter(round=self), False),
                (answer_model.objects.filter(proposal__round=self), False),
                (task_model.objects.filter(clarification_round=self), False),
            ]
            tracks = apps.get_model("projects", "Project").objects.filter(source_proposal__round=self)
            drive_model = apps.get_model("storage", "Drive")
            drives = drive_model.objects.filter(project_bindings__project_id__in=tracks.values("pk"))
            collections.extend(
                [
                    (tracks, True),
                    (task_model.objects.filter(project_id__in=tracks.values("pk")), True),
                    (drives, True),
                    (apps.get_model("storage", "File").objects.filter(drive_id__in=drives.values("pk")), True),
                ]
            )
            subject = to_subject_ref(user)
            relationships = active_relationship_model().objects
            for rows, track_rows in collections:
                if track_rows:
                    OwnerQuerySet.release(rows, user)
                if rows.model is task_model:
                    rows.filter(assignee=user).update(assignee=None)
                resource_type = rows.model._meta.rebac_resource_type
                identifiers = [str(pk) for pk in rows.order_by().values_list("pk", flat=True)]
                shares = relationships.filter(
                    resource_type=resource_type,
                    resource_id__in=identifiers,
                    relation__in=tuple(rows.model.rebac_grantable),
                )
                deletions = set()
                for share in shares:
                    direct = share.subject_type == subject.subject_type and share.subject_id == subject.subject_id
                    group = (
                        share.subject_type == "auth/group"
                        and share.subject_id in groups
                        and share.optional_subject_relation == "member"
                    )
                    wildcard = share.subject_type == "auth/user" and share.subject_id == "*"
                    if not (direct or group or wildcard):
                        continue
                    detail = dict(
                        resource=f"{resource_type}:{share.resource_id}",
                        relation=share.relation,
                        subject=str(
                            SubjectRef.of(
                                share.subject_type,
                                share.subject_id,
                                share.optional_subject_relation,
                            )
                        ),
                    )
                    if direct or (track_rows and group):
                        result["removed"].append(detail)
                        deletions.add(
                            (
                                share.resource_id,
                                share.relation,
                                share.subject_type,
                                share.subject_id,
                                share.optional_subject_relation,
                            )
                        )
                    else:
                        result["reported"].append(detail)
                # The upstream filter accepts one resource identity. Discovery is
                # batched above; deletion retains its audit and consistency owner.
                for identifier, relation, kind, holder, holder_relation in sorted(deletions):
                    delete_relationships(
                        RelationshipFilter(
                            resource_type=resource_type,
                            resource_id=identifier,
                            relation=relation,
                            subject_type=kind,
                            subject_id=holder,
                            optional_subject_relation=holder_relation,
                        )
                    )
            locked.save(update_fields=("updated_at",))
        _adopt(self, locked, ("updated_at", "updated_by"))
        return result

    def route_clarification(self, task: Any, step: str) -> None:
        """Optional integration hook, called before insertion and after passing."""

    def current_clarification_visibility(self) -> str:
        """Snapshot the section audience from the current phase and its boundary."""
        with system_context(reason="proposals.round.clarification_default"):
            project = self.target_project()
            if self.clarifications_shared_until_id is None:
                return "restricted"
            if project.current_milestone_id is None:
                return "inherited"
            return (
                "inherited"
                if project.current_milestone.sort_order <= self.clarifications_shared_until.sort_order
                else "restricted"
            )

    def ask(
        self,
        title: str,
        body: str,
        audience: str = "default",
        recipient: Any = None,
        client_creation_key: str | None = None,
    ) -> Any:
        """Ask one replay-safe question with anonymous content attribution."""
        if not self.has_access("ask"):
            raise PermissionDenied("Round ask access is required.")
        manager = self.has_access("manage")
        asker_id = actor_user_id(instance_actor(self))
        if asker_id is None:
            raise ValidationError("Asking requires a user.")
        if audience not in {"default", "managers"}:
            raise ValidationError({"audience": "Choose default or managers."})
        if not manager and recipient is not None:
            raise ValidationError({"recipient": "Only managers may choose a recipient."})
        if manager and recipient is None:
            raise ValidationError({"recipient": "Choose a user or responders."})
        fingerprint = hashlib.sha256(
            json.dumps(
                dict(
                    title=title,
                    body=body,
                    audience=audience,
                    recipient=str(recipient.pk) if isinstance(recipient, models.Model) else recipient,
                ),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with transaction.atomic(), system_context(reason="proposals.round.ask"):
            locked = type(self).objects.lock_if_supported().get(pk=self.pk)
            task_model = apps.get_model("projects", "Task")
            if client_creation_key:
                previous = task_model.objects.filter(
                    clarification_round=locked,
                    clarification_asker_id=asker_id,
                    clarification_creation_key=client_creation_key,
                ).first()
                if previous is not None:
                    if previous.clarification_creation_fingerprint != fingerprint:
                        raise CreationKeyConflict()
                    return previous
            if not manager and timezone.now() > locked.last_call_at:
                raise ValidationError("The last call for questions has passed.")
            if locked.closed_at is not None:
                raise ValidationError("Questions cannot be asked in a terminal round.")
            project = locked.target_project()
            default = locked.current_clarification_visibility()
            task = task_model(
                title=title,
                note=body,
                project=project,
                parent_id=locked.task_id or (project.converted_from_id if project else None),
                clarification_round=locked,
                clarification_asker_id=asker_id,
                clarification_by_manager=manager,
                clarification_default_visibility=default,
                clarification_surrendered=audience == "managers",
                clarification_creation_key=client_creation_key or None,
                clarification_creation_fingerprint=fingerprint if client_creation_key else "",
                visibility="restricted" if audience == "managers" else default,
                assignee_id=locked.facilitator_id,
            )
            with actor_context(_CLARIFICATION_SYSTEM_ACTOR):
                locked.route_clarification(task, "asked")
                task.sudo(reason="proposals.round.ask.insert").save(ownerless=True)
                if manager:
                    locked._pass_clarification_locked(task, recipient, "default", manager_ask=True)
        return task

    def resolve_clarification(self, task: Any, *, expected_revision: int) -> Any:
        """Resolve one question under manager authority and its observed revision."""

        actor = instance_actor(self)
        with transaction.atomic():
            locked_round = system_queryset(type(self), lock=("self",)).get(pk=self.pk).with_actor(actor)
            if not locked_round.has_access("manage"):
                raise PermissionDenied("Round management is required to resolve a question.")
            locked = system_queryset(type(task), lock=("self",)).get(pk=task.pk, clarification_round=locked_round)
            locked.require_revision(expected_revision)
            if locked.status == "done":
                return locked
            if locked.status != "open":
                raise ValidationError({"task": "Only an open question can be resolved."})
            locked.with_actor(actor).complete()
            return locked

    def edit_clarification(
        self,
        task: Any,
        title: str,
        body: str,
        audience: str | None = None,
        expected_revision: int | None = None,
    ) -> Any:
        """Edit unpassed content under the same anonymous actor as creation."""
        if not self.has_access("ask"):
            raise PermissionDenied("Round ask access is required.")
        manager = self.has_access("manage")
        actor_id = actor_user_id(instance_actor(self))
        with transaction.atomic(), system_context(reason="proposals.round.edit_clarification"):
            type(self).objects.lock_if_supported().get(pk=self.pk)
            locked = type(task).objects.lock_if_supported().get(pk=task.pk, clarification_round=self)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if not manager and actor_id != locked.clarification_asker_id:
                raise PermissionDenied("Only the asker or a round manager may edit this question.")
            if locked.clarification_passed_at is not None:
                raise ValidationError("A passed question's content cannot be edited.")
            if audience not in {None, "managers"}:
                raise ValidationError({"audience": "Only narrowing to managers is allowed while editing."})
            with actor_context(_CLARIFICATION_SYSTEM_ACTOR):
                if audience == "managers":
                    locked.set_visibility("restricted")
                    locked.clarification_surrendered = True
                locked.title, locked.note = title, body
                locked.sudo(reason="proposals.round.edit_clarification.content").save(
                    update_fields=("title", "note", "clarification_surrendered", "updated_at"),
                )
        return locked

    def pass_clarification(
        self,
        task: Any,
        recipient: Any,
        audience: str = "default",
        expected_revision: int | None = None,
    ) -> Any:
        """Assign a question and retain the audience of its first pass."""
        if not self.has_access("manage"):
            raise PermissionDenied("Round management is required.")
        with transaction.atomic(), system_context(reason="proposals.round.pass_clarification"):
            locked_round = type(self).objects.lock_if_supported().get(pk=self.pk)
            locked = type(task).objects.lock_if_supported().get(pk=task.pk, clarification_round=locked_round)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            with actor_context(_CLARIFICATION_SYSTEM_ACTOR):
                locked_round._pass_clarification_locked(locked, recipient, audience)
        return locked

    def _pass_clarification_locked(
        self,
        task: Any,
        recipient: Any,
        audience: str,
        *,
        manager_ask: bool = False,
    ) -> None:
        if audience not in {"default", "asker"}:
            raise ValidationError({"audience": "Choose default or asker."})
        if audience == "default" and task.clarification_surrendered:
            raise ValidationError({"audience": "A surrendered question must be explicitly returned to its asker."})
        if recipient == "responders":
            if not task.clarification_by_manager:
                raise ValidationError({"recipient": "Only a manager's question may address all responders."})
            visibility, assignee_id = "inherited", None
        elif isinstance(recipient, models.Model) and recipient.pk is not None:
            assignee_id = recipient.pk
            visibility = "restricted" if manager_ask or audience == "asker" else task.clarification_default_visibility
            task.validate_record_access_subject("assignee", recipient)
        else:
            raise ValidationError({"recipient": "Choose a saved user or responders."})
        surrendered = False if audience == "asker" else task.clarification_surrendered
        if task.clarification_passed_at is not None:
            if task.clarification_passed_audience != audience:
                raise ValidationError({"audience": "A passed question's audience cannot change through passing."})
            if recipient == "responders" and task.visibility != "inherited":
                raise ValidationError({"audience": "A restricted question cannot be passed to all responders."})
            if task.assignee_id == assignee_id and task.status != "done":
                return
            visibility, surrendered = task.visibility, task.clarification_surrendered
        if task.status == "done":
            task.reopen()
        if task.visibility != visibility:
            task.set_visibility(visibility)
        task.assignee_id = assignee_id
        task.clarification_surrendered = surrendered
        if task.clarification_passed_at is None:
            task.clarification_passed_at = timezone.now()
            task.clarification_passed_audience = audience
            task.allow_immutable_save("clarification_passed_at", "clarification_passed_audience")
        task.sudo(reason="proposals.round.pass_clarification.assignment").save(
            update_fields=(
                "assignee",
                "clarification_surrendered",
                "clarification_passed_at",
                "clarification_passed_audience",
                "updated_at",
            ),
        )
        self.route_clarification(task, "passed")

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
        update_fields = kwargs.get("update_fields")
        boundaries = [
            name
            for name in ("opens_after", "clarifications_shared_until")
            if (update_fields is None or {name, f"{name}_id"}.intersection(update_fields))
            and f"{name}_id" in self.__dict__
        ]
        previous = (
            {}
            if self._state.adding or not boundaries
            else (system_queryset(type(self)).filter(pk=self.pk).values(*(f"{name}_id" for name in boundaries)).get())
        )
        changed_ids = {
            name: getattr(self, f"{name}_id")
            for name in boundaries
            if getattr(self, f"{name}_id") is not None
            and (self._state.adding or previous[f"{name}_id"] != getattr(self, f"{name}_id"))
        }
        if changed_ids:
            project = self.target_project()
            valid = set(
                system_queryset(apps.get_model("projects", "Milestone"))
                .filter(
                    pk__in=changed_ids.values(),
                    project_id=project.pk if project else None,
                )
                .values_list("pk", flat=True)
            )
            for name, identifier in changed_ids.items():
                if identifier not in valid:
                    raise ValidationError({name: "The milestone must belong to the target project."})
        if self.pk is not None and not self._state.adding:
            # Opening-policy immutability depends on the committed lifecycle state.
            with system_context(reason="proposals.round.opening_policy"):
                persisted = type(self)._base_manager.filter(pk=self.pk).values("status", "opening_policy").first()
            if persisted is not None and persisted["status"] == RoundStatus.COLLECTING:
                self.allow_immutable_save("opening_policy")
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

    def open(self, expected_revision: int | None = None) -> Self:
        """Stamp disclosure once, in round-first lock order."""
        if not self.has_access("write"):
            raise PermissionDenied("Round write access is required.")
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.open").lock_if_supported().get(pk=self.pk)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.status == RoundStatus.COLLECTING:
                if not locked.opening_phase_ready():
                    raise ValidationError({"opens_after": "The current phase must follow the opening boundary."})
                locked._mark_opened()
                locked._disclose()
            elif locked.status != RoundStatus.OPENED:
                locked.status_transitions.not_allowed(locked.status, RoundStatus.OPENED)
        _adopt(self, locked, ("status", "opened_at", "opened_by", "updated_at", "updated_by"))
        return self

    def _disclose(self) -> None:
        """Apply the configured audience by stamping eligible receipt columns."""
        if self.opening_policy == RoundOpeningPolicy.FACILITATOR_ONLY:
            return
        proposals = (
            apps.get_model("proposals", "Proposal")
            .objects.sudo(
                reason="proposals.round.disclose",
            )
            .lock_if_supported()
            .filter(round=self, retired_at__isnull=True)
            .exclude(state=ProposalState.WITHDRAWN)
            .order_by("pk")
        )
        if self.opening_policy != RoundOpeningPolicy.DRAFTS_AND_TRACKS:
            proposals = proposals.filter(state=ProposalState.SUBMITTED)
        for proposal in proposals:
            changed = []
            if proposal.disclosed_at is None:
                proposal.disclosed_at = self.opened_at
                changed.append("disclosed_at")
            if self.opening_policy in {RoundOpeningPolicy.ANSWERS_AND_TRACKS, RoundOpeningPolicy.DRAFTS_AND_TRACKS}:
                if proposal.track_id is not None and proposal.track_published_at is None:
                    proposal.track_published_at = self.opened_at
                    changed.append("track_published_at")
            if changed:
                proposal.allow_immutable_save(*changed)
                proposal.save(update_fields=(*changed, "updated_at"))

    def widen_opening_policy(self, policy: str, expected_revision: int | None = None) -> Self:
        """Widen an opened round without retracting any disclosure receipt."""
        if not self.has_access("write"):
            raise PermissionDenied("Round write access is required.")
        try:
            policy = RoundOpeningPolicy(policy)
        except ValueError as error:
            raise ValidationError({"opening_policy": "Unknown opening policy."}) from error
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.widen").lock_if_supported().get(pk=self.pk)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            order = OPENING_POLICY_ORDER
            if locked.status != RoundStatus.OPENED or order.index(policy) < order.index(locked.opening_policy):
                raise ValidationError({"opening_policy": "Only an opened round's policy may be widened."})
            if locked.opening_policy != policy:
                locked.opening_policy = policy
                locked.allow_immutable_save("opening_policy")
                locked.save(update_fields=("opening_policy", "updated_at"))
                locked._disclose()
        _adopt(self, locked, ("opening_policy", "updated_at", "updated_by"))
        return self

    def close(
        self,
        outcome: str | RoundOutcome,
        *,
        accepted: Iterable[models.Model] = (),
        partial: Iterable[models.Model] = (),
        expected_revision: int | None = None,
    ) -> Self:
        """Close with an exact decision over every submitted Proposal."""

        if not self.has_access("write"):
            raise PermissionDenied("Round write access is required.")

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
            if expected_revision is not None:
                locked.require_revision(expected_revision)
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

    def cancel(self, expected_revision: int | None = None) -> Self:
        """Cancel a collecting or opened Round, preserving a null outcome."""

        if not self.has_access("write"):
            raise PermissionDenied("Round write access is required.")

        if self.pk is None:
            raise ValidationError("A saved round is required.")
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.cancel").lock_if_supported().get(pk=self.pk)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
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

    def transfer_facilitation(self, user: models.Model, expected_revision: int | None = None) -> Self:
        """Transfer the manager seat without duplicating it in track grants."""
        if not self.has_access("write"):
            raise PermissionDenied("Round write access is required.")
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.round.transfer").lock_if_supported().get(pk=self.pk)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.status in {RoundStatus.CLOSED, RoundStatus.CANCELLED}:
                raise ValidationError({"facilitator": "Terminal rounds cannot transfer facilitation."})
            if apps.get_model("proposals", "Proposal").system_queryset().filter(round=locked, responder=user).exists():
                raise ValidationError({"facilitator": "A responder cannot become the facilitator."})
            if locked.facilitator_id != user.pk:
                locked.facilitator = user
                locked.allow_immutable_save("facilitator_id")
                locked.save(update_fields=("facilitator", "updated_at"))
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

        cast(Any, self).outcome = None
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
        actual_partial = {proposal.pk for proposal in proposals if proposal.state == ProposalState.PARTIALLY_ACCEPTED}
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
                type(round).objects.sudo(reason="proposals.capture.round").lock_if_supported().get(pk=round.pk)
            )
            if locked_round.status != RoundStatus.COLLECTING:
                raise ValidationError({"round": "Replies cannot be captured after opening."})
            locked_message = (
                type(message).objects.sudo(reason="proposals.capture.message").lock_if_supported().get(pk=message.pk)
            )
            resolved_party = party or self._resolved_sender_party(locked_message)
            if resolved_party is None:
                raise ValidationError({"party": "Capture requires a party or a resolved sender party."})
            interpretation = self._interpret(locked_message, locked_round)
            payload_hash = self._payload_hash(locked_message)
            proposal = self._locked_capture_proposal(message=locked_message, round=locked_round, party=resolved_party)
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
                proposal, message=locked_message, interpretation=interpretation, payload_hash=payload_hash
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
            .filter(models.Q(source_message_id=message.pk) | models.Q(round_id=round.pk, party_id=party.pk))
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
                .filter(models.Q(source_message_id=message.pk) | models.Q(round_id=round.pk, party_id=party.pk))
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
            ),
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
        handle: Any = message.sender
        if handle.party_id is None:
            apps.get_model("parties", "PartyHandle").objects.suggest_for(handle)
            # Matching may assign the party through another Handle instance.
            handle.refresh_from_db(fields=["party"])
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


class Proposal(OptimisticLockMixin, ImmutableFieldsMixin, AuditMixin, AngeeDataModel):
    """One responder's structured response; its private work lives on ``track``."""

    runtime = True
    sqid_prefix = "prp_"
    rebac_grantable = {"reader": "share"}
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
        "disclosed_at",
        "track_published_at",
        "retired_at",
        "retired_by_id",
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
    disclosed_at = models.DateTimeField(null=True, blank=True, editable=False)
    track_published_at = models.DateTimeField(null=True, blank=True, editable=False)
    retired_at = models.DateTimeField(null=True, blank=True, editable=False)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="retired_proposals",
    )
    statement = models.TextField(blank=True, default="")
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
        """Admit shells through manager/requester invariants and stamp late disclosure."""

        if not self._state.adding:
            super().save(*args, **kwargs)
            return
        with transaction.atomic():
            locked_round = (
                apps.get_model("proposals", "Round")
                .objects.sudo(reason="proposals.proposal.create.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            if (
                not system_queryset(type(locked_round))
                .filter(
                    type(locked_round).admission_condition(),
                    pk=locked_round.pk,
                )
                .exists()
            ):
                raise ValidationError({"round": "This round is not accepting proposal shells."})
            self.round = locked_round
            if self.responder_id is not None:
                subject = SubjectRef.of("auth/user", str(self.responder_id))
                if (
                    backend()
                    .check_access(subject=subject, action="manage", resource=to_object_ref(locked_round))
                    .allowed
                ):
                    raise ValidationError({"responder": "A round manager cannot be a responder."})
                self.validate_record_access_subject("responder", subject)
            if locked_round.status == RoundStatus.OPENED:
                self.disclosed_at = timezone.now()
            super().save(*args, **kwargs)

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """Apply the round's holder invariant to shells and explicit shares."""
        super().validate_record_access_subject(relation, subject)
        with system_context(reason="proposals.proposal.subject"):
            self.round.validate_build_subject(subject)

    @classmethod
    def track_status_expression(cls, actor: Any) -> models.Expression:
        """Project the gated track scalar in the proposal list query."""
        if actor is None:
            return models.Value(None, output_field=models.CharField())
        rows = cls.objects.with_actor(actor).with_action("read_track_status").scoped_for_aggregate()
        return models.Subquery(rows.filter(pk=models.OuterRef("pk")).values("track__status")[:1])

    def track_status(self) -> str | None:
        """Read the scalar through the same gated expression as a list."""
        return (
            system_queryset(type(self))
            .filter(pk=self.pk)
            .annotate(
                _track_status=type(self).track_status_expression(instance_actor(self)),
            )
            .values_list("_track_status", flat=True)
            .get()
        )

    def deletion_error(self) -> str | None:
        """Return why this Proposal is no longer an untouched draft."""

        if self.state != ProposalState.DRAFT or any(
            value is not None for value in (self.submitted_at, self.decided_at, self.retired_at, self.disclosed_at)
        ):
            return "Only an untouched draft proposal can be deleted."
        if self.source_message_id is not None or self.track_id is not None:
            return "Captured or tracked proposals cannot be deleted."
        if any(
            value not in (None, "")
            for value in (
                self.cost,
                self.currency_id,
                self.staffing,
                self.statement,
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

    def submit(self, expected_revision: int | None = None) -> Self:
        """Submit under the round-first lock, freezing draft writes by state."""

        if not self.has_access("write"):
            raise PermissionDenied("Proposal write access is required.")
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
                type(self)
                .objects.sudo(reason="proposals.proposal.submit")
                .lock_if_supported()
                .filter(round_id=locked_round.pk, pk=self.pk)
                .order_by("pk")
                .get()
            )
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.state == ProposalState.SUBMITTED:
                locked._assert_submitted_postcondition()
            elif locked.state != ProposalState.DRAFT:
                locked.state_transitions.not_allowed(locked.state, ProposalState.SUBMITTED)
            elif locked_round.status != RoundStatus.COLLECTING and not (
                locked_round.status == RoundStatus.OPENED
                and locked_round.opening_policy == RoundOpeningPolicy.DRAFTS_AND_TRACKS
            ):
                raise ValidationError({"round": "Proposals cannot be submitted after opening."})
            else:
                locked._submit_locked(fallback_user_id=locked.responder_id or locked_round.facilitator_id)
        _adopt(self, locked, ("state", "submitted_at", "submitted_by", "updated_at", "updated_by"))
        return self

    def withdraw(self, expected_revision: int | None = None) -> Self:
        """Withdraw one submitted Proposal under the Round-first lock order."""

        if not self.has_access("withdraw"):
            raise PermissionDenied("Proposal withdraw access is required.")
        if self.pk is None:
            raise ValidationError("A saved proposal is required.")
        with transaction.atomic(), system_context(reason="proposals.proposal.withdraw"):
            round_model = apps.get_model("proposals", "Round")
            locked_round = (
                round_model.objects.sudo(reason="proposals.proposal.withdraw.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            locked = (
                type(self)
                .objects.sudo(reason="proposals.proposal.withdraw")
                .lock_if_supported()
                .filter(round_id=locked_round.pk, pk=self.pk)
                .order_by("pk")
                .get()
            )
            if expected_revision is not None:
                locked.require_revision(expected_revision)
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
                type(self)
                .objects.sudo(reason="proposals.proposal.identify")
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
        """Create one ownerless, item-owning project with its own bound drive."""

        if not self.has_access("write"):
            raise PermissionDenied("Proposal write access is required.")
        if self.pk is None:
            raise ValidationError("A saved proposal is required.")
        actor = instance_actor(self)
        project_model = apps.get_model("projects", "Project")
        with transaction.atomic():
            round_model = apps.get_model("proposals", "Round")
            locked_round = (
                round_model.objects.sudo(reason="proposals.proposal.create_track.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            locked = (
                type(self)
                .objects.sudo(reason="proposals.proposal.create_track")
                .lock_if_supported()
                .filter(round_id=locked_round.pk, pk=self.pk)
                .order_by("pk")
                .get()
            )
            if locked.track_id is not None:
                track: Any = locked.track
            else:
                track = project_model(
                    title=f"{locked_round.name} — proposal track",
                    body="Private proposal delivery track.",
                    lead_id=locked.responder_id or locked_round.facilitator_id,
                    owns_items=True,
                )
                with actor_context(_TRACK_SYSTEM_ACTOR), system_context(reason="proposals.proposal.create_track"):
                    track.save(ownerless=True)
                locked.track = track
                locked.allow_immutable_save("track_id")
                locked.save(update_fields=("track", "updated_at"))
                if (
                    locked_round.status == RoundStatus.OPENED
                    and locked_round.opening_policy
                    in {
                        RoundOpeningPolicy.DRAFTS_AND_TRACKS,
                        RoundOpeningPolicy.ANSWERS_AND_TRACKS,
                    }
                    and locked.disclosed_at is not None
                ):
                    locked._publish_track_locked()
            with system_context(reason="proposals.proposal.track_drive"):
                track = system_queryset(project_model).get(pk=track.pk)
                drive_model = apps.get_model("storage", "Drive")
                slug = f"proposal-{locked.pk}"
                drive = drive_model._base_manager.filter(
                    slug=slug,
                    project_bindings__project=track,
                ).first()
                if drive is None:
                    with actor_context(_TRACK_SYSTEM_ACTOR):
                        drive = drive_model.objects.create_on_default_backend(
                            slug=slug,
                            name=track.title,
                            owns_items=True,
                        )
                    drive = system_queryset(drive_model).get(pk=drive.pk)
                    bind(project=track, target=drive)
        bind_actor(track, actor)
        _adopt(self, locked, ("track", "track_published_at", "updated_at", "updated_by"))
        self._state.fields_cache["track"] = track
        return track

    def publish_track(self, expected_revision: int | None = None) -> models.Model:
        """Publish the private track to this Round's responders, idempotently."""

        if not self.has_access("publish"):
            raise PermissionDenied("Proposal publish access is required.")
        if self.pk is None:
            raise ValidationError("A saved proposal is required.")
        with transaction.atomic():
            round_model = apps.get_model("proposals", "Round")
            locked_round = (
                round_model.objects.sudo(reason="proposals.proposal.publish_track.round")
                .lock_if_supported()
                .get(pk=self.round_id)
            )
            locked = (
                type(self)
                .objects.sudo(reason="proposals.proposal.publish_track.proposals")
                .lock_if_supported()
                .get(round_id=locked_round.pk, pk=self.pk)
            )
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            track = locked._publish_track_locked()
        _adopt(self, locked, ("track_published_at", "updated_at", "updated_by"))
        return track

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
        self._mark_submitted(fallback_user_id)

    def _assert_submitted_postcondition(self) -> None:
        if self.submitted_at is None or self.submitted_by_id is None or self.decided_at is not None:
            raise ValidationError("Submitted proposal receipts do not match the requested postcondition.")

    def _publish_track_locked(self) -> models.Model:
        if self.track_id is None:
            raise ValidationError({"track": "Create the proposal track before publishing it."})
        if self.track_published_at is None:
            self.track_published_at = timezone.now()
            self.allow_immutable_save("track_published_at")
            self.save(update_fields=("track_published_at", "updated_at"))
        return self.track

    def _validate_lifecycle_receipts(self) -> None:
        if self.state == ProposalState.DRAFT and any(
            value is not None
            for value in (self.submitted_at, self.submitted_by_id, self.decided_at, self.decided_by_id)
        ):
            raise ValidationError("A draft proposal cannot carry lifecycle receipts.")
        if self.state != ProposalState.DRAFT and (self.submitted_at is None or self.submitted_by_id is None):
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


class ProjectProposalAccess(models.Model):
    """Grant proposal visibility from one Project without owning its policy."""

    extends = "projects.Project"
    runtime = False
    rebac_grantable = {"proposal_viewer": "share"}

    def apply_setup(self, *, round: Mapping[str, Any] | None = None, **options: Any) -> None:
        """Provision the declared round after the project's template acts."""

        if not isinstance(round, Mapping):
            raise ValidationError({"round": "Declare round setup choices."})
        super().apply_setup(**options)
        values = dict(round)
        template = values.pop("template")
        facilitator = values.pop("facilitator")
        responders = values.pop("responders")
        team = values.pop("team", None)
        requester = values.pop("requester_party", None)
        apps.get_model("proposals", "Round").objects.provision(
            self, template, facilitator, responders, team,
            requester_party=requester, configuration=values,
        )

    @classmethod
    def setup_complete_condition(cls, actor: Any) -> models.Q:
        """A proposal-enabled project also requires a configured readable round."""

        round_model = apps.get_model("proposals", "Round")
        rounds = round_model.objects.with_actor(actor).scoped().filter(
            round_model.setup_complete_condition(actor), project_id=models.OuterRef("pk"),
        )
        return super().setup_complete_condition(actor) & models.Q(models.Exists(rounds))

    @classmethod
    def question_attention_expression(cls, actor: Any, *, passed_on: bool = False) -> models.Expression:
        """Count readable unanswered questions on this project."""

        return apps.get_model("projects", "Task").question_count_expression(
            actor, scope=models.Q(project_id=models.OuterRef("pk")), passed_on=passed_on,
        )

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """Keep the requester's ceiling on track projects."""
        super().validate_record_access_subject(relation, subject)
        with system_context(reason="proposals.track.subject"):
            round = apps.get_model("proposals", "Round").objects.for_track(self.pk)
            if round is not None:
                round.validate_build_subject(subject)

    def selectable_milestones(self) -> models.QuerySet:
        """Limit unopened rounds to the boundary and its immediate successor."""
        choices = super().selectable_milestones()
        with system_context(reason="proposals.track.phase_choices"):
            rounds = (
                apps.get_model("proposals", "Round")
                ._base_manager.filter(
                    models.Q(project_id=self.pk) | models.Q(task__project_id=self.pk),
                    opened_at__isnull=True,
                    closed_at__isnull=True,
                    opens_after__isnull=False,
                )
                .select_related("opens_after")
            )
            for round in rounds:
                next_rank = (
                    choices.filter(sort_order__gt=round.opens_after.sort_order)
                    .order_by(
                        "sort_order",
                    )
                    .values_list("sort_order", flat=True)
                    .first()
                )
                choices = choices.filter(
                    sort_order__lte=round.opens_after.sort_order if next_rank is None else next_rank,
                )
        return choices

    class Meta:
        """Abstract zero-column donor folded into the composed Project model."""

        abstract = True


class TaskProposalAccess(ImmutableFieldsMixin):
    """Contribute question facts and track-item sharing to the task owner."""

    extends = "projects.Task"
    runtime = False
    rebac_grantable = {"proposal_viewer": "share"}
    clarification_round = models.ForeignKey(
        "proposals.Round",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.CASCADE,
        related_name="clarifications",
    )
    clarification_asker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="asked_clarifications",
    )
    clarification_by_manager = models.BooleanField(default=False, editable=False)
    clarification_surrendered = models.BooleanField(default=False, editable=False)
    clarification_default_visibility = models.CharField(max_length=20, default="restricted", editable=False)
    clarification_passed_at = models.DateTimeField(null=True, blank=True, editable=False)
    clarification_passed_audience = models.CharField(max_length=16, default="", blank=True, editable=False)
    clarification_creation_key = models.CharField(max_length=255, null=True, blank=True, editable=False)
    clarification_creation_fingerprint = models.CharField(max_length=64, blank=True, default="", editable=False)
    shared_with_responders = models.BooleanField(default=False, editable=False)
    hasura_readable_fields = (
        "clarification_round",
        "clarification_asker",
        "clarification_by_manager",
        "clarification_surrendered",
        "clarification_default_visibility",
        "clarification_passed_at",
        "shared_with_responders",
    )
    hasura_filterable_fields = (
        "clarification_round",
        "clarification_passed_at",
        "clarification_by_manager",
        "clarification_surrendered",
        "shared_with_responders",
    )
    hasura_groupable_fields = ("clarification_round", "clarification_passed_at")
    hasura_aggregatable_fields = ("clarification_round", "clarification_passed_at")

    immutable_fields = (
        "clarification_round_id",
        "clarification_asker_id",
        "clarification_by_manager",
        "clarification_default_visibility",
        "clarification_passed_at",
        "clarification_passed_audience",
        "clarification_creation_key",
        "clarification_creation_fingerprint",
        "shared_with_responders",
    )

    class Meta:
        """Question replay identity is independent of anonymous task attribution."""

        abstract = True
        constraints = (
            models.UniqueConstraint(
                fields=("clarification_round", "clarification_asker", "clarification_creation_key"),
                condition=models.Q(clarification_creation_key__isnull=False),
                name="uq_proposals_clarification_creation_key",
            ),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Reject content changes after the first pass, including generated writes."""
        if self._state.adding and self.shared_with_responders:
            actor = instance_actor(self)
            round = apps.get_model("proposals", "Round").objects.for_track(self.project_id)
            if (
                round is None
                or actor is None
                or not backend().check_access(subject=actor, action="manage", resource=to_object_ref(round)).allowed
            ):
                raise PermissionDenied("Only a round manager may insert a task shared with responders.")
        update_fields = kwargs.get("update_fields")
        content = {"title", "note"}.intersection(update_fields if update_fields is not None else self.__dict__)
        if not self._state.adding and content and self.clarification_round_id is not None:
            with system_context(reason="proposals.clarification.content"):
                previous = (
                    type(self)
                    ._base_manager.filter(pk=self.pk)
                    .values(
                        "clarification_passed_at",
                        *sorted(content),
                    )
                    .get()
                )
                if previous["clarification_passed_at"] is not None and any(
                    previous[name] != getattr(self, name) for name in content
                ):
                    raise ValidationError("A passed question's content cannot be edited.")
        super().save(*args, **kwargs)

    @classmethod
    def _hidden_clarification_asker_condition(cls) -> models.Q:
        return models.Q(
            clarification_round__clarification_askers=ClarificationAskers.HIDDEN,
            clarification_by_manager=False,
            clarification_asker_id__isnull=False,
        )

    @classmethod
    def clarification_widen_blocker_expression(cls) -> models.Expression:
        """Compute the publication blocker; callers must authorize its disclosure.

        Keep the hidden asker's identity inside the model-owned system query.
        The caller projects only the blocker, never a gated column from its
        actor-scoped outer row.
        """
        messages = cls.thread_messages_expression(models.OuterRef("pk")).filter(
            created_by_id=models.OuterRef("clarification_asker_id"),
        )
        blocker = models.Case(
            models.When(
                cls._hidden_clarification_asker_condition() & models.Q(models.Exists(messages)),
                then=models.Value("hidden_asker_message"),
            ),
            default=models.Value(None),
            output_field=models.CharField(),
        )
        return models.Subquery(
            system_queryset(cls).filter(pk=models.OuterRef("pk")).annotate(_blocker=blocker).values("_blocker"),
        )

    @classmethod
    def visibility_blockers(cls, value: str) -> tuple[tuple[models.Q, type[ValidationError]], ...]:
        """Keep question publication rules on the task verb's shared predicate seam."""
        blockers = super().visibility_blockers(value)
        if value == "restricted":
            return (*blockers, (models.Q(
                clarification_round__isnull=False, visibility="inherited", clarification_passed_at__isnull=False,
            ), PublishedQuestion))
        if value == "inherited":
            return (*blockers, (models.Q(Exact(
                cls.clarification_widen_blocker_expression(), models.Value("hidden_asker_message"),
            )), ClarificationWidenBlocked))
        return blockers

    def _message_post(self, body: str, **kwargs: Any) -> models.Model:
        """Serialize user comments and notes with publication on the task row.

        Automatic system logs use messaging's separate native write path. Keep
        the original record's actor for both authorization and attribution.
        """
        if self.clarification_round_id is None:
            return super()._message_post(body, **kwargs)
        with transaction.atomic():
            locked = (
                system_queryset(type(self), lock=("self",))
                .annotate(
                    _hidden_asker=models.Case(
                        models.When(self._hidden_clarification_asker_condition(), then="clarification_asker_id"),
                        default=models.Value(None),
                    ),
                )
                .get(pk=self.pk)
            )
            poster_id = actor_user_id(instance_actor(self))
            if locked.visibility == "inherited" and poster_id is not None and poster_id == locked._hidden_asker:
                raise ClarificationWidenBlocked()
            return super()._message_post(body, **kwargs)

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """Keep the requester's ceiling on track items; other tasks and questions stay native.

        Only a task on a responder's track asks the round. A question or any task
        on the round's own target project never consults that project's holder
        policy, which stays that project's business.
        """
        super().validate_record_access_subject(relation, subject)
        with system_context(reason="proposals.task.subject"):
            round = apps.get_model("proposals", "Round").objects.for_track(self.project_id)
            if round is not None:
                round.validate_build_subject(subject)

    def set_responder_share(self, shared: bool, expected_revision: int | None = None) -> Any:
        """Share a track item with every current responder or clear that audience."""
        if not self.has_access("widen" if shared else "narrow"):
            raise PermissionDenied("The task audience permission is required.")
        with transaction.atomic(), system_context(reason="proposals.task.responder_share"):
            locked = type(self).objects.lock_if_supported().get(pk=self.pk)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if apps.get_model("proposals", "Round").objects.for_track(locked.project_id) is None:
                raise ValidationError("Responder sharing requires a task on a proposal track.")
            if locked.shared_with_responders != shared:
                locked.shared_with_responders = shared
                locked.allow_immutable_save("shared_with_responders")
                locked.save(update_fields=("shared_with_responders", "updated_at"))
        _adopt(self, locked, ("shared_with_responders", "updated_at", "updated_by"))
        return self

    @classmethod
    def clarification_waiting_users(cls, actor: Any) -> models.QuerySet:
        """Own the unanswered-recipient predicate used by projections and counts."""
        round_model = apps.get_model("proposals", "Round")
        proposal_model = apps.get_model("proposals", "Proposal")
        managed = round_model.objects.with_actor(actor).with_action("manage").scoped()
        with system_context(reason="proposals.clarification.waiting"):
            current = proposal_model._base_manager.filter(
                round_id=models.OuterRef("_question_round"),
                responder_id=models.OuterRef("pk"),
                retired_at__isnull=True,
            )
            messages = cls.thread_messages_expression(models.OuterRef("_question_id")).filter(
                created_by_id=models.OuterRef("pk"),
            )
            users = (
                user_label_queryset()
                .annotate(
                    **{
                        alias: models.ExpressionWrapper(
                            models.OuterRef(field),
                            output_field=cls._meta.pk if field == "pk" else cls._meta.get_field(field),
                        )
                        for alias, field in (
                            ("_question_id", "pk"),
                            ("_question_round", "clarification_round"),
                            ("_question_assignee", "assignee"),
                            ("_manager_question", "clarification_by_manager"),
                            ("_passed", "clarification_passed_at"),
                        )
                    },
                )
                .filter(_question_round__isnull=False)
                .filter(
                    models.Q(pk=models.F("_question_assignee"))
                    | (
                        models.Q(_question_assignee__isnull=True, _manager_question=True, _passed__isnull=False)
                        & models.Q(models.Exists(current))
                    ),
                )
                .filter(
                    models.Q(models.Exists(managed.filter(pk=models.OuterRef("_question_round"))))
                    | models.Q(pk=actor_user_id(actor)),
                )
                .filter(~models.Exists(messages))
                .order_by("pk")
                .annotate(
                    _recipient=JSONObject(
                        id="pk",
                        name=user_label_expression(),
                    ),
                )
            )
            return users if actor is not None else users.none()

    @classmethod
    def clarification_waiting_expression(cls, actor: Any) -> models.Expression:
        """Project the same unanswered recipients in the task's SQL query."""

        if actor is None:
            return models.Value([], output_field=models.JSONField())
        return ClarificationWaitingSubquery(cls.clarification_waiting_users(actor).values("_recipient"))

    @classmethod
    def question_count_expression(cls, actor: Any, *, scope: models.Q, passed_on: bool) -> models.Expression:
        """Count questions, not recipients, intersecting the native task read scope."""

        if actor is None:
            return models.Value(0)
        waiting = cls.clarification_waiting_users(actor)
        if not passed_on:
            waiting = waiting.filter(pk=actor_user_id(actor))
        questions = cls.objects.with_actor(actor).scoped().filter(
            scope, status="open", clarification_round__isnull=False,
        ).filter(models.Exists(waiting))
        if passed_on:
            managed = apps.get_model("proposals", "Round").objects.with_actor(actor).with_action("manage").scoped()
            questions = questions.filter(
                models.Exists(managed.filter(pk=models.OuterRef("clarification_round_id"))),
                clarification_passed_at__isnull=False,
            )
        return questions.readable_count_subquery(actor=actor)

    @classmethod
    def question_attention_expression(cls, actor: Any, *, passed_on: bool = False) -> models.Expression:
        """Include this question or the questions filed under this source task."""

        return cls.question_count_expression(
            actor, scope=models.Q(parent_id=models.OuterRef("pk")) | models.Q(pk=models.OuterRef("pk")),
            passed_on=passed_on,
        )

    def clarification_waiting(self) -> list[dict[str, Any]]:
        """Read the optimized projection, with the same SQL for a standalone row."""
        if "_clarification_waiting" in self.__dict__:
            return self.__dict__["_clarification_waiting"]
        expression = type(self).clarification_waiting_expression(instance_actor(self))
        with system_context(reason="proposals.clarification.waiting.row"):
            return (
                type(self)
                ._base_manager.filter(pk=self.pk)
                .annotate(
                    _clarification_waiting=expression,
                )
                .values_list("_clarification_waiting", flat=True)
                .get()
            )


class DriveProposalAccess(models.Model):
    """Apply the build-content holder invariant through project bindings."""

    extends = "storage.Drive"
    runtime = False

    class Meta:
        abstract = True

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """Ask every bound track's round before granting this drive."""
        super().validate_record_access_subject(relation, subject)
        with system_context(reason="proposals.drive.subject"):
            rounds = (
                apps.get_model("proposals", "Round")
                ._base_manager.filter(
                    proposals__track__resource_bindings__content_type=ContentType.objects.get_for_model(
                        canonical_record_model(type(self)),
                    ),
                    proposals__track__resource_bindings__object_id=self.pk,
                )
                .distinct()
            )
            for round in rounds:
                round.validate_build_subject(subject)


class FileProposalAccess(models.Model):
    """Keep file grants within the same holder ceiling as their track drive."""

    extends = "storage.File"
    runtime = False

    class Meta:
        abstract = True

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """Delegate the invariant through the file's owning drive."""
        super().validate_record_access_subject(relation, subject)
        with system_context(reason="proposals.file.subject"):
            self.drive.validate_record_access_subject(relation, subject)


ProposalsRole = role_anchor("proposals/role")
"""Table-less REBAC anchor for global proposal-viewer membership."""


class Answer(OptimisticLockMixin, ImmutableFieldsMixin, AuditMixin, AngeeDataModel):
    """One Proposal response aligned to one Topic in the same Round."""

    runtime = True
    sqid_prefix = "ans_"
    immutable_fields = ("proposal_id", "topic_id", "visibility", "shared_with_responders")
    rebac_grantable = {"reader": "manage"}

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
    visibility = StateField(choices_enum=AnswerVisibility, default=AnswerVisibility.ROUND)
    shared_with_responders = models.BooleanField(default=False, editable=False)

    def validate_record_access_subject(self, relation: str, subject: Any) -> None:
        """Apply the same requester ceiling as the proposal."""
        super().validate_record_access_subject(relation, subject)
        with system_context(reason="proposals.answer.subject"):
            self.proposal.validate_record_access_subject(relation, subject)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Require round management for an initial responder-wide audience."""
        if self._state.adding and self.shared_with_responders:
            actor = instance_actor(self)
            proposal = (
                system_queryset(apps.get_model("proposals", "Proposal"))
                .select_related("round")
                .get(
                    pk=self.proposal_id,
                )
            )
            if (
                actor is None
                or not backend()
                .check_access(
                    subject=actor,
                    action="manage",
                    resource=to_object_ref(proposal.round),
                )
                .allowed
            ):
                raise PermissionDenied("Only a round manager may insert an answer shared with responders.")
        super().save(*args, **kwargs)

    def allowed_visibility(self, *, narrow: bool | None = None, manage: bool | None = None) -> list[str]:
        """Use the verb's ordered audience contract for both choices and writes."""
        narrow = self.has_access("narrow") if narrow is None else narrow
        manage = self.has_access("manage") if manage is None else manage
        if not narrow:
            return []
        choices = ANSWER_VISIBILITY_ORDER if manage else ANSWER_VISIBILITY_ORDER[
            ANSWER_VISIBILITY_ORDER.index(self.visibility):
        ]
        return [str(value) for value in choices]

    def set_visibility(self, value: str, expected_revision: int | None = None) -> Self:
        """Narrow the inherited audience, or let a manager select any audience."""
        if value not in AnswerVisibility.values:
            raise ValidationError({"visibility": "Unknown answer audience."})
        if not self.has_access("narrow"):
            raise PermissionDenied("Answer narrow access is required.")
        manager = self.has_access("manage")
        with transaction.atomic():
            locked = type(self).objects.sudo(reason="proposals.answer.visibility").lock_if_supported().get(pk=self.pk)
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if value not in locked.allowed_visibility(narrow=True, manage=manager):
                raise PermissionDenied("Only a manager may widen an answer.")
            if locked.visibility != value:
                locked.visibility = value
                locked.allow_immutable_save("visibility")
                locked.save(update_fields=("visibility", "updated_at"))
        _adopt(self, locked, ("visibility", "updated_at", "updated_by"))
        return self

    def set_responder_share(self, shared: bool, expected_revision: int | None = None) -> Self:
        """Set one live responder audience without per-person shares."""
        if not self.has_access("manage" if shared else "narrow"):
            raise PermissionDenied("The answer audience permission is required.")
        with transaction.atomic():
            locked = (
                type(self).objects.sudo(reason="proposals.answer.responder_share").lock_if_supported().get(pk=self.pk)
            )
            if expected_revision is not None:
                locked.require_revision(expected_revision)
            if locked.shared_with_responders != shared:
                locked.shared_with_responders = shared
                locked.allow_immutable_save("shared_with_responders")
                locked.save(update_fields=("shared_with_responders", "updated_at"))
        _adopt(self, locked, ("shared_with_responders", "updated_at", "updated_by"))
        return self

    class Meta:
        """Django options for topic-aligned answers."""

        abstract = True
        ordering = ("topic", "proposal", "sqid")
        rebac_resource_type = "proposals/answer"
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
