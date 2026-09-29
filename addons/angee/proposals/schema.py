"""GraphQL resources and authored verbs for sealed proposal rounds."""

from __future__ import annotations

from functools import partial
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import current_actor
from strawberry import auto
from strawberry.experimental.pydantic import input as pydantic_input
from strawberry.scalars import JSON

from angee.base.mixins import CreationKeyConflict, StaleRevisionError
from angee.graphql.actions import (
    ActionResult,
    ActionSelectionInput,
    action_guard,
    authorized_action_target,
    authorized_permission_target,
    many_actions,
)
from angee.graphql.capabilities import held_permissions, permission_annotations, permissions_field
from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    declared_hasura_resource_fields,
    declared_hasura_write_relation_fields,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.ids import PublicID, optional_public_id
from angee.graphql.inputs import InputReference
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.audit import AuthoredRefMixin
from angee.iam.identity import user_public_id
from angee.iam.schema import UserType
from angee.messaging.schema import MessageType
from angee.money.schema import CurrencyType
from angee.parties.schema import PartyType
from angee.projects.schema import MilestoneType, ProjectType, TaskType
from angee.proposals.inputs import RoundTemplate, TopicTemplate
from angee.proposals.models import AnswerVisibility, PassAudience, QuestionAudience, RoundOpeningPolicy
from angee.spaces.schema import SpaceGroupType

Round = apps.get_model("proposals", "Round")
Topic = apps.get_model("proposals", "Topic")
Proposal = apps.get_model("proposals", "Proposal")
Answer = apps.get_model("proposals", "Answer")
Review = apps.get_model("proposals", "Review")
Project = apps.get_model("projects", "Project")
Task = apps.get_model("projects", "Task")
Party = apps.get_model("parties", "Party")
Message = apps.get_model("messaging", "Message")
Currency = apps.get_model("money", "Currency")
Team = apps.get_model("spaces", "Group")
Milestone = apps.get_model("projects", "Milestone")
User = get_user_model()

RoundOutcome = Round._meta.get_field("outcome").choices_enum
strawberry.enum(cast(Any, RoundOutcome))
for enum in (RoundOpeningPolicy, AnswerVisibility, QuestionAudience, PassAudience):
    strawberry.enum(cast(Any, enum))


def _permission_annotation(model: Any, permission: str, info: strawberry.Info) -> Any:
    del info
    return permission_annotations(model, (permission,))[f"_angee_permission_{permission}"]


def _user_id(value: Any | None) -> strawberry.ID | None:
    """Project one attribution user id without exposing the user row."""

    return cast("strawberry.ID | None", optional_public_id(user_public_id(value)))


@pydantic_input(model=TopicTemplate, all_fields=True)
class ProposalTopicTemplateInput:
    """Typed topic declaration from its native validation owner."""


@pydantic_input(model=RoundTemplate, all_fields=True)
class ProposalRoundTemplateInput:
    """Typed round template from its native validation owner."""


@strawberry.input
class ProposalRoundSetupInput:
    """Round setup references are authorized before the domain sees them."""

    template: ProposalRoundTemplateInput
    facilitator: PublicID = strawberry.field(metadata={InputReference: InputReference(settings.AUTH_USER_MODEL)})
    responders: list[PublicID] = strawberry.field(metadata={InputReference: InputReference(settings.AUTH_USER_MODEL)})
    team: PublicID | None = strawberry.field(default=None, metadata={InputReference: InputReference("spaces.Group")})
    requester_party: PublicID | None = strawberry.field(
        default=None, metadata={InputReference: InputReference("parties.Party")},
    )


@strawberry.input(name="ProjectSetupInput", extend=True)
class ProjectProposalSetupInput:
    """Proposals contributes its typed setup choices to projects."""

    round: ProposalRoundSetupInput


@strawberry.type
class RoundRosterEntry:
    """The permitted roster facts without a proposal identifier."""

    user: strawberry.ID | None
    name: str
    track_status: str | None = None


@strawberry.type
class ClarificationRecipient:
    """An unanswered recipient reference, scoped by the question projection."""

    user: strawberry.ID | None
    name: str


def _clarification_waiting(self: Any) -> list[ClarificationRecipient]:
    return [
        ClarificationRecipient(user=_user_id(value["id"]), name=value["name"]) for value in self.clarification_waiting()
    ]


@strawberry.type
class RemovedShare:
    """One share identified by its canonical resource, relation and subject."""

    resource: str
    relation: str
    subject: str


@strawberry.type
class ResponderRemovalResult(ActionResult):
    """Retirement outcome with shares removed and retained for review."""

    _removed: strawberry.Private[list[RemovedShare]] = strawberry.field(default_factory=list)
    _reported: strawberry.Private[list[RemovedShare]] = strawberry.field(default_factory=list)

    @strawberry.field
    def removed_shares(self) -> list[RemovedShare]:
        # The shared guard returns a base ActionResult on domain failure.
        return self._removed if isinstance(self, ResponderRemovalResult) else []

    @strawberry.field
    def reported_shares(self) -> list[RemovedShare]:
        return self._reported if isinstance(self, ResponderRemovalResult) else []


@strawberry_django.type(Round)
class ProposalRoundType(AuthoredRefMixin, AngeeNode):
    """Round row with independently gated settings and roster projections."""

    revision: auto
    roster_visibility: str | None
    clarification_askers: str | None
    permissions = permissions_field(("manage", "write", "respond", "ask"))
    team: SpaceGroupType | None = actor_scoped_to_one("team")
    opens_after: MilestoneType | None = actor_scoped_to_one("opens_after")
    clarifications_shared_until: MilestoneType | None = actor_scoped_to_one("clarifications_shared_until")

    @strawberry_django.field(annotate={"_can_open": lambda info: Round.can_open_expression(current_actor())})
    def can_open(self) -> bool:
        """Read the owning verb's predicate from the optimized list query."""
        return cast(Any, self)._can_open

    @strawberry_django.field(annotate={"_can_admit": lambda info: Round.can_admit_expression(current_actor())})
    def can_admit(self) -> bool:
        """Read shell admission eligibility from its owner."""
        return cast(Any, self)._can_admit

    @strawberry_django.field(
        annotate={
            "_angee_permission_actor": lambda info: permission_annotations(Round, ())["_angee_permission_actor"],
            **{
                f"_angee_permission_{name}": partial(_permission_annotation, Round, name)
                for name in ("see_roster", "roster_status")
            },
        },
        prefetch_related=lambda info: Round.roster_prefetch(),
    )
    def roster(self) -> list[RoundRosterEntry]:
        """Compose batched native gates with the round's prefetched roster."""
        held = held_permissions(cast(Any, self), ("see_roster", "roster_status"))
        return [
            RoundRosterEntry(user=_user_id(row.user_id), name=row.name, track_status=row.track_status)
            for row in cast(Any, self).roster(permitted="see_roster" in held, status="roster_status" in held)
        ]

    name: auto
    opening_policy: auto
    status: auto
    outcome: auto
    last_call_at: auto
    submission_deadline: auto
    opened_at: auto
    closed_at: auto
    created_at: auto
    updated_at: auto

    task: TaskType | None = actor_scoped_to_one("task")
    project: ProjectType | None = actor_scoped_to_one("project")
    requester_party: PartyType | None = actor_scoped_to_one("requester_party")

    @strawberry_django.field(only=["facilitator_id"])
    def facilitator(self) -> strawberry.ID | None:
        """Return the facilitator's public id."""

        return _user_id(cast(Any, self).facilitator_id)

    @strawberry_django.field(only=["opened_by_id"])
    def opened_by(self) -> strawberry.ID | None:
        """Return the opening actor's public id."""

        return _user_id(cast(Any, self).opened_by_id)

    @strawberry_django.field(only=["closed_by_id"])
    def closed_by(self) -> strawberry.ID | None:
        """Return the closing actor's public id."""

        return _user_id(cast(Any, self).closed_by_id)


def question_attention_field(model: Any, *, passed_on: bool = False) -> Any:
    """Bind the same recipient-scope annotation to task and project fields."""

    name = "_questions_passed_on" if passed_on else "_questions_waiting_for_me"

    def resolve(root: Any) -> int:
        return getattr(root, name)

    return strawberry_django.field(resolver=resolve, annotate={
        name: lambda info: model.question_attention_expression(current_actor(), passed_on=passed_on),
    })


@strawberry.type
class TaskProposalsFields:
    """Shared declarations on both task schema nodes."""

    questions_waiting_for_me: int = question_attention_field(Task)
    questions_passed_on: int = question_attention_field(Task, passed_on=True)

    clarification_round: ProposalRoundType | None = actor_scoped_to_one("clarification_round")
    clarification_asker: UserType | None = actor_scoped_to_one("clarification_asker")
    clarification_by_manager: auto
    clarification_surrendered: auto
    clarification_default_visibility: auto
    clarification_passed_at: auto
    shared_with_responders: auto
    clarification_waiting = strawberry_django.field(
        resolver=_clarification_waiting,
        annotate={"_clarification_waiting": lambda info: Task.clarification_waiting_expression(current_actor())},
    )

    @strawberry_django.field(
        annotate={
            "_angee_permission_actor": lambda info: permission_annotations(Task, ())["_angee_permission_actor"],
            "_angee_permission_widen": partial(_permission_annotation, Task, "widen"),
            "_clarification_widen_blocker": lambda info: Task.clarification_widen_blocker_expression(),
        },
    )
    def clarification_widen_blocker(self) -> str | None:
        """Explain blocked publication only to a viewer allowed to widen."""
        if "widen" not in held_permissions(cast(Any, self), ("widen",)):
            return None
        return cast(Any, self)._clarification_widen_blocker


@strawberry_django.type(Task, name="TaskType", extend=True)
class TaskProposalsExtension(TaskProposalsFields):
    """Public task contributions owned by proposals."""


@strawberry_django.type(Task, name="ConsoleTaskType", extend=True)
class ConsoleTaskProposalsExtension(TaskProposalsFields):
    """Console task contributions owned by proposals."""


@strawberry_django.type(Topic)
class ProposalTopicType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of one stable comparison topic."""

    key: auto
    name: auto
    hint: auto
    sort_order: auto
    created_at: auto
    updated_at: auto

    round: ProposalRoundType | None = actor_scoped_to_one("round")


@strawberry_django.type(Proposal)
class ProposalFields(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a field-gated structured response."""

    revision: auto
    statement: str | None
    disclosed_at: auto
    track_published_at: auto
    retired_at: auto
    permissions = permissions_field(("write", "publish", "withdraw"))

    @strawberry_django.field(annotate={"_track_status": lambda info: Proposal.track_status_expression(current_actor())})
    def track_status(self) -> str | None:
        """Read the gated scalar without exposing the track."""
        return cast(Any, self)._track_status

    state: auto
    cost: auto
    # The database keeps this comparison cell as a non-null string, while the
    # field permission redacts it for proposal viewers outside the sealed group.
    staffing: str | None
    timeframe_start: auto
    timeframe_end: auto
    confidence: auto
    valid_until: auto
    capture_payload_hash: auto
    capture_parser_version: auto
    submitted_at: auto
    decided_at: auto
    created_at: auto
    updated_at: auto

    round: ProposalRoundType | None = actor_scoped_to_one("round")
    party: PartyType | None = actor_scoped_to_one("party")
    source_message: MessageType | None = actor_scoped_to_one("source_message")
    track: ProjectType | None = actor_scoped_to_one("track")
    currency: CurrencyType | None = actor_scoped_to_one("currency")

    @strawberry_django.field(only=["retired_by_id"])
    def retired_by(self) -> strawberry.ID | None:
        """Return the retirement actor's public id."""

        return _user_id(cast(Any, self).retired_by_id)

    @strawberry_django.field(only=["submitted_by_id"])
    def submitted_by(self) -> strawberry.ID | None:
        """Return the submission actor's public id."""

        return _user_id(cast(Any, self).submitted_by_id)

    @strawberry_django.field(only=["decided_by_id"])
    def decided_by(self) -> strawberry.ID | None:
        """Return the decision actor's public id."""

        return _user_id(cast(Any, self).decided_by_id)


@strawberry_django.type(Proposal)
class ProposalType(ProposalFields):
    """Public response with a roster-gated scalar responder reference."""

    @strawberry_django.field(
        only=["responder_id"],
        annotate={
            "_angee_permission_actor": lambda info: permission_annotations(Proposal, ())["_angee_permission_actor"],
            "_angee_permission_read__responder": lambda info: permission_annotations(
                Proposal,
                ("read__responder",),
            )["_angee_permission_read__responder"],
        },
    )
    def responder(self) -> strawberry.ID | None:
        """Return the responder's public id."""

        return (
            _user_id(cast(Any, self).responder_id)
            if "read__responder"
            in held_permissions(
                cast(Any, self),
                ("read__responder",),
            )
            else None
        )


@strawberry_django.type(Proposal)
class ConsoleProposalType(ProposalFields):
    """Console response with the same roster gate on its responder relation."""

    responder: UserType | None = actor_scoped_to_one("responder")


@strawberry.type
class ProjectAttentionFields:
    """Project summaries compose the question owner's recipient scopes."""

    questions_waiting_for_me: int = question_attention_field(Project)
    questions_passed_on: int = question_attention_field(Project, passed_on=True)


@strawberry_django.type(Project, name="ProjectType", extend=True)
class ProjectProposalsExtension(ProjectAttentionFields):
    """Expose the proposal-owned track relation through the common redaction seam."""

    source_proposal: ProposalType | None = actor_scoped_to_one("source_proposal")


@strawberry_django.type(Project, name="ConsoleProjectType", extend=True)
class ConsoleProjectProposalsExtension(ProjectAttentionFields):
    """Contribute the track relation to the console project node."""

    source_proposal: ConsoleProposalType | None = actor_scoped_to_one("source_proposal")


@strawberry_django.type(Answer)
class ProposalAnswerType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of one topic-aligned answer."""

    revision: auto
    visibility: auto
    shared_with_responders: auto
    permissions = permissions_field(("write", "narrow", "manage"))

    body: auto
    created_at: auto
    updated_at: auto

    proposal: ProposalType | None = actor_scoped_to_one("proposal")
    topic: ProposalTopicType | None = actor_scoped_to_one("topic")


@strawberry_django.type(Review)
class ProposalReviewType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of one evaluator assessment."""

    body: auto
    created_at: auto
    updated_at: auto

    proposal: ProposalType | None = actor_scoped_to_one("proposal")

    @strawberry_django.field(only=["reviewer_id"])
    def reviewer(self) -> strawberry.ID | None:
        """Return the reviewer's public id."""

        return _user_id(cast(Any, self).reviewer_id)


@action_guard("Resolve question failed.")
def resolve_proposal_clarification(
    info: strawberry.Info, task: PublicID, expected_revision: int,
) -> ActionResult:
    """Resolve a question through its round's management verb."""

    question = authorized_permission_target(info, Task, task, "write")
    if question.clarification_round_id is None:
        raise ValidationError({"task": "Choose a clarification question."})
    round = question.clarification_round.with_actor(current_actor())
    result = round.resolve_clarification(question, expected_revision=expected_revision)
    return ActionResult(ok=True, message="Question resolved.", id=result.sqid)

@strawberry.type
class ProposalActionMutation:
    """Row-authorized lifecycle, identity, track, and capture verbs."""

    @strawberry.mutation
    @action_guard("Round provisioning failed.")
    def provision_proposal_round(
        self,
        info: strawberry.Info,
        template: JSON,
        facilitator: PublicID,
        responders: list[PublicID],
        project: PublicID | None = None,
        task: PublicID | None = None,
        team: PublicID | None = None,
    ) -> ActionResult:
        """Resolve authorized inputs and delegate replay-safe setup."""
        if (project is None) == (task is None):
            raise ValidationError({"project": "Choose exactly one project or task."})
        target_id = project if project is not None else task
        assert target_id is not None
        target_row = authorized_permission_target(
            info, Project if project is not None else Task, target_id, "write"
        )
        facilitator_row = authorized_permission_target(info, User, facilitator, "read")
        responder_rows = [authorized_permission_target(info, User, user, "read") for user in responders]
        team_row = authorized_permission_target(info, Team, team, "read") if team else None
        round = Round.objects.provision(target_row, template, facilitator_row, responder_rows, team_row)
        return ActionResult(ok=True, message="Proposal round provisioned.", id=round.sqid)

    @strawberry.mutation
    @action_guard("Responder admission failed.")
    def admit_proposal_round_responder(
        self,
        info: strawberry.Info,
        round: PublicID,
        responder: PublicID,
        party: PublicID | None = None,
        track: bool = False,
    ) -> ActionResult:
        """Admit a responder through the shell owner."""
        row = authorized_permission_target(info, Round, round, "write")
        user = authorized_permission_target(info, User, responder, "read")
        party_row = authorized_permission_target(info, Party, party, "read") if party else None
        proposal = row.admit(user, party_row, track)
        return ActionResult(ok=True, message="Responder admitted.", id=proposal.sqid)

    @strawberry.mutation
    @action_guard("Responder removal failed.", errors=(StaleRevisionError,))
    def remove_proposal_round_responder(
        self,
        info: strawberry.Info,
        round: PublicID,
        responder: PublicID,
        expected_revision: int | None = None,
    ) -> ResponderRemovalResult:
        """Return the retirement owner's share report."""
        row = authorized_permission_target(info, Round, round, "manage")
        user = User.system_queryset().from_public_id(str(responder))
        if user is None:
            raise ValidationError({"responder": "Responder was not found."})
        report = row.remove_responder(user, expected_revision)
        result = ResponderRemovalResult(
            ok=True,
            message=f"Responder removed. {len(report['removed'])} shares removed; "
            f"{len(report['reported'])} wider shares remain for review.",
            id=row.sqid,
        )
        result._removed = [RemovedShare(**value) for value in report["removed"]]
        result._reported = [RemovedShare(**value) for value in report["reported"]]
        return result

    @strawberry.mutation
    @action_guard("Opening policy change failed.", errors=(StaleRevisionError,))
    def widen_proposal_round_opening_policy(
        self,
        info: strawberry.Info,
        round: PublicID,
        policy: RoundOpeningPolicy,
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Widen the round's disclosure policy."""
        row = authorized_permission_target(info, Round, round, "write")
        row.widen_opening_policy(policy, expected_revision)
        return ActionResult(ok=True, message="Opening policy widened.", id=row.sqid)

    @strawberry.mutation
    @action_guard("Answer visibility change failed.", errors=(StaleRevisionError,))
    def set_proposal_answer_visibility(
        self,
        info: strawberry.Info,
        answer: PublicID,
        visibility: AnswerVisibility,
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Select an answer audience through its owner."""
        row = authorized_permission_target(info, Answer, answer, "narrow")
        row.set_visibility(visibility, expected_revision)
        return ActionResult(ok=True, message="Answer audience changed.", id=row.sqid)

    @strawberry.mutation
    @action_guard("Answer responder sharing failed.", errors=(StaleRevisionError,))
    def set_proposal_answer_responder_share(
        self,
        info: strawberry.Info,
        answer: PublicID,
        shared: bool,
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Set the live responder audience on an answer."""
        row = authorized_permission_target(info, Answer, answer, "manage" if shared else "narrow")
        row.set_responder_share(shared, expected_revision)
        return ActionResult(ok=True, message="Answer responder audience changed.", id=row.sqid)

    @strawberry.mutation
    @action_guard("Task responder sharing failed.", errors=(StaleRevisionError,))
    def set_task_responder_share(
        self,
        info: strawberry.Info,
        task: PublicID,
        shared: bool,
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Set the live responder audience on a track task."""
        row = authorized_permission_target(info, Task, task, "widen" if shared else "narrow")
        row.set_responder_share(shared, expected_revision)
        return ActionResult(ok=True, message="Task responder audience changed.", id=row.sqid)

    @strawberry.mutation
    @action_guard("Question creation failed.", errors=(CreationKeyConflict,))
    def ask_proposal_round(
        self,
        info: strawberry.Info,
        round: PublicID,
        title: str,
        body: str,
        audience: QuestionAudience = QuestionAudience("default"),
        recipient: PublicID | None = None,
        responders: bool = False,
        client_creation_key: str | None = None,
    ) -> ActionResult:
        """Return only the question id, including replays after surrender."""
        row = authorized_permission_target(info, Round, round, "ask")
        task = row.ask(
            title, body, audience, _clarification_recipient(info, recipient, responders), client_creation_key
        )
        return ActionResult(ok=True, message="Question asked.", id=task.sqid)

    @strawberry.mutation
    @action_guard("Question edit failed.", errors=(StaleRevisionError,))
    def edit_proposal_round_clarification(
        self,
        info: strawberry.Info,
        round: PublicID,
        task: PublicID,
        title: str,
        body: str,
        audience: QuestionAudience | None = None,
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Dispatch pre-pass editing to the round."""
        row = authorized_permission_target(info, Round, round, "ask")
        question = Task.system_queryset().from_public_id(str(task))
        if question is None:
            raise ValidationError({"task": "Question was not found."})
        result = row.edit_clarification(question, title, body, audience, expected_revision)
        return ActionResult(ok=True, message="Question updated.", id=result.sqid)

    resolve_proposal_clarification = strawberry.mutation(resolver=resolve_proposal_clarification)

    @strawberry.mutation
    def resolve_proposal_clarifications(
        self, info: strawberry.Info, selection: list[ActionSelectionInput],
    ) -> list[ActionResult]:
        """Resolve selected questions; refusals do not roll back eligible rows."""

        return many_actions(selection, lambda item: resolve_proposal_clarification(
            info, item.id, item.expected_revision,
        ))

    @strawberry.mutation
    @action_guard("Passing question failed.", errors=(StaleRevisionError,))
    def pass_proposal_round_clarification(
        self,
        info: strawberry.Info,
        round: PublicID,
        task: PublicID,
        recipient: PublicID | None = None,
        responders: bool = False,
        audience: PassAudience = PassAudience("default"),
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Assign and pass the existing question without copying its content."""
        row = authorized_permission_target(info, Round, round, "manage")
        question = Task.system_queryset().from_public_id(str(task))
        if question is None:
            raise ValidationError({"task": "Question was not found."})
        result = row.pass_clarification(
            question,
            _clarification_recipient(info, recipient, responders),
            audience,
            expected_revision,
        )
        return ActionResult(ok=True, message="Question passed.", id=result.sqid)

    @strawberry.mutation
    @action_guard("Open round failed.", errors=(StaleRevisionError,))
    def open_proposal_round(
        self, info: strawberry.Info, round: PublicID, expected_revision: int | None = None
    ) -> ActionResult:
        """Open one collecting Round under its declared disclosure policy."""

        target = authorized_action_target(info, Round, round, "write")
        target.open(expected_revision=expected_revision)
        return ActionResult(ok=True, message="Proposal round opened.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Close round failed.", errors=(StaleRevisionError,))
    def close_proposal_round(
        self,
        info: strawberry.Info,
        round: PublicID,
        outcome: RoundOutcome,  # type: ignore[valid-type]
        accepted: list[PublicID],
        partial: list[PublicID],
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Close one Round with disjoint accepted and partial selections."""

        target = authorized_action_target(info, Round, round, "write")
        accepted_rows = [authorized_action_target(info, Proposal, value, "evaluate") for value in accepted]
        partial_rows = [authorized_action_target(info, Proposal, value, "evaluate") for value in partial]
        target.close(outcome, accepted=accepted_rows, partial=partial_rows, expected_revision=expected_revision)
        return ActionResult(ok=True, message="Proposal round closed.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Cancel round failed.", errors=(StaleRevisionError,))
    def cancel_proposal_round(
        self, info: strawberry.Info, round: PublicID, expected_revision: int | None = None
    ) -> ActionResult:
        """Cancel a collecting or opened Round."""

        target = authorized_action_target(info, Round, round, "write")
        target.cancel(expected_revision=expected_revision)
        return ActionResult(ok=True, message="Proposal round cancelled.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Transfer facilitation failed.", errors=(StaleRevisionError,))
    def transfer_proposal_round_facilitation(
        self,
        info: strawberry.Info,
        round: PublicID,
        facilitator: PublicID,
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Transfer the round manager seat."""

        target = authorized_action_target(info, Round, round, "write")
        user = User.system_queryset().from_public_id(str(facilitator))
        if user is None:
            raise ValidationError({"facilitator": "User was not found."})
        target.transfer_facilitation(user, expected_revision=expected_revision)
        return ActionResult(ok=True, message="Round facilitation transferred.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Submit proposal failed.", errors=(StaleRevisionError,))
    def submit_proposal(
        self, info: strawberry.Info, proposal: PublicID, expected_revision: int | None = None
    ) -> ActionResult:
        """Submit one draft and freeze its document."""

        target = authorized_action_target(info, Proposal, proposal, "write")
        target.submit(expected_revision=expected_revision)
        return ActionResult(ok=True, message="Proposal submitted.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Withdraw proposal failed.", errors=(StaleRevisionError,))
    def withdraw_proposal(
        self, info: strawberry.Info, proposal: PublicID, expected_revision: int | None = None
    ) -> ActionResult:
        """Withdraw one submitted Proposal as responder or facilitator."""

        target = authorized_permission_target(info, Proposal, proposal, "withdraw")
        target.withdraw(expected_revision=expected_revision)
        return ActionResult(ok=True, message="Proposal withdrawn.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Identify proposal party failed.")
    def identify_proposal_party(
        self,
        info: strawberry.Info,
        proposal: PublicID,
        party: PublicID,
    ) -> ActionResult:
        """Set one Proposal's party once through its row-locked verb."""

        target = authorized_action_target(info, Proposal, proposal, "write")
        party_row = authorized_permission_target(info, Party, party, "read")
        target.identify_party(party_row)
        return ActionResult(ok=True, message="Proposal party identified.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Create proposal track failed.")
    def create_proposal_track(self, info: strawberry.Info, proposal: PublicID) -> ActionResult:
        """Create the Proposal's one system-owned private Project track."""

        target = authorized_action_target(info, Proposal, proposal, "write")
        track = target.create_track()
        return ActionResult(ok=True, message="Proposal track created.", id=track.sqid)

    @strawberry.mutation
    @action_guard("Publish proposal track failed.", errors=(StaleRevisionError,))
    def publish_proposal_track(
        self, info: strawberry.Info, proposal: PublicID, expected_revision: int | None = None
    ) -> ActionResult:
        """Publish a validated private track to the Round's responders."""

        target = authorized_permission_target(info, Proposal, proposal, "publish")
        track = target.publish_track(expected_revision=expected_revision)
        return ActionResult(ok=True, message="Proposal track published.", id=track.sqid)

    @strawberry.mutation
    @action_guard("Capture proposal failed.")
    def capture_proposal_from_message(
        self,
        info: strawberry.Info,
        round: PublicID,
        message: PublicID,
        party: PublicID | None = None,
    ) -> ActionResult:
        """Capture and submit one structured reply in a single transaction."""

        round_row = authorized_action_target(info, Round, round, "write")
        message_row = authorized_permission_target(info, Message, message, "read")
        party_row = None if party is None else authorized_permission_target(info, Party, party, "read")
        proposal = Proposal.objects.capture_from_message(message_row, round_row, party_row)
        return ActionResult(ok=True, message="Proposal captured.", id=proposal.sqid)


def _clarification_recipient(info: strawberry.Info, recipient: PublicID | None, responders: bool) -> Any:
    """Decode one explicit user or the round-wide responder audience."""
    if recipient is not None and responders:
        raise ValidationError({"recipient": "Choose a user or all responders, not both."})
    if responders:
        return "responders"
    return authorized_permission_target(info, User, recipient, "read") if recipient is not None else None


_ROUND_RESOURCE = hasura_model_resource(
    ProposalRoundType,
    model=Round,
    name="proposal_rounds",
    filterable=[
        "id",
        "task",
        "project",
        "name",
        "team",
        "facilitator",
        "requester_party",
        "opening_policy",
        "status",
        "outcome",
        "last_call_at",
        "submission_deadline",
        "opened_at",
        "opened_by",
        "closed_at",
        "closed_by",
        "created_at",
        "updated_at",
        *declared_hasura_resource_fields(Round, "hasura_filterable_fields"),
    ],
    sortable=[
        "name",
        "status",
        "opening_policy",
        "last_call_at",
        "submission_deadline",
        "opened_at",
        "closed_at",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id"],
    groupable=["task", "project", "facilitator", "requester_party", "opening_policy", "status", "outcome"],
    insertable=[
        "task",
        "project",
        "name",
        "facilitator",
        "requester_party",
        "opening_policy",
        "status",
        "team",
        "roster_visibility",
        "clarification_askers",
        "opens_after",
        "clarifications_shared_until",
        "last_call_at",
        "submission_deadline",
        *declared_hasura_resource_fields(Round, "hasura_insertable_fields"),
    ],
    updatable=[
        "name",
        "requester_party",
        "opening_policy",
        "last_call_at",
        "submission_deadline",
        "team",
        "roster_visibility",
        "clarification_askers",
        "opens_after",
        "clarifications_shared_until",
        *declared_hasura_resource_fields(Round, "hasura_updatable_fields"),
    ],
    field_id_decode={
        "team": public_pk_decoder(Team),
        "opens_after": public_pk_decoder(Milestone),
        "clarifications_shared_until": public_pk_decoder(Milestone),
        "task": public_pk_decoder(Task),
        "project": public_pk_decoder(Project),
        "facilitator": public_pk_decoder(User),
        "requester_party": public_pk_decoder(Party),
        "opened_by": public_pk_decoder(User),
        "closed_by": public_pk_decoder(User),
    },
    write_backend=AngeeHasuraWriteBackend(
        Round,
        public_id_fields=(
            "task",
            "project",
            "facilitator",
            "requester_party",
            "team",
            "opens_after",
            "clarifications_shared_until",
            *declared_hasura_write_relation_fields(Round),
        ),
        delete_guard=lambda instance: instance.deletion_error(),
    ),
)

_TOPIC_RESOURCE = hasura_model_resource(
    ProposalTopicType,
    model=Topic,
    name="proposal_topics",
    filterable=["id", "round", "key", "name", "created_at", "updated_at"],
    sortable=["round", "sort_order", "key", "name", "created_at", "updated_at"],
    aggregatable=["id", "sort_order"],
    groupable=["round", "key"],
    insertable=["round", "key", "name", "hint", "sort_order"],
    updatable=["name", "hint", "sort_order"],
    field_id_decode={"round": public_pk_decoder(Round)},
    write_backend=AngeeHasuraWriteBackend(Topic, public_id_fields=("round",)),
)


def _proposal_resource(node_type: type) -> Any:
    """Build the same Proposal resource around one schema-specific node type."""

    return hasura_model_resource(
        node_type,
        model=Proposal,
        name="proposals",
        filterable=[
            "id",
            "round",
            "party",
            "state",
            "track",
            "submitted_at",
            "submitted_by",
            "decided_at",
            "decided_by",
            "created_at",
            "updated_at",
        ],
        sortable=[
            "round",
            "state",
            "submitted_at",
            "decided_at",
            "created_at",
            "updated_at",
        ],
        aggregatable=["id"],
        groupable=["round", "party", "state", "track"],
        insertable=[
            "round",
            "responder",
            "party",
            "source_message",
            "state",
            "statement",
            "cost",
            "currency",
            "staffing",
            "timeframe_start",
            "timeframe_end",
            "confidence",
            "valid_until",
        ],
        updatable=[
            "statement",
            "cost",
            "currency",
            "staffing",
            "timeframe_start",
            "timeframe_end",
            "confidence",
            "valid_until",
        ],
        field_id_decode={
            "round": public_pk_decoder(Round),
            "responder": public_pk_decoder(User),
            "party": public_pk_decoder(Party),
            "source_message": public_pk_decoder(Message),
            "track": public_pk_decoder(Project),
            "currency": public_pk_decoder(Currency),
            "submitted_by": public_pk_decoder(User),
            "decided_by": public_pk_decoder(User),
        },
        write_backend=AngeeHasuraWriteBackend(
            Proposal,
            public_id_fields=("round", "responder", "party", "source_message", "currency"),
            delete_guard=lambda instance: instance.deletion_error(),
        ),
    )


_PUBLIC_PROPOSAL_RESOURCE = _proposal_resource(ProposalType)
_CONSOLE_PROPOSAL_RESOURCE = _proposal_resource(ConsoleProposalType)

_ANSWER_RESOURCE = hasura_model_resource(
    ProposalAnswerType,
    model=Answer,
    name="proposal_answers",
    filterable=["id", "proposal", "topic", "visibility", "shared_with_responders", "created_at", "updated_at"],
    sortable=["topic", "proposal", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["proposal", "topic"],
    insertable=["proposal", "topic", "body", "visibility"],
    updatable=["body"],
    field_id_decode={
        "proposal": public_pk_decoder(Proposal),
        "topic": public_pk_decoder(Topic),
    },
    write_backend=AngeeHasuraWriteBackend(Answer, public_id_fields=("proposal", "topic")),
)

_REVIEW_RESOURCE = hasura_model_resource(
    ProposalReviewType,
    model=Review,
    name="proposal_reviews",
    filterable=["id", "proposal", "reviewer", "created_at", "updated_at"],
    sortable=["reviewer", "proposal", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["proposal", "reviewer"],
    insertable=["proposal", "reviewer", "body"],
    updatable=["body"],
    field_id_decode={
        "proposal": public_pk_decoder(Proposal),
        "reviewer": public_pk_decoder(User),
    },
    write_backend=AngeeHasuraWriteBackend(Review, public_id_fields=("proposal", "reviewer")),
)

_COMMON_RESOURCE_TYPES = [
    *_ROUND_RESOURCE.types,
    *_TOPIC_RESOURCE.types,
    *_ANSWER_RESOURCE.types,
    *_REVIEW_RESOURCE.types,
]


def _proposals_schema_bucket(proposal_resource: Any, proposal_type: type) -> dict[str, Any]:
    """Return the shared proposal surface with its schema-specific Proposal node."""

    return {
        "query": [
            _ROUND_RESOURCE.query,
            _TOPIC_RESOURCE.query,
            proposal_resource.query,
            _ANSWER_RESOURCE.query,
            _REVIEW_RESOURCE.query,
        ],
        "mutation": [
            ProposalActionMutation,
            _ROUND_RESOURCE.mutation,
            _TOPIC_RESOURCE.mutation,
            proposal_resource.mutation,
            _ANSWER_RESOURCE.mutation,
            _REVIEW_RESOURCE.mutation,
        ],
        "types": [
            ProposalRoundType,
            ProposalTopicType,
            proposal_type,
            ProposalAnswerType,
            ProposalReviewType,
            ProjectType,
            TaskType,
            PartyType,
            MessageType,
            CurrencyType,
            *proposal_resource.types,
            *_COMMON_RESOURCE_TYPES,
        ],
        "input_extensions": [ProjectProposalSetupInput],
        "type_extensions": [TaskProposalsExtension, ProjectProposalsExtension],
    }


_PUBLIC_PROPOSALS_SCHEMA_BUCKET = _proposals_schema_bucket(
    _PUBLIC_PROPOSAL_RESOURCE,
    ProposalType,
)
_CONSOLE_PROPOSALS_SCHEMA_BUCKET = _proposals_schema_bucket(
    _CONSOLE_PROPOSAL_RESOURCE,
    ConsoleProposalType,
)
_CONSOLE_PROPOSALS_SCHEMA_BUCKET["types"].append(UserType)
_CONSOLE_PROPOSALS_SCHEMA_BUCKET["type_extensions"].extend(
    [ConsoleTaskProposalsExtension, ConsoleProjectProposalsExtension],
)

schemas = {
    "public": {**_PUBLIC_PROPOSALS_SCHEMA_BUCKET},
    "console": {
        **_CONSOLE_PROPOSALS_SCHEMA_BUCKET,
        "subscription": [
            changes(Round, field="proposalRoundChanged"),
            changes(Topic, field="proposalTopicChanged"),
            changes(Proposal, field="proposalChanged"),
            changes(Answer, field="proposalAnswerChanged"),
            changes(Review, field="proposalReviewChanged"),
        ],
    },
}
