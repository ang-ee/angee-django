"""Real decision admission for browser journeys and management-shell setup."""

from collections.abc import Sequence
from typing import Any

from django.apps import apps
from pydantic import Field

from angee.base.identity import public_id_of
from angee.decisions.contracts import DEFAULT_REQUESTER, DecisionContext, DecisionRecordReference, DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict


class Accept(Action, key="accept", label="Accept", verdict=Verdict.COMPLETED, outcome="accepted"):
    """Accept with a defaulted note and the retained, read-only record identity."""

    note: str = "Reviewed"
    reference: str = Field(json_schema_extra={"readOnly": True})


class Reject(Action, key="reject", label="Reject", verdict=Verdict.REJECTED, outcome="rejected"):
    """Reject with a nonempty explanation."""

    reason: str = Field(min_length=3)


def seed_group(*, actor: Any, assignees: Sequence[Any], reference: Any, requester: Any = DEFAULT_REQUESTER) -> Any:
    """Admit a real inbox question using its declared action pair.

    Call from a management shell with existing people and a record readable by
    every participant. No test app or test tables are required on that host.
    The frozen form remains usable by the normal ``decide`` action.
    The returned group contains one decision.
    """
    return apps.get_model("decisions", "Decision").objects.admit_group([
        DecisionRequest(
            kind="review_reference", subject=reference, assignees=tuple(assignees), requester=requester,
            actions=(Accept, Reject),
            context=DecisionContext(references=(DecisionRecordReference(
                model=reference._meta.label, id=public_id_of(reference), label=str(reference),
            ),)),
            initial={"accept": {"reference": public_id_of(reference)}},
        ),
    ], actor=actor)
