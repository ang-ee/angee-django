"""GraphQL resources and authored verbs for sealed proposal rounds."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from strawberry import auto

from angee.graphql.actions import (
    ActionResult,
    action_guard,
    authorized_action_target,
    resolve_action_target,
)
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, optional_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.audit import AuthoredRefMixin
from angee.iam.identity import user_public_id
from angee.messaging.schema import MessageType
from angee.money.schema import CurrencyType
from angee.parties.schema import PartyType
from angee.projects.schema import ProjectType, TaskType

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
User = get_user_model()

RoundOutcome = Round._meta.get_field("outcome").choices_enum
strawberry.enum(cast(Any, RoundOutcome))


def _user_id(value: Any | None) -> strawberry.ID | None:
    """Project one attribution user id without exposing the user row."""

    return cast("strawberry.ID | None", optional_public_id(user_public_id(value)))


def _permission_target(model: type, value: PublicID, permission: str, reason: str) -> Any:
    """Resolve a non-write lifecycle target and enforce its explicit permission."""

    target = resolve_action_target(model, value, reason=reason)
    if not target.has_access(permission):
        raise ValidationError(f"You are not allowed to {permission} this {model._meta.verbose_name}.")
    return target


@strawberry_django.type(Round)
class ProposalRoundType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of one directly granted solicitation Round."""

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
class ProposalType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a field-gated structured response."""

    state: auto
    cost: auto
    # The database keeps this comparison cell as a non-null string, but B8's
    # field gate redacts it to GraphQL null for round readers.
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

    @strawberry_django.field(only=["responder_id"])
    def responder(self) -> strawberry.ID | None:
        """Return the responder's public id."""

        return _user_id(cast(Any, self).responder_id)

    @strawberry_django.field(only=["submitted_by_id"])
    def submitted_by(self) -> strawberry.ID | None:
        """Return the submission actor's public id."""

        return _user_id(cast(Any, self).submitted_by_id)

    @strawberry_django.field(only=["decided_by_id"])
    def decided_by(self) -> strawberry.ID | None:
        """Return the decision actor's public id."""

        return _user_id(cast(Any, self).decided_by_id)


@strawberry_django.type(Answer)
class ProposalAnswerType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of one topic-aligned answer."""

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


@strawberry.type
class ProposalActionMutation:
    """Row-authorized lifecycle, identity, track, and capture verbs."""

    @strawberry.mutation
    @action_guard("Open round failed.")
    def open_proposal_round(self, info: strawberry.Info, round: PublicID) -> ActionResult:
        """Open one collecting Round under its declared disclosure policy."""

        target = authorized_action_target(info, Round, round, "write")
        target.open()
        return ActionResult(ok=True, message="Proposal round opened.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Close round failed.")
    def close_proposal_round(
        self,
        info: strawberry.Info,
        round: PublicID,
        outcome: RoundOutcome,  # type: ignore[valid-type]
        accepted: list[PublicID],
        partial: list[PublicID],
    ) -> ActionResult:
        """Close one Round with disjoint accepted and partial selections."""

        target = authorized_action_target(info, Round, round, "write")
        accepted_rows = [authorized_action_target(info, Proposal, value, "evaluate") for value in accepted]
        partial_rows = [authorized_action_target(info, Proposal, value, "evaluate") for value in partial]
        target.close(outcome, accepted=accepted_rows, partial=partial_rows)
        return ActionResult(ok=True, message="Proposal round closed.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Cancel round failed.")
    def cancel_proposal_round(self, info: strawberry.Info, round: PublicID) -> ActionResult:
        """Cancel a collecting or opened Round."""

        target = authorized_action_target(info, Round, round, "write")
        target.cancel()
        return ActionResult(ok=True, message="Proposal round cancelled.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Transfer facilitation failed.")
    def transfer_proposal_round_facilitation(
        self,
        info: strawberry.Info,
        round: PublicID,
        facilitator: PublicID,
    ) -> ActionResult:
        """Transfer facilitation and reconcile private-track grants."""

        target = authorized_action_target(info, Round, round, "write")
        user = User.system_queryset().from_public_id(str(facilitator))
        if user is None:
            raise ValidationError({"facilitator": "User was not found."})
        target.transfer_facilitation(user)
        return ActionResult(ok=True, message="Round facilitation transferred.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Submit proposal failed.")
    def submit_proposal(self, info: strawberry.Info, proposal: PublicID) -> ActionResult:
        """Submit one draft and revoke its responder editor tuple."""

        target = authorized_action_target(info, Proposal, proposal, "write")
        target.submit()
        return ActionResult(ok=True, message="Proposal submitted.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Withdraw proposal failed.")
    def withdraw_proposal(self, info: strawberry.Info, proposal: PublicID) -> ActionResult:
        """Withdraw one submitted Proposal as responder or facilitator."""

        del info
        target = _permission_target(
            Proposal,
            proposal,
            "withdraw",
            "proposals.graphql.withdraw",
        )
        target.withdraw()
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
        party_row = _permission_target(Party, party, "read", "proposals.graphql.identify_party")
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
    @action_guard("Publish proposal track failed.")
    def publish_proposal_track(self, info: strawberry.Info, proposal: PublicID) -> ActionResult:
        """Publish a validated private track to the Round's responders."""

        del info
        target = _permission_target(
            Proposal,
            proposal,
            "publish",
            "proposals.graphql.publish_track",
        )
        track = target.publish_track()
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
        message_row = _permission_target(Message, message, "read", "proposals.graphql.capture_message")
        party_row = (
            None
            if party is None
            else _permission_target(Party, party, "read", "proposals.graphql.capture_party")
        )
        proposal = Proposal.objects.capture_from_message(message_row, round_row, party_row)
        return ActionResult(ok=True, message="Proposal captured.", id=proposal.sqid)


_ROUND_RESOURCE = hasura_model_resource(
    ProposalRoundType,
    model=Round,
    name="proposal_rounds",
    filterable=[
        "id",
        "task",
        "project",
        "name",
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
        "last_call_at",
        "submission_deadline",
    ],
    updatable=["name", "requester_party", "opening_policy", "last_call_at", "submission_deadline"],
    field_id_decode={
        "task": public_pk_decoder(Task),
        "project": public_pk_decoder(Project),
        "facilitator": public_pk_decoder(User),
        "requester_party": public_pk_decoder(Party),
        "opened_by": public_pk_decoder(User),
        "closed_by": public_pk_decoder(User),
    },
    write_backend=AngeeHasuraWriteBackend(
        Round,
        public_id_fields=("task", "project", "facilitator", "requester_party"),
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

_PROPOSAL_RESOURCE = hasura_model_resource(
    ProposalType,
    model=Proposal,
    name="proposals",
    filterable=[
        "id",
        "round",
        "responder",
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
    groupable=["round", "responder", "party", "state", "track"],
    insertable=[
        "round",
        "responder",
        "party",
        "source_message",
        "state",
        "cost",
        "currency",
        "staffing",
        "timeframe_start",
        "timeframe_end",
        "confidence",
        "valid_until",
    ],
    updatable=[
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

_ANSWER_RESOURCE = hasura_model_resource(
    ProposalAnswerType,
    model=Answer,
    name="proposal_answers",
    filterable=["id", "proposal", "topic", "created_at", "updated_at"],
    sortable=["topic", "proposal", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["proposal", "topic"],
    insertable=["proposal", "topic", "body"],
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

_RESOURCE_TYPES = [
    *_ROUND_RESOURCE.types,
    *_TOPIC_RESOURCE.types,
    *_PROPOSAL_RESOURCE.types,
    *_ANSWER_RESOURCE.types,
    *_REVIEW_RESOURCE.types,
]

_PROPOSALS_SCHEMA_BUCKET = {
    "query": [
        _ROUND_RESOURCE.query,
        _TOPIC_RESOURCE.query,
        _PROPOSAL_RESOURCE.query,
        _ANSWER_RESOURCE.query,
        _REVIEW_RESOURCE.query,
    ],
    "mutation": [
        ProposalActionMutation,
        _ROUND_RESOURCE.mutation,
        _TOPIC_RESOURCE.mutation,
        _PROPOSAL_RESOURCE.mutation,
        _ANSWER_RESOURCE.mutation,
        _REVIEW_RESOURCE.mutation,
    ],
    "types": [
        ProposalRoundType,
        ProposalTopicType,
        ProposalType,
        ProposalAnswerType,
        ProposalReviewType,
        ProjectType,
        TaskType,
        PartyType,
        MessageType,
        CurrencyType,
        *_RESOURCE_TYPES,
    ],
}

schemas = {
    "public": {**_PROPOSALS_SCHEMA_BUCKET},
    "console": {
        **_PROPOSALS_SCHEMA_BUCKET,
        "subscription": [
            changes(Round, field="proposalRoundChanged"),
            changes(Topic, field="proposalTopicChanged"),
            changes(Proposal, field="proposalChanged"),
            changes(Answer, field="proposalAnswerChanged"),
            changes(Review, field="proposalReviewChanged"),
        ],
    },
}
