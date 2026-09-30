"""Native execution reads and manager-backed operator mutations under REBAC."""

from typing import Any

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows import schema as workflow_schema
from angee.workflows.definition import Definition
from angee.workflows.runner import runner
from angee.workflows.states import AttemptResult, RunStatus, StepRunStatus
from angee.workflows.steps import Step, StepMode
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepArtifact, StepAttempt, StepRun, WorkflowRun
from tests.conftest import SchemaAddon, create_user, execute_schema, result_data, vault_for
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
        "workflows.Workflow", "workflows.WorkflowVersion",
        "workflows.WorkflowRun", "workflows.WorkflowRunEvidence",
        "workflows.StepRun", "workflows.StepAttempt", "workflows.StepArtifact",
        "workflows.Trigger", "workflows.TriggerEvent", "workflows.StepWatch",
    }
    fields = set(schema._schema.mutation_type.fields)
    assert fields == {
        "cancel_workflow_run", "reprocess_workflow_run", "retry_step", "retry_step_accepting_duplicate",
        "enable_workflow_trigger", "disable_workflow_trigger", "revoke_workflow_trigger_grant",
        "insert_trigger_one", "update_trigger_by_pk", "delete_trigger_by_pk",
    }
    for name in ("workflow", "workflowversion", "workflowrun", "workflowrunevidence",
                 "steprun", "stepattempt", "stepartifact"):
        assert {name, f"{name}_by_pk", f"{name}_aggregate"} <= set(schema._schema.query_type.fields)
    assert "draft" not in schema._schema.get_type("WorkflowType").fields
    assert "layout" not in schema._schema.get_type("WorkflowType").fields
    for type_name, pair, obsolete in (
        ("WorkflowRunType", {"subject_model", "subject_id"}, set()),
        ("StepArtifactType", {"record_model", "record_id"}, {"model_label"}),
        ("StepWatchType", {"record_model", "record_id"}, {"record_model_label", "record_public_id"}),
        ("TriggerEventType", {"record_model", "record_id"}, set()),
    ):
        fields = set(schema._schema.get_type(type_name).fields)
        assert pair <= fields
        assert not obsolete & fields


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
    write_relationships([
        RelationshipTuple(resource=to_object_ref(owner), relation="directory_reader", subject=to_subject_ref(owner)),
    ])
    step = system_queryset(StepRun).get(run=run)
    attempt = system_queryset(StepAttempt).get(step_run=step)
    artifact = system_queryset(StepArtifact).get(step_run=step)
    query = """{
      workflowrun { id origin run_as { id display_name } input output version { workflow { name } } }
      steprun { id node_key run { id } attempts { id } artifacts { label } }
      stepattempt { id number result step_run { id } }
      stepartifact { id label record_model record_id step_run { id } }
    }"""
    visible = result_data(execute_schema(schema, query, user=owner))
    assert visible["workflowrun"][0]["input"] == {"value": 7}
    assert visible["workflowrun"][0]["output"] == {"value": 7}
    assert visible["workflowrun"][0]["origin"] == "MANUAL"
    assert visible["workflowrun"][0]["run_as"] == {"id": owner.sqid, "display_name": str(owner)}
    assert visible["steprun"][0]["attempts"] == [{"id": attempt.sqid}]
    assert visible["stepartifact"][0]["record_id"] == run.sqid
    assert visible["stepartifact"][0]["record_model"] == "workflows.WorkflowRun"
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

    labels = """{
      workflow { display_name }
      workflowversion { display_name }
      workflowrun { display_name }
      steprun { display_name }
      stepattempt { display_name }
      stepartifact { display_name }
    }"""
    with CaptureQueriesContext(connection) as one:
        assert result_data(execute_schema(schema, labels, user=owner)) == {
            "workflow": [{"display_name": workflow.name}],
            "workflowversion": [{"display_name": "Version 1"}],
            "workflowrun": [{"display_name": run.sqid}],
            "steprun": [{"display_name": "entry"}],
            "stepattempt": [{"display_name": "Attempt 1"}],
            "stepartifact": [{"display_name": "Execution evidence"}],
        }
    second = start_run(workflow, actor=owner)
    run_until(second)
    with CaptureQueriesContext(connection) as many:
        expanded = result_data(execute_schema(schema, labels, user=owner))
    assert len(expanded["workflowrun"]) == len(expanded["stepartifact"]) == 2
    assert len(many) == len(one)
    artifact.label = ""
    assert str(artifact) == artifact.sqid


def test_artifact_reference_is_redacted_for_run_reader_without_source_read(schema, callers, register_step):
    admin, owner, reader = callers
    source = vault_for(owner, name="Private artifact")

    class ArtifactStep(Step[Value, Value, None]):
        key = "private_artifact"

        def run(self, ctx):
            ctx.artifact(source, label="Private source")
            return ctx.done(ctx.input)

    register_step(ArtifactStep)
    workflow = load_workflow(document("entry", step=ArtifactStep.key), actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", owner)
    run = start_run(workflow, actor=owner, input={"value": 1})
    run_until(run)
    run.with_actor(owner).grant_record_access("reader", reader)
    query = "{ stepartifact { record_model record_id } steprun { artifacts { record_model record_id } } }"
    hidden = {"record_model": None, "record_id": None}
    assert result_data(execute_schema(schema, query, user=reader)) == {
        "stepartifact": [hidden], "steprun": [{"artifacts": [hidden]}],
    }


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
    assert rows["workflowrun_by_pk"] == {"origin": "REPROCESS", "reprocess_of": {"id": run.sqid}}


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
        mode = StepMode.IO
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
    facts = result_data(execute_schema(schema, """{
      workflowrun { version { id } can_reprocess step_runs { can_retry requires_duplicate_acknowledgement } }
    }""", user=operator))["workflowrun"]
    assert facts == [{
        "version": None, "can_reprocess": False,
        "step_runs": [{"can_retry": True, "requires_duplicate_acknowledgement": True}],
    }]
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
                schema, "{ workflowrun { origin run_as { id display_name } display_name } }", user=actor,
            ))
        assert len(data["workflowrun"]) == count
        counts.append(len(queries))
    assert counts[0] == counts[1]


def test_narrow_step_run_relation_projection_does_not_fetch_each_foreign_key(schema, execution):
    """A selected forward relation includes its FK column in the parent query."""

    actor, _sent = execution
    workflow = load_workflow(document("entry"), actor=actor)
    counts = []
    for count in (1, 5):
        while system_queryset(StepRun).count() < count:
            run_until(start_run(workflow, actor=actor))
        with CaptureQueriesContext(connection) as queries:
            data = result_data(execute_schema(schema, "{ steprun { run { id } } }", user=actor))
        assert len(data["steprun"]) == count
        counts.append(len(queries))
    assert counts[0] == counts[1]


def test_diagnostics_distinguish_run_operators_editors_and_readers(schema, callers, register_step):
    """Errors need editing authority; traces require execution operator authority."""
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
    editor, viewer, reader, operator = (create_user(f"diagnostic-{role}")
                                         for role in ("editor", "viewer", "reader", "operator"))
    for role, actor in (("editor", editor), ("viewer", viewer)):
        workflow.with_actor(admin).grant_record_access(role, actor)
    for role, actor in (("reader", reader), ("operator", operator)):
        run.with_actor(owner).grant_record_access(role, actor)
    query = """query($id: String!, $run: String!) {
      stepattempt_by_pk(id: $id) { error stacktrace }
      workflowrun_by_pk(id: $run) { error }
    }"""
    for actor, error, trace in ((editor, True, False), (admin, True, True), (owner, True, True),
                                (operator, True, True), (viewer, False, False), (reader, False, False),
                                (other, False, False)):
        assert attempt.with_actor(actor).has_access("read__error") is error
        assert attempt.with_actor(actor).has_access("read__stacktrace") is trace
        assert run.with_actor(actor).has_access("read__error") is error
        data = result_data(execute_schema(schema, query, {"id": attempt.sqid, "run": run.sqid}, user=actor))
        if actor == other:
            assert data["stepattempt_by_pk"] is None
            assert data["workflowrun_by_pk"] is None
        else:
            assert data["stepattempt_by_pk"]["error"] == (attempt.error if error else None)
            assert data["workflowrun_by_pk"]["error"] == (run.error if error else None)
            if trace:
                assert "ValueError" in data["stepattempt_by_pk"]["stacktrace"]
            else:
                assert data["stepattempt_by_pk"]["stacktrace"] is None


def test_run_validation_failure_retains_field_context(schema, execution, monkeypatch):
    """A run-level failure renders validation text without replacing successful step evidence."""
    actor, _sent = execution
    workflow = load_workflow(document("entry"), actor=actor)
    run = start_run(workflow, actor=actor)

    def invalid_result(*args, **kwargs):
        raise ValidationError({"result": "The result is no longer available."})

    monkeypatch.setattr(Definition, "result_for", invalid_result)
    run_until(run)
    assert run.status == RunStatus.FAILED
    assert result_data(execute_schema(schema, """{
      workflowrun { error }
      stepattempt { error result }
    }""", user=actor)) == {
        "workflowrun": [{"error": "result: The result is no longer available."}],
        "stepattempt": [{"error": "", "result": "SUCCEEDED"}],
    }


def test_terminal_cancel_reports_open_step_cleanup_without_claiming_run_cancellation(schema, execution):
    """Decision 38 leaves terminal facts intact and accurately reports row cleanup."""
    actor, _sent = execution
    workflow = load_workflow({"nodes": {
        "entry": {"step": "echo", "next": {"done": ["failure", "remaining"]}},
        "failure": {"step": "reject"}, "remaining": {"step": "echo"},
    }}, actor=actor)
    run = start_run(workflow, actor=actor)
    run_until(run)
    assert run.status == "failed"
    assert run.can_cancel(actor)
    retained = (run.status, run.outcome, run.output, run.error, run.finished_at)
    query = "mutation($id: ID!) { cancel_workflow_run(id: $id) { ok message } }"
    first = result_data(execute_schema(schema, query, {"id": run.sqid}, user=actor))
    assert first["cancel_workflow_run"] == {
        "ok": True, "message": "Run already finished; 1 open step canceled.",
    }
    second = result_data(execute_schema(schema, query, {"id": run.sqid}, user=actor))
    assert second["cancel_workflow_run"] == {"ok": True, "message": "Nothing to cancel."}
    run = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (run.status, run.outcome, run.output, run.error, run.finished_at) == retained
    assert not run.can_cancel(actor)


def test_origin_enum_has_native_choice_labels(schema):
    """The stored origin uses the same labelled enum projection as its choices."""
    origin = schema._schema.get_type("RunOrigin")
    assert {key: value.description for key, value in origin.values.items()} == {
        "MANUAL": "Manual", "WORKFLOW": "Workflow", "REPROCESS": "Reprocess", "TRIGGER": "Trigger",
    }


@pytest.mark.parametrize(("step_key", "settle", "cancel", "reprocess", "retry"), [
    ("echo", False, True, False, False),
    ("echo", True, False, True, False),
    ("reject", True, False, True, True),
    ("pause", True, True, False, False),
])
def test_viewer_facts_share_state_and_permission_owners(schema, callers, step_key, settle, cancel, reprocess, retry):
    """A reader sees execution but never gains an operator affordance from its state."""
    admin, owner, reader = callers
    workflow = load_workflow(document("entry", step=step_key), actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", owner)
    run = start_run(workflow, actor=owner)
    run.with_actor(owner).grant_record_access("reader", reader)
    row = system_queryset(StepRun).get(run=run)
    if settle:
        runner.execute(row.pk)
    query = """{
      workflowrun { can_cancel can_reprocess step_runs { can_retry requires_duplicate_acknowledgement } }
    }"""
    for actor, expected in ((owner, (cancel, reprocess, retry)), (reader, (False, False, False))):
        data = result_data(execute_schema(schema, query, user=actor))["workflowrun"]
        assert data == [{
            "can_cancel": expected[0], "can_reprocess": expected[1],
            "step_runs": [{"can_retry": expected[2], "requires_duplicate_acknowledgement": False}],
        }]


def test_error_routed_failure_does_not_offer_retry(schema, callers):
    """Routing an error consumes recovery even while its successor remains waiting."""
    admin, owner, _reader = callers
    workflow = load_workflow({"nodes": {
        "entry": {"step": "reject", "next": {"error": "recovery"}}, "recovery": {"step": "pause"},
    }}, actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", owner)
    run = start_run(workflow, actor=owner)
    runner.execute(system_queryset(StepRun).get(run=run, node_key="entry").pk)
    data = result_data(execute_schema(schema, """{
      steprun(where: {node_key: {_eq: "entry"}}) { status can_retry }
    }""", user=owner))
    assert data["steprun"] == [{"status": "FAILED", "can_retry": False}]


def test_step_rows_follow_graph_order_even_when_database_order_differs(schema, callers):
    """Stored ranks survive out-of-order planning and a viewer-redacted version."""
    admin, owner, operator = callers
    workflow = load_workflow({"nodes": {
        "start": {"step": "echo", "next": {"done": ["branch_a", "branch_b"]}},
        "branch_a": {"step": "echo", "next": {"done": "a_child"}},
        "branch_b": {"step": "echo", "next": {"done": "b_child"}},
        "a_child": {"step": "echo", "next": {"done": "finish"}},
        "b_child": {"step": "echo", "next": {"done": "finish"}},
        "finish": {"step": "echo", "join": "all", "input": {"from": "a_child"}},
    }, "results": [{"from": "finish"}]}, actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", owner)
    run = start_run(workflow, actor=owner)
    runner.execute(system_queryset(StepRun).get(run=run, node_key="start").pk)
    runner.execute(system_queryset(StepRun).get(run=run, node_key="branch_b").pk)
    run_until(run)
    assert run.status == "succeeded"
    run.with_actor(owner).grant_record_access("operator", operator)
    rows = system_queryset(StepRun).filter(run=run)
    assert list(rows.order_by("pk").values_list("node_key", flat=True)) == [
        "start", "branch_a", "branch_b", "b_child", "a_child", "finish",
    ]
    expected = ["start", "branch_a", "branch_b", "a_child", "b_child", "finish"]
    assert list(rows.values_list("node_key", flat=True)) == expected
    for actor in (owner, operator):
        data = result_data(execute_schema(schema, """{
          workflowrun { version { id } step_runs { node_key } }
        }""", user=actor))["workflowrun"][0]
        assert [row["node_key"] for row in data["step_runs"]] == expected
        assert (data["version"] is None) is (actor == operator)
