"""Native execution reads and manager-backed operator mutations under REBAC."""

from typing import Any

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows import schema as workflow_schema
from angee.workflows.states import AttemptResult, RunStatus, StepRunStatus
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepArtifact, StepAttempt, StepRun, WorkflowRun
from tests.conftest import SchemaAddon, execute_schema, result_data
from tests.workflow_steps import Value, document

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


@pytest.fixture
def schema():
    """Compose the same console bucket the installed addon contributes."""

    parts = {key: tuple(workflow_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}
    return GraphQLSchemas([SchemaAddon({"console": parts})]).build("console")


@pytest.fixture
def callers(execution):
    """Separate workflow author, execution principal and unrelated requester."""

    admin, _sent = execution
    with system_context(reason="workflow GraphQL caller fixtures"):
        owner = get_user_model().objects.create_user(username="workflow-owner")
        other = get_user_model().objects.create_user(username="workflow-other")
    return admin, owner, other


def action(schema, name, target, actor):
    """Send an operator request through the assembled GraphQL execution boundary."""

    return execute_schema(
        schema, f"mutation($id: ID!) {{ {name}(id: $id) {{ ok id }} }}",
        {"id": target.sqid}, user=actor,
    )


def test_execution_resources_expose_reads_without_engine_crud(schema):
    """Resource metadata and roots provide no generic engine-row mutation path."""

    resources = {resource.model_label: resource for resource in schema.angee_resources}
    assert set(resources) == {
        "workflows.WorkflowRun", "workflows.StepRun", "workflows.StepAttempt", "workflows.StepArtifact",
    }
    fields = set(schema._schema.mutation_type.fields)
    assert fields == {
        "cancel_workflow_run", "reprocess_workflow_run", "retry_step", "retry_step_accepting_duplicate",
    }
    for name in ("workflowrun", "steprun", "stepattempt", "stepartifact"):
        assert {name, f"{name}_by_pk", f"{name}_aggregate"} <= set(schema._schema.query_type.fields)
    assert "draft" not in schema._schema.get_type("WorkflowType").fields
    assert "layout" not in schema._schema.get_type("WorkflowType").fields


def test_run_owner_reads_execution_evidence_but_another_starter_cannot(schema, callers, register_step):
    """Run, step, attempt and artifact reads inherit the same execution visibility."""

    admin, owner, other = callers

    class EvidenceStep(Step[Value, Value, None]):
        """Record a real artifact through the body context before settlement."""

        key = "graphql_evidence"

        def run(self, ctx):
            """Retain the run's public identity as neutral execution evidence."""
            ctx.artifact(ctx.run, label="Execution evidence")
            return ctx.done(ctx.input)

    register_step(EvidenceStep)
    workflow = load_workflow(document("entry", step=EvidenceStep.key), actor=admin)
    for actor in (owner, other):
        workflow.with_actor(admin).grant_record_access("starter", actor)
    run = start_run(workflow, actor=owner, input={"value": 7})
    run_until(run)
    assert run.status == RunStatus.SUCCEEDED
    step = system_queryset(StepRun).get(run=run)
    attempt = system_queryset(StepAttempt).get(step_run=step)
    artifact = system_queryset(StepArtifact).get(step_run=step)
    query = """{
      workflowrun { id origin run_as input output version { workflow { name } } }
      steprun { id node_key run { id } attempts { id } artifacts { label } }
      stepattempt { id number result step_run { id } }
      stepartifact { id label model_label record_id step_run { id } }
    }"""
    visible = result_data(execute_schema(schema, query, user=owner))
    assert visible["workflowrun"][0]["input"] == {"value": 7}
    assert visible["workflowrun"][0]["output"] == {"value": 7}
    assert visible["workflowrun"][0]["origin"] == "manual"
    assert visible["workflowrun"][0]["run_as"] == owner.sqid
    assert visible["steprun"][0]["attempts"] == [{"id": attempt.sqid}]
    assert visible["stepartifact"][0]["record_id"] == run.sqid
    assert visible["stepartifact"][0]["model_label"] == "workflows.WorkflowRun"
    assert result_data(execute_schema(schema, query, user=other)) == {
        "workflowrun": [], "steprun": [], "stepattempt": [], "stepartifact": [],
    }
    for name, record in (("workflowrun", run), ("steprun", step), ("stepattempt", attempt), ("stepartifact", artifact)):
        hidden = result_data(execute_schema(
            schema, f"query($id: String!) {{ {name}_by_pk(id: $id) {{ id }} }}",
            {"id": record.sqid}, user=other,
        ))
        assert hidden == {f"{name}_by_pk": None}
    with pytest.raises(PermissionDenied), transaction.atomic():
        StepArtifact.objects.with_actor(owner).create(step_run=step, record=run, label="Forbidden insertion")


def test_cancel_action_checks_requester_before_calling_owner(schema, callers):
    """An unrelated or anonymous requester cannot cancel another user's run."""

    admin, owner, other = callers
    workflow = load_workflow(document("entry"), actor=admin)
    for actor in (owner, other):
        workflow.with_actor(admin).grant_record_access("starter", actor)
    run = start_run(workflow, actor=owner)
    denied = result_data(action(schema, "cancel_workflow_run", run, other))
    assert not denied["cancel_workflow_run"]["ok"]
    assert action(schema, "cancel_workflow_run", run, None).errors
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.RUNNING
    assert result_data(action(schema, "cancel_workflow_run", run, owner))["cancel_workflow_run"]["ok"]
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.CANCELED


def test_reprocess_action_returns_replacement_id_and_operator_attribution(schema, callers):
    """Reprocessing delegates current-version admission and preserves its predecessor."""

    admin, owner, operator = callers
    workflow = load_workflow(document("entry"), actor=admin)
    for actor in (owner, operator):
        workflow.with_actor(admin).grant_record_access("starter", actor)
    run = start_run(workflow, actor=owner, input={"value": 3})
    run_until(run)
    run.with_actor(owner).grant_record_access("operator", operator)
    result = result_data(action(schema, "reprocess_workflow_run", run, operator))["reprocess_workflow_run"]
    assert result["ok"] and result["id"] != run.sqid
    replacement = system_queryset(WorkflowRun).get(reprocess_of=run)
    assert result["id"] == replacement.sqid
    assert replacement.run_as_id == operator.pk and replacement.input == run.input
    assert replacement.origin == "reprocess"
    rows = result_data(execute_schema(
        schema, "query($id: String!) { workflowrun_by_pk(id: $id) { origin reprocess_of { id } } }",
        {"id": replacement.sqid}, user=operator,
    ))
    assert rows["workflowrun_by_pk"] == {"origin": "reprocess", "reprocess_of": {"id": run.sqid}}


def test_retry_action_uses_run_permission_instead_of_engine_row_write(schema, callers):
    """A run owner can retry a failed step while generic step writes remain forbidden."""

    admin, owner, reader = callers
    workflow = load_workflow(document("entry", step="reject"), actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", owner)
    run = start_run(workflow, actor=owner)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    run.with_actor(owner).grant_record_access("reader", reader)
    for name in ("retry_step", "retry_step_accepting_duplicate"):
        assert not result_data(action(schema, name, step, reader))[name]["ok"]
        assert action(schema, name, step, None).errors
    assert system_queryset(StepRun).get(pk=step.pk).status == StepRunStatus.FAILED
    assert not step.with_actor(owner).has_access("write")
    assert result_data(action(schema, "retry_step", step, owner))["retry_step"]["ok"]
    assert system_queryset(StepRun).get(pk=step.pk).status == StepRunStatus.READY
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.RUNNING


def test_duplicate_retry_action_records_requesting_operator(schema, callers, register_step):
    """Only explicit acknowledgment permits retry after an uncertain external effect."""

    admin, owner, operator = callers

    class UncertainStep(Step[Value, Value, None]):
        """Time out after an external-effect marker on the initial attempt."""

        key = "graphql_uncertain"
        mode = "IO"
        effect_idempotent = False

        def run(self, ctx):
            """Finish only the explicitly retried attempt."""
            if ctx.attempt.number == 1:
                ctx.begin_effect()
                raise SoftTimeLimitExceeded()
            return ctx.done(ctx.input)

    register_step(UncertainStep)
    workflow = load_workflow(document("entry", step=UncertainStep.key), actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", owner)
    run = start_run(workflow, actor=owner)
    run.with_actor(owner).grant_record_access("operator", operator)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    assert step.status == StepRunStatus.FAILED
    initial = system_queryset(StepAttempt).get(step_run=step)
    assert initial.result == AttemptResult.TIMED_OUT and initial.effect_started_at is not None
    assert not result_data(action(schema, "retry_step", step, operator))["retry_step"]["ok"]
    acknowledged = result_data(action(schema, "retry_step_accepting_duplicate", step, operator))
    assert acknowledged["retry_step_accepting_duplicate"]["ok"]
    run_until(run)
    attempts = list(system_queryset(StepAttempt).filter(step_run=step).order_by("number"))
    assert [attempt.acknowledged_by_id for attempt in attempts] == [None, operator.pk]
    visible = result_data(execute_schema(
        schema, "{ stepattempt(order_by: {number: asc}) { number acknowledged_by } }", user=operator,
    ))
    assert visible["stepattempt"] == [
        {"number": 1, "acknowledged_by": None}, {"number": 2, "acknowledged_by": operator.sqid},
    ]


def test_narrow_origin_and_actor_projection_does_not_fetch_each_run(schema, execution):
    """Computed execution identity declares its native optimizer field dependencies."""

    actor, _sent = execution
    workflow = load_workflow(document("entry"), actor=actor)
    counts = []
    for count in (1, 4):
        while system_queryset(WorkflowRun).count() < count:
            start_run(workflow, actor=actor)
        with CaptureQueriesContext(connection) as queries:
            data: dict[str, Any] = result_data(execute_schema(
                schema, "{ workflowrun { origin run_as display_name } }", user=actor,
            ))
        assert len(data["workflowrun"]) == count
        counts.append(len(queries))
    assert counts[0] == counts[1]


def test_attempt_error_and_stacktrace_share_the_declared_field_gate(schema, callers, register_step):
    """Every reader of an attempt's error can read its trace; hidden attempts expose neither."""
    admin, owner, other = callers

    class DiagnosticFailure(Step[Value, Value, None]):
        key = "diagnostic_failure"

        def run(self, ctx):
            raise ValueError("The declared input could not be processed.")

    register_step(DiagnosticFailure)
    workflow = load_workflow(document("entry", step=DiagnosticFailure.key), actor=admin)
    for actor in (owner, other):
        workflow.with_actor(admin).grant_record_access("starter", actor)
    run = start_run(workflow, actor=owner)
    run_until(run)
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    query = "query($id: String!) { stepattempt_by_pk(id: $id) { error stacktrace } }"
    for actor in (admin, owner, other):
        allowed = actor != other
        assert attempt.with_actor(actor).has_access("read__error") is allowed
        assert attempt.with_actor(actor).has_access("read__stacktrace") is allowed
        data = result_data(execute_schema(schema, query, {"id": attempt.sqid}, user=actor))
        if allowed:
            assert data["stepattempt_by_pk"]["error"] == attempt.error
            assert "ValueError" in data["stepattempt_by_pk"]["stacktrace"]
        else:
            assert data["stepattempt_by_pk"] is None
