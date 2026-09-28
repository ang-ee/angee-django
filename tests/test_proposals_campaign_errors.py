"""D26's wire refusal codes must survive the shared action envelope."""

from dataclasses import asdict

import pytest

from angee.graphql.actions import ActionResult
from angee.proposals.models import ClarificationWidenBlocked, PublishedQuestion


@pytest.mark.parametrize("error_type", (ClarificationWidenBlocked, PublishedQuestion))
def test_publication_refusal_keeps_its_domain_code_in_the_action_result(
    error_type: type[ClarificationWidenBlocked] | type[PublishedQuestion],
) -> None:
    """Exercise the exact envelope owner even while composed hosts are blocked."""
    error = error_type()
    payload = asdict(ActionResult.from_error(error, "Question publication refused."))
    assert payload["ok"] is False
    assert payload.get("code") == error.code
    assert payload["validation_errors"]
