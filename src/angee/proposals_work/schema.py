"""Expose the bridge-owned relation on the existing round GraphQL node."""

import strawberry
import strawberry_django
from django.apps import apps

from angee.graphql.ids import PublicID
from angee.graphql.inputs import InputReference
from angee.graphql.relations import actor_scoped_to_one
from angee.work.schema import WorkQueueType

Round = apps.get_model("proposals", "Round")


@strawberry_django.type(Round, name="ProposalRoundType", extend=True)
class RoundQuestionsQueueExtension:
    """Redact an unreadable routing queue through the shared relation owner."""

    clarification_queue: WorkQueueType | None = actor_scoped_to_one("clarification_queue")


@strawberry.input(name="ProposalRoundSetupInput", extend=True)
class RoundQuestionsQueueSetupInput:
    """Contribute question routing to the typed round setup input."""

    clarification_queue: PublicID | None = strawberry.field(
        default=None, metadata={InputReference: InputReference("work.Queue")},
    )


schemas = {
    "public": {"type_extensions": [RoundQuestionsQueueExtension], "input_extensions": [RoundQuestionsQueueSetupInput]},
    "console": {"type_extensions": [RoundQuestionsQueueExtension], "input_extensions": [RoundQuestionsQueueSetupInput]},
}
