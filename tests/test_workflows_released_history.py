"""Import paths that released workflow migrations name stay loadable."""

from __future__ import annotations

from angee.workflows.attempts import AttemptCause, AttemptResultKind
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import _empty_workflow_output_schema


def test_released_workflow_history_constants_keep_their_values() -> None:
    # Pre-rebuild migrations subscript these by member name inside constraints.
    assert AttemptCause["AUTOMATIC_RETRY"] == "automatic_retry"
    assert AttemptCause["TEST_FIXTURE"] == "test_fixture"
    assert AttemptResultKind["TRANSIENT_ERROR"] == "transient_error"
    assert [kind.value for kind in WorkflowDispatchKind] == [
        "advance", "execute", "decision_expire", "decision_escalate",
        "artifact_delivery", "child_cancel", "run_cancel", "run_settle",
    ]
    assert _empty_workflow_output_schema() == {"type": "object", "properties": {}, "additionalProperties": False}
