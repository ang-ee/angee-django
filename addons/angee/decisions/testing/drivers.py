"""Real question admission for browser journeys and management shells."""

from collections.abc import Sequence
from typing import Any

from django.apps import apps

from angee.decisions.contracts import DEFAULT_REQUESTER, DecisionProposal, DecisionRequest


def seed_decision(*, actor: Any, assignees: Sequence[Any], reference: Any, requester: Any = DEFAULT_REQUESTER) -> Any:
    return apps.get_model("decisions", "Decision").objects.ask(
        DecisionRequest(
            kind="review_reference",
            records=(reference,),
            assignees=tuple(assignees),
            requester=requester,
            proposal=DecisionProposal.model_validate(
                {
                    "alternatives": (
                        {"key": "accept", "label": "Accept", "outcome": "accepted"},
                        {"key": "reject", "label": "Reject", "outcome": "rejected"},
                    )
                }
            ),
        ),
        actor=actor,
    )
