"""Read-boundary coverage for retained workflow inspection evidence."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from graphql import parse, validate
from rebac import system_context

from angee.workflows import engine
from angee.workflows.steps import HandlerStep, StepResult
from tests.conftest import execute_schema, result_data
from tests.test_workflows import _console_schema, _published_workflow
from tests.workflows import WorkflowDispatch, advance_once

User = get_user_model()


def test_artifact_target_reference_is_a_computed_object_not_an_unowned_relation() -> None:
    """The native artifact list can select the authorized target reference payload."""

    schema = _console_schema()
    resource = next(
        item for item in schema.angee_resources
        if item.model_label == "workflows.StepArtifact"
    )
    display = next(field for field in resource.fields if field.name == "target_reference")
    query = resource.query.fields["target_reference"]

    assert display.kind == "object"
    assert display.relation_model_label is None
    assert display.relation_object is False
    assert query.kind == "object"
    assert query.relation is None
    assert validate(
        schema._schema,
        parse("""
          query ArtifactRows($attempt: String!) {
            workflow_step_artifacts(where: {attempt: {_eq: $attempt}}, limit: 20) {
              id declaration_index label target_reference { model id } created_at
            }
          }
        """),
    ) == []


@pytest.fixture()
def handler_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Give the abstract handler a concrete operation for retained fixtures."""

    calls: list[dict[str, Any]] = []

    def run(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        calls.append({"step_run": step_run.pk})
        return StepResult.done(output={"visible": True}, outcome="done")

    monkeypatch.setattr(HandlerStep, "run", run)
    return calls


@pytest.mark.django_db(transaction=True)
def test_attempt_resource_lists_bounded_summary_and_reads_selected_payload(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """The list can stay payload-free while the selected detail reads retained evidence."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    schema = _console_schema()
    owner = User.objects.create_user(username="workflow-inspection-reader")
    subject, workflow = _published_workflow(
        name="Readable inspection workflow",
        subject_declaration="workflows.workflow",
        owner=owner,
    )
    run = engine.start(workflow, subject=subject, actor=owner)
    assert run.created_by == owner
    step_run = advance_once(run)[0]
    with system_context(reason="test workflow inspection attempt"):
        attempt = step_run.current_attempt
        assert WorkflowDispatch.objects.filter(step_attempt=attempt).exists()

    summary_query = """
      query AttemptSummary($stepRun: String!) {
        workflow_step_attempts(
          where: {step_run: {_eq: $stepRun}}
          order_by: [{ordinal: asc}]
          limit: 1
        ) {
          id ordinal status cause result_kind
        }
      }
    """
    summary = result_data(
        execute_schema(schema, summary_query, {"stepRun": step_run.sqid}, user=owner)
    )["workflow_step_attempts"]
    assert summary == [
        {
            "id": attempt.sqid,
            "ordinal": attempt.ordinal,
            "status": "claimed",
            "cause": "initial",
            "result_kind": "",
        }
    ]

    detail_query = """
      query AttemptPayload($id: String!) {
        workflow_step_attempts_by_pk(id: $id) {
          id input_present input input_provenance
          output_present output checkpoint_present checkpoint error stacktrace
        }
      }
    """
    detail = result_data(execute_schema(schema, detail_query, {"id": attempt.sqid}, user=owner))[
        "workflow_step_attempts_by_pk"
    ]
    assert detail["id"] == attempt.sqid
    assert detail["input_present"] is True
    assert detail["input"] == {}
    assert detail["input_provenance"] == {"kind": "automatic"}
    assert detail["output_present"] is False
    assert detail["checkpoint_present"] is False
    assert detail["error"] in (None, "")
    assert detail["stacktrace"] in (None, "")


@pytest.mark.django_db(transaction=True)
def test_attempt_resource_denies_list_and_guessed_detail_without_step_run_read(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """An attempt identifier grants no visibility beyond its owning logical execution."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    schema = _console_schema()
    owner = User.objects.create_user(username="workflow-inspection-owner")
    plain = User.objects.create_user(username="workflow-inspection-plain")
    subject, workflow = _published_workflow(
        name="Private inspection workflow",
        subject_declaration="workflows.workflow",
        owner=owner,
    )
    run = engine.start(workflow, subject=subject, actor=owner)
    step_run = advance_once(run)[0]
    with system_context(reason="test denied workflow inspection attempt"):
        attempt = step_run.current_attempt

    query = """
      query DeniedAttempt($run: String!, $stepRun: String!, $attempt: String!) {
        workflow_runs(limit: 10) { id status }
        workflow_runs_by_pk(id: $run) { id status }
        workflow_runs_aggregate { aggregate { count } }
        workflow_step_runs(limit: 10) { id status }
        workflow_step_runs_by_pk(id: $stepRun) { id status }
        workflow_step_runs_aggregate { aggregate { count } }
        workflow_step_attempts(limit: 10) { id ordinal status }
        workflow_step_attempts_by_pk(id: $attempt) { id status step_run { id run { id } } }
        workflow_step_attempts_aggregate { aggregate { count } }
      }
    """
    denied = result_data(
        execute_schema(
            schema,
            query,
            {"run": run.sqid, "stepRun": step_run.sqid, "attempt": attempt.sqid},
            user=plain,
        )
    )
    assert denied["workflow_runs"] == []
    assert denied["workflow_runs_by_pk"] is None
    assert denied["workflow_runs_aggregate"]["aggregate"]["count"] == 0
    assert denied["workflow_step_runs"] == []
    assert denied["workflow_step_runs_by_pk"] is None
    assert denied["workflow_step_runs_aggregate"]["aggregate"]["count"] == 0
    assert denied["workflow_step_attempts"] == []
    assert denied["workflow_step_attempts_by_pk"] is None
    assert denied["workflow_step_attempts_aggregate"]["aggregate"]["count"] == 0
