"""Read-boundary coverage for retained workflow inspection evidence."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import strawberry
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from graphql import GraphQLEnumType, GraphQLObjectType, get_named_type, parse, validate
from rebac import system_context
from strawberry.schema.config import StrawberryConfig

from angee.testing.models import StepAttempt, Workflow, WorkflowDispatch, WorkflowRun
from angee.workflows import engine
from angee.workflows.attempts import AttemptResult, AttemptResultKind, LeaseRevocationReason
from angee.workflows.models import RunStatus, StepRunStatus, WaitingKind
from angee.workflows.steps import StepResult
from tests.conftest import execute_schema, result_data
from tests.test_workflows import _console_schema, _published_workflow
from tests.workflows import FixtureStep, advance_once

User = get_user_model()
# Schema resolves concrete workflow models registered by the fixture imports above.
workflow_schema = importlib.import_module("angee.workflows.schema")


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
def fixture_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record calls made through the concrete test fixture operation."""

    calls: list[dict[str, Any]] = []

    def run(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        calls.append({"step_run": step_run.pk})
        return StepResult.done(output={"visible": True}, outcome="done")

    monkeypatch.setattr(FixtureStep, "run", run)
    return calls


@pytest.mark.django_db(transaction=True)
def test_attempt_resource_lists_bounded_summary_and_reads_selected_payload(
    composed_tables: None,
    no_workflow_queue: None,
    fixture_calls: list[dict[str, Any]],
) -> None:
    """The list can stay payload-free while the selected detail reads retained evidence."""

    del composed_tables, no_workflow_queue, fixture_calls
    schema = _console_schema()
    owner = User.objects.create_user(username="workflow-inspection-reader")
    subject, workflow = _published_workflow(
        name="Readable inspection workflow",
        subject_declaration="workflows.workflow",
        owner=owner,
    )
    run = engine.start(workflow, subject=subject, actor=owner)
    assert run.created_by == owner
    assert run.parent_relation is None
    root = result_data(execute_schema(schema, """
      query RootRun($id: String!) {
        workflow_runs(where: {id: {_eq: $id}}) { id parent_relation waiting_kind }
      }
    """, {"id": run.sqid}, user=owner))["workflow_runs"]
    assert root == [{"id": run.sqid, "parent_relation": None, "waiting_kind": None}]
    step_run = advance_once(run)[0]
    with pytest.raises(ValidationError, match="Parent relationship"):
        engine.start(workflow, subject=subject, actor=owner, parent_step_run=step_run, parent_relation="")
    with system_context(reason="test workflow inspection attempt"):
        with pytest.raises(ValidationError, match="Parent relationship"):
            WorkflowRun.objects.create(workflow=workflow, parent_step_run=step_run, parent_relation="")
        attempt = step_run.current_attempt
        assert WorkflowDispatch.objects.filter(step_attempt=attempt).exists()

    summary_query = """
      query AttemptSummary($stepRun: String!) {
        workflow_step_attempts(
          where: {step_run: {_eq: $stepRun}}
          order_by: [{ordinal: asc}]
          limit: 1
        ) {
          id ordinal status cause result_kind lease_revocation_reason
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
            "result_kind": None,
            "lease_revocation_reason": None,
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

    StepAttempt.objects.revoke(
        attempt.pk,
        lease_token=attempt.lease_token,
        reason=LeaseRevocationReason.SUPERSEDED,
        at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="late result"),
        recorded_at=timezone.now(),
    )
    retained = result_data(execute_schema(schema, """
      query ReturnedAttempts($stepRun: String!) {
        workflow_step_attempts(
          where: {step_run: {_eq: $stepRun}, result_kind: {_eq: "error"}}
          limit: 1
        ) { id result_kind lease_revocation_reason }
      }
    """, {"stepRun": step_run.sqid}, user=owner))["workflow_step_attempts"]
    assert retained == [{
        "id": attempt.sqid,
        "result_kind": "ERROR",
        "lease_revocation_reason": "SUPERSEDED",
    }]

    resource = next(item for item in schema.angee_resources if item.model_label == "workflows.StepAttempt")
    for name in ("result_kind", "lease_revocation_reason"):
        display = next(field for field in resource.fields if field.name == name)
        assert display.kind == "enum"
        assert display.model_field_name == name
        assert resource.query.fields[name].nullable is True
    result_filter = resource.query.fields["result_kind"].filter
    assert result_filter is not None
    assert result_filter.scalar == "String"
    assert ("ERROR", "error") in {(entry.from_value, entry.to_value) for entry in result_filter.value_map}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", list(WaitingKind))
def test_run_and_step_waiting_kind_share_native_enum(
    composed_tables: None,
    no_workflow_queue: None,
    kind: WaitingKind,
) -> None:
    """Stored and annotation-backed wait reasons share one nullable enum."""

    del composed_tables, no_workflow_queue
    schema = _console_schema()
    owner = User.objects.create_user(username="workflow-waiting-reader")
    subject, workflow = _published_workflow(
        name="Waiting inspection workflow", subject_declaration="workflows.workflow", owner=owner,
    )
    run = engine.start(workflow, subject=subject, actor=owner)
    row = advance_once(run)[0]
    with system_context(reason="test waiting inspection projection"):
        row.mark_waiting(waiting_kind=kind)
        run.refresh_from_db()
        run.mark_waiting()
    assert row.status == StepRunStatus.WAITING
    assert run.status == RunStatus.WAITING

    data = result_data(execute_schema(schema, """
      query WaitingRows($run: String!, $step: String!) {
        workflow_runs_by_pk(id: $run) { waiting_kind }
        workflow_step_runs_by_pk(id: $step) { waiting_kind }
      }
    """, {"run": run.sqid, "step": row.sqid}, user=owner))
    assert data == {
        "workflow_runs_by_pk": {"waiting_kind": kind.name},
        "workflow_step_runs_by_pk": {"waiting_kind": kind.name},
    }
    run_type = schema._schema.get_type("WorkflowRunType")
    step_type = schema._schema.get_type("StepRunType")
    assert isinstance(run_type, GraphQLObjectType)
    assert isinstance(step_type, GraphQLObjectType)
    wait_enum = get_named_type(step_type.fields["waiting_kind"].type)
    assert isinstance(wait_enum, GraphQLEnumType)
    assert get_named_type(run_type.fields["waiting_kind"].type) is wait_enum


@pytest.mark.django_db(transaction=True)
def test_attempt_resource_denies_list_and_guessed_detail_without_step_run_read(
    composed_tables: None,
    no_workflow_queue: None,
    fixture_calls: list[dict[str, Any]],
) -> None:
    """An attempt identifier grants no visibility beyond its owning logical execution."""

    del composed_tables, no_workflow_queue, fixture_calls
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


@pytest.mark.django_db(transaction=True)
def test_decision_target_projection_reuses_one_authorized_lookup_per_viewer_request(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = User.objects.create_user(username="target-reader")
    stranger = User.objects.create_user(username="target-stranger")
    with system_context(reason="test decision target projection"):
        target = Workflow.objects.create(name="Private target", created_by=owner)
    decision = SimpleNamespace(target_model=target._meta.label, target_id=str(target.sqid), target_tab="details")
    lookup = Mock(wraps=workflow_schema.instance_for_id)
    monkeypatch.setattr(workflow_schema, "instance_for_id", lookup)

    @strawberry.type
    class Query:
        @strawberry.field
        def decision(self) -> workflow_schema.DecisionTargetFields:
            return decision

    schema = strawberry.Schema(query=Query, config=StrawberryConfig(auto_camel_case=False))
    document = (
        "{ decision { target_model target_id target_tab target_label"
        " target_reference { model id tab label } } }"
    )
    for actor, expected in ((owner, str(target.sqid)), (stranger, None), (owner, str(target.sqid))):
        context = SimpleNamespace(request=SimpleNamespace(user=actor))
        result = schema.execute_sync(document, context_value=context)
        assert result.errors is None
        row = result.data["decision"]
        assert row["target_id"] == expected
        # target_label reuses the same memoized projection as target_reference,
        # so selecting the flat label column adds no per-row authorized lookup.
        assert row["target_label"] == ("Private target" if expected else None)
        assert row["target_reference"] == (
            {"model": target._meta.label, "id": expected, "tab": "details", "label": "Private target"}
            if expected
            else None
        )
        assert lookup.call_count == 1
        lookup.reset_mock()
