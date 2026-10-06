"""Native execution reads and manager-backed operator mutations under REBAC."""

from typing import Any
from uuid import uuid4

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel, Field
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows import schema as workflow_schema
from angee.workflows.definition import Definition
from angee.workflows.runner import runner
from angee.workflows.states import AttemptResult, RunStatus, StepRunStatus
from angee.workflows.steps import Step, StepMode
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRecord, StepRun, Workflow, WorkflowRun
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
        "workflows.WorkflowRun", "workflows.StepRecord",
        "workflows.StepRun", "workflows.StepAttempt", "workflows.StepRecord",
        "workflows.Trigger", "workflows.TriggerEvent", "workflows.StepWatch",
    }
    fields = set(schema._schema.mutation_type.fields)
    assert fields == {
        "start_workflow_run", "cancel_workflow_run", "reprocess_workflow_run", "retry_step", "retry_step_accepting_duplicate",
        "enable_workflow_trigger", "disable_workflow_trigger", "revoke_workflow_trigger_grant",
        "insert_trigger_one", "update_trigger_by_pk", "delete_trigger_by_pk",
        "save_workflow_draft", "publish_workflow",
    }
    for name in ("workflow", "workflowversion", "workflowrun",
                 "steprun", "stepattempt", "steprecord"):
        assert {name, f"{name}_by_pk", f"{name}_aggregate"} <= set(schema._schema.query_type.fields)
    assert "draft" in schema._schema.get_type("WorkflowType").fields
    assert "layout" in schema._schema.get_type("WorkflowType").fields
    for type_name, pair, obsolete in (
        ("WorkflowRunType", {"subject_model", "subject_id"}, set()),
        ("StepRecordType", {"record_model", "record_id"}, {"model_label"}),
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
            ctx.record(ctx.run, label="Execution evidence")
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
    artifact = system_queryset(StepRecord).get(step_run=step)
    query = """{
      workflowrun { id origin run_as { id display_name } input output version { workflow { name } } }
      steprun { id node_key run { id } attempts { id } records { label } }
      stepattempt { id number result step_run { id } }
      steprecord { id label record_model record_id step_run { id } }
    }"""
    visible = result_data(execute_schema(schema, query, user=owner))
    assert visible["workflowrun"][0]["input"] == {"value": 7}
    assert visible["workflowrun"][0]["output"] == {"value": 7}
    assert visible["workflowrun"][0]["origin"] == "MANUAL"
    assert visible["workflowrun"][0]["run_as"] == {"id": owner.sqid, "display_name": str(owner)}
    assert visible["steprun"][0]["attempts"] == [{"id": attempt.sqid}]
    assert visible["steprecord"][0]["record_id"] == run.sqid
    assert visible["steprecord"][0]["record_model"] == "workflows.WorkflowRun"
    assert result_data(execute_schema(schema, query, user=other)) == {
        "workflowrun": [], "steprun": [], "stepattempt": [], "steprecord": [],
    }
    for name, record in (("workflowrun", run), ("steprun", step), ("stepattempt", attempt), ("steprecord", artifact)):
        hidden = result_data(execute_schema(
            schema, f"query($id: String!) {{ {name}_by_pk(id: $id) {{ id }} }}",
            {"id": record.sqid}, user=other,
        ))
        assert hidden == {f"{name}_by_pk": None}
    with pytest.raises(PermissionDenied), transaction.atomic():
        StepRecord.objects.with_actor(owner).create(step_run=step, record=run, label="Forbidden insertion")

    labels = """{
      workflow { display_name }
      workflowversion { display_name }
      workflowrun { display_name }
      steprun { display_name }
      stepattempt { display_name }
      steprecord { display_name }
    }"""
    run_label = str(run)
    with CaptureQueriesContext(connection) as one:
        assert result_data(execute_schema(schema, labels, user=owner)) == {
            "workflow": [{"display_name": workflow.name}],
            "workflowversion": [{"display_name": "Version 1"}],
            "workflowrun": [{"display_name": run_label}],
            "steprun": [{"display_name": "entry"}],
            "stepattempt": [{"display_name": "Attempt 1"}],
            "steprecord": [{"display_name": "Execution evidence"}],
        }
    second = start_run(workflow, actor=owner)
    run_until(second)
    with CaptureQueriesContext(connection) as many:
        expanded = result_data(execute_schema(schema, labels, user=owner))
    assert len(expanded["workflowrun"]) == len(expanded["steprecord"]) == 2
    assert len(many) == len(one)
    artifact.label = ""
    assert str(artifact) == artifact.sqid


def test_artifact_reference_is_redacted_for_run_reader_without_source_read(schema, callers, register_step):
    admin, owner, reader = callers
    source = vault_for(owner, name="Private artifact")

    class ArtifactStep(Step[Value, Value, None]):
        key = "private_artifact"

        def run(self, ctx):
            ctx.record(source, label="Private source")
            return ctx.done(ctx.input)

    register_step(ArtifactStep)
    workflow = load_workflow(document("entry", step=ArtifactStep.key), actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", owner)
    run = start_run(workflow, actor=owner, input={"value": 1})
    run_until(run)
    run.with_actor(owner).grant_record_access("reader", reader)
    query = "{ steprecord { record_model record_id } steprun { records { record_model record_id } } }"
    hidden = {"record_model": None, "record_id": None}
    assert result_data(execute_schema(schema, query, user=reader)) == {
        "steprecord": [hidden], "steprun": [{"records": [hidden]}],
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


def test_narrow_outcome_label_uses_published_vocabulary_without_row_queries(schema, execution):
    actor, _sent = execution
    workflow = load_workflow(document("entry"), actor=actor)
    counts = []
    for count in (1, 4):
        while system_queryset(WorkflowRun).count() < count:
            run_until(start_run(workflow, actor=actor))
        with CaptureQueriesContext(connection) as queries:
            data = result_data(execute_schema(schema, "{ workflowrun { outcome outcome_label } }", user=actor))
        assert data["workflowrun"] == [{"outcome": "done", "outcome_label": "Done"}] * count
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
    ("reject", True, True, True, True),
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


@pytest.mark.parametrize("relation,visible", [("viewer", True), ("starter", False), ("operator", False)])
def test_studio_native_field_reads_follow_monitor_on_every_path_and_batch(
    schema, callers, monkeypatch, relation, visible
):
    """Zed redaction agrees on root, run, version and trigger reads at every list size."""
    from rebac import actor_context

    from angee.knowledge import schema as knowledge_schema
    from angee.workflows.testing.drivers import trigger_source
    from angee.workflows.testing.models import Trigger
    from tests.conftest import Vault, make_addon

    admin, editor, reader = callers
    monkeypatch.setattr(GraphQLSchemas, "_discovered", GraphQLSchemas([make_addon(schemas=knowledge_schema.schemas)]))

    def create(key):
        workflow = load_workflow(document("entry"), key=key, actor=admin, subject_model="knowledge.vault")
        workflow.with_actor(admin).grant_record_access(relation, reader)
        run = start_run(workflow, actor=admin, subject=vault_for(admin, name=key))
        run.with_actor(admin).grant_record_access("reader", reader)
        with actor_context(admin), trigger_source(Vault):
            Trigger.objects.create(workflow=workflow, source="record_changed", model_label="knowledge.vault")
        return workflow

    workflow = create("studio_one")
    fields = "id permissions draft draft_revision layout"
    query = f"""query($id: String!) {{
      workflow_by_pk(id: $id) {{ {fields} }}
      workflow {{ {fields} }}
      workflowrun {{ version {{ workflow {{ {fields} }} }} }}
      workflowversion {{ workflow {{ {fields} }} }}
      trigger {{ workflow {{ {fields} }} }}
    }}"""
    with CaptureQueriesContext(connection) as one:
        data = result_data(execute_schema(schema, query, {"id": workflow.sqid}, user=reader))
    rows = [
        data["workflow_by_pk"],
        *data["workflow"],
        *(row["version"]["workflow"] for row in data["workflowrun"]),
        *(row["workflow"] for row in data["workflowversion"]),
        *(row["workflow"] for row in data["trigger"]),
    ]
    assert len(rows) == 5
    for row in rows:
        assert row["permissions"] == (["monitor"] if visible else ["start"] if relation in {"starter", "operator"} else [])
        assert row["draft"] == (document("entry") if visible else None)
        assert row["draft_revision"] == (1 if visible else None)
        assert row["layout"] == ({} if visible else None)
    create("studio_two")
    with CaptureQueriesContext(connection) as many:
        larger = result_data(execute_schema(schema, query, {"id": workflow.sqid}, user=reader))
    assert len(larger["workflow"]) == 2
    assert len(many) == len(one)


def test_studio_save_conflict_precedes_validation_and_publish_checks_revision(schema, callers):
    """Native errors keep stale writes and stale publications from changing the row."""
    admin, editor, reader = callers
    workflow = load_workflow(document("entry"), actor=admin)
    workflow.with_actor(admin).grant_record_access("editor", editor)
    workflow.with_actor(admin).grant_record_access("viewer", reader)
    revision = workflow.draft_revision
    draft = document("renamed")
    query = """mutation($id: ID!, $draft: JSON!, $layout: JSON!, $revision: Int!) {
      save_workflow_draft(id: $id, draft: $draft, layout: $layout, expected_revision: $revision) {
        revision diagnostics { node path code message }
      }
    }"""
    variables = {"id": workflow.sqid, "draft": draft, "layout": {"renamed": [120, 80]}, "revision": revision}
    assert execute_schema(schema, query, variables, user=reader).errors
    saved = result_data(execute_schema(schema, query, variables, user=editor))["save_workflow_draft"]
    assert saved == {"revision": revision + 1, "diagnostics": []}
    variables["draft"] = {"invalid": True}
    stale = execute_schema(schema, query, variables, user=editor)
    assert stale.errors[0].extensions == {"code": "STALE_REVISION", "current_revision": revision + 1}
    assert system_queryset(Workflow).get(pk=workflow.pk).draft == draft
    publish = """mutation($id: ID!, $revision: Int!) {
      publish_workflow(id: $id, expected_revision: $revision) { number dependents }
    }"""
    stale = execute_schema(schema, publish, {"id": workflow.sqid, "revision": revision}, user=editor)
    assert stale.errors[0].extensions["code"] == "STALE_REVISION"
    published = result_data(
        execute_schema(schema, publish, {"id": workflow.sqid, "revision": revision + 1}, user=editor)
    )
    assert published["publish_workflow"] == {"number": 2, "dependents": []}
    unchanged = result_data(
        execute_schema(schema, publish, {"id": workflow.sqid, "revision": revision + 1}, user=editor)
    )
    assert unchanged == published
    variables["revision"] = revision + 1
    invalid = execute_schema(schema, query, variables, user=editor)
    assert invalid.errors[0].extensions["code"] == "VALIDATION"
    assert invalid.errors[0].extensions["validationErrors"]


def test_studio_outcomes_resolve_await_contracts_and_isolate_bad_nodes(schema, callers, register_step):
    """Monitor readers get real child outcomes and located per-node fallbacks."""
    from angee.workflows.awaits import AwaitRun

    class ChoiceConfig(BaseModel):
        minimum: int = Field(default=1, ge=1)
        outcome: str = "approved"

    class ConfiguredStep(Step[Value, Value, ChoiceConfig]):
        key = "configured_studio"
        internal = True

        @classmethod
        def outcomes_for(cls, config):
            return {config.outcome: "Selected outcome"}

    register_step(ConfiguredStep)
    register_step(AwaitRun)
    admin, editor, reader = callers
    child = load_workflow(
        {"nodes": {"entry": {"step": "echo"}}, "results": [{"from": "entry", "as": "accepted"}]},
        key="studio_child",
        actor=admin,
    )
    hidden = load_workflow(
        {"nodes": {"entry": {"step": "echo"}}, "results": [{"from": "entry", "as": "secret_outcome"}]},
        key="hidden", actor=admin,
    )
    assert not read_scoped_queryset(Workflow, reader).filter(pk=hidden.pk).exists()
    workflow = load_workflow(document("entry"), actor=admin)
    workflow.with_actor(admin).grant_record_access("viewer", reader)
    child.with_actor(admin).grant_record_access("viewer", reader)
    query = """query($id: ID!, $configuration: [WorkflowStepConfiguration!]!) {
      workflow_step_choices(id: $id) { key internal config_schema }
      workflow_step_outcomes(id: $id, configurations: $configuration) {
        node outcomes issues { node path code message }
      }
    }"""
    configurations = [
        {"node": "await-client", "step": "await_run", "config": {"expects": child.key}},
        {"node": "configured-client", "step": ConfiguredStep.key, "config": {"outcome": "accepted"}},
        {"node": "bad-config", "step": ConfiguredStep.key, "config": {"minimum": 0}},
        {"node": "bad-outcome", "step": ConfiguredStep.key, "config": {"outcome": "INVALID"}},
        {"node": "retired", "step": "retired", "config": {}},
        {"node": "hidden-child", "step": "await_run", "config": {"expects": "hidden"}},
        {"node": "missing-child", "step": "await_run", "config": {"expects": "missing"}},
    ]
    data = result_data(
        execute_schema(schema, query, {"id": workflow.sqid, "configuration": configurations}, user=reader)
    )
    choice = next(item for item in data["workflow_step_choices"] if item["key"] == ConfiguredStep.key)
    assert choice["internal"] and choice["config_schema"]["properties"]["minimum"]["minimum"] == 1
    by_id = {item["node"]: item for item in data["workflow_step_outcomes"]}
    assert "accepted" in by_id["await-client"]["outcomes"]
    assert by_id["configured-client"]["outcomes"] == {"accepted": "Selected outcome", "error": "Error"}
    for key in ("bad-config", "bad-outcome", "hidden-child"):
        assert by_id[key]["outcomes"]["error"] == "Error"
        assert by_id[key]["issues"] and by_id[key]["issues"][0]["node"] == key
    assert by_id["bad-outcome"]["issues"][0]["path"] == ["nodes", "bad-outcome", "step"]
    assert by_id["bad-outcome"]["issues"][0]["code"] == "outcome"
    saved = Workflow.objects.save_draft(workflow, draft={"nodes": {
        "bad_outcome": {"step": ConfiguredStep.key, "config": {"outcome": "INVALID"}},
    }}, expected_revision=workflow.draft_revision, actor=admin)
    outcome_issue = next(issue for issue in saved.issues if issue.code == "outcome")
    assert outcome_issue.path == ["nodes", "bad_outcome", "step"]
    assert outcome_issue.message == by_id["bad-outcome"]["issues"][0]["message"]
    assert by_id["hidden-child"]["outcomes"] == by_id["missing-child"]["outcomes"]
    assert "secret_outcome" not in str(by_id["hidden-child"])
    hidden_message = by_id["hidden-child"]["issues"][0]["message"].replace("hidden", "missing")
    assert hidden_message == by_id["missing-child"]["issues"][0]["message"]
    assert by_id["retired"]["outcomes"] == {} and by_id["retired"]["issues"][0]["code"] == "unknown_step"


def test_studio_success_keeps_nonblocking_config_diagnostics_and_publish_validation(schema, callers, register_step):
    class Configuration(BaseModel):
        minimum: int = Field(ge=1)

    class Configured(Step[Value, Value, Configuration]):
        key = "studio_validation"

    register_step(Configured)
    admin, editor, reader = callers
    workflow = load_workflow(document("entry"), actor=admin)
    draft = document("entry", step=Configured.key)
    draft["nodes"]["entry"]["config"] = {"minimum": 0}
    saved = result_data(execute_schema(
        schema,
        """mutation($id: ID!, $draft: JSON!, $revision: Int!) {
          save_workflow_draft(id: $id, draft: $draft, layout: {}, expected_revision: $revision) {
            revision diagnostics { node path code message }
          }
        }""",
        {"id": workflow.sqid, "draft": draft, "revision": workflow.draft_revision},
        user=admin,
    ))["save_workflow_draft"]
    assert len(saved["diagnostics"]) == 1
    assert saved["diagnostics"][0]["path"] == ["nodes", "entry", "config", "minimum"]
    published = execute_schema(
        schema,
        "mutation($id: ID!, $revision: Int!) { publish_workflow(id: $id, expected_revision: $revision) { number } }",
        {"id": workflow.sqid, "revision": saved["revision"]},
        user=admin,
    )
    assert published.errors[0].extensions["code"] == "VALIDATION"
    assert "nodes.entry.config.minimum" in published.errors[0].extensions["validationErrors"]


def test_studio_registry_projection_resolves_and_builds_each_choice_once(monkeypatch, register_step):
    """The rowless projection passes one resolved class to the common choice owner."""
    from angee.base import impl as impl_owner
    from angee.base.impl import resolve_all_impl_classes
    from angee.workflows.schema import WorkflowStepChoice

    class Projected(Step[Value, Value, None]):
        key = "studio_projected"

    register_step(Projected)
    resolve = impl_owner.resolve_impl_class
    build = Projected.choice
    resolved = []
    built = []

    def resolve_once(base, key):
        resolved.append(key)
        return resolve(base, key)

    def build_once(cls):
        built.append(cls)
        return build()

    monkeypatch.setattr(impl_owner, "resolve_impl_class", resolve_once)
    monkeypatch.setattr(Projected, "choice", classmethod(build_once))
    choices = [WorkflowStepChoice.from_step(step) for step in resolve_all_impl_classes(Step)]
    assert resolved.count(Projected.key) == 1
    assert built == [Projected]
    projected = next(choice for choice in choices if choice.key == Projected.key)
    assert projected.defaults == build().defaults
    assert projected.outcomes == {"done": "Done", "error": "Error"}


@pytest.mark.parametrize("broken", ["retired", "tightened", "malformed"])
def test_broken_published_child_is_a_located_issue_for_query_save_and_publish(schema, callers, register_step, broken):
    from angee.workflows.awaits import AwaitRun

    class Config(BaseModel):
        minimum: int = 1

    class Child(Step[Value, Value, Config]):
        key = "studio_broken_child"

    register_step(Child)
    register_step(AwaitRun)
    admin, _, _ = callers
    child = load_workflow({"nodes": {"entry": {"step": Child.key}}, "results": [{"from": "entry"}]},
                          key="broken_child", actor=admin)
    parent = load_workflow(document("entry"), actor=admin)
    if broken == "retired":
        from django.conf import settings
        settings.ANGEE_WORKFLOW_STEP_CLASSES.pop(Child.key)
    elif broken == "malformed":
        with system_context(reason="test historical published document no longer parses"):
            version = type(child.published).objects.create(
                workflow=child, number=2, document={"nodes": []}, content_hash="historical",
            )
            system_queryset(Workflow).filter(pk=child.pk).update(published=version)
    else:
        class Tightened(BaseModel):
            minimum: int = Field(ge=2)
        Child.config_model = Tightened
    draft = {"nodes": {"awaited": {"step": "await_run", "config": {"expects": child.key}}}}
    query = """query($id: ID!, $configurations: [WorkflowStepConfiguration!]!) {
      workflow_step_outcomes(id: $id, configurations: $configurations) {
        node outcomes issues { node path code message }
      }
    }"""
    data = result_data(execute_schema(schema, query, {"id": parent.sqid, "configurations": [
        {"node": "awaited", "step": "await_run", "config": {"expects": child.key}},
    ]}, user=admin))["workflow_step_outcomes"][0]
    assert data["issues"][0]["code"] == "expected_workflow"
    assert data["issues"][0]["path"] == ["nodes", "awaited", "config", "expects"]
    saved = Workflow.objects.save_draft(parent, draft=draft, expected_revision=parent.draft_revision, actor=admin)
    assert any(issue.code == "expected_workflow" for issue in saved.issues)
    with pytest.raises(ValidationError, match="invalid published contract"):
        Workflow.objects.publish(parent, expected_revision=saved.revision, actor=admin)


def test_await_resolution_is_batched_and_configurations_are_bounded(callers, register_step):
    from angee.workflows.awaits import AwaitRun

    register_step(AwaitRun)
    admin, _, _ = callers
    children = [load_workflow(document("entry"), key=f"batch_{index}", actor=admin) for index in range(3)]
    def entries(count):
        return [{"node": f"node_{index}", "step": "await_run", "config": {"expects": children[index % 3].key}}
                for index in range(count)]
    with CaptureQueriesContext(connection) as one:
        Workflow.objects.authoring_outcomes(entries(1), actor=admin)
    with CaptureQueriesContext(connection) as many:
        Workflow.objects.authoring_outcomes(entries(10), actor=admin)
    assert len(many) == len(one)
    version_table = Workflow._meta.get_field("published").related_model._meta.db_table
    contract_queries = [query for query in many if f'JOIN "{version_table}"' in query["sql"]]
    assert len(contract_queries) == 1
    with pytest.raises(ValidationError, match="100"):
        Workflow.objects.authoring_outcomes(entries(101), actor=admin)

@pytest.mark.parametrize("config", [[], "invalid", 1])
def test_outcome_configuration_wire_shape_errors_are_located_native_validation(schema, callers, config):
    admin, _, _ = callers
    workflow = load_workflow(document("entry"), actor=admin)
    query = """query($id: ID!, $configurations: [WorkflowStepConfiguration!]!) {
      workflow_step_outcomes(id: $id, configurations: $configurations) { node outcomes }
    }"""
    result = execute_schema(schema, query, {"id": workflow.sqid, "configurations": [
        {"node": "valid", "step": "echo", "config": {}},
        {"node": "bad", "step": "echo", "config": config},
    ]}, user=admin)
    assert result.errors[0].extensions["code"] == "VALIDATION"
    assert "configurations.1.config" in result.errors[0].extensions["validationErrors"]


def test_outcome_configuration_bound_and_entry_errors_share_the_manager_adapter(schema, callers):
    admin, _, _ = callers
    workflow = load_workflow(document("entry"), actor=admin)
    query = """query($id: ID!, $configurations: [WorkflowStepConfiguration!]!) {
      workflow_step_outcomes(id: $id, configurations: $configurations) { node outcomes }
    }"""
    result = execute_schema(schema, query, {"id": workflow.sqid, "configurations": [
        {"node": str(index), "step": "echo", "config": []} for index in range(101)
    ]}, user=admin)
    assert result.errors[0].extensions["code"] == "VALIDATION"
    assert "100" in str(result.errors[0].extensions["validationErrors"]["configurations"])


def start_on_record(schema, workflow, subject, actor, *, input=None, request_key=None):
    """Use the public record verb with the client's admission identity."""
    return result_data(execute_schema(schema, """mutation(
      $workflow: ID!, $subject: WorkflowRunSubjectInput!, $input: JSON!, $key: String!
    ) {
      start_workflow_run(workflow_id: $workflow, subject: $subject, input: $input, request_key: $key) {
        ok message id validation_errors
      }
    }""", {
        "workflow": workflow.sqid, "subject": {"model": subject._meta.label, "id": subject.sqid},
        "input": {} if input is None else input, "key": request_key or str(uuid4()),
    }, user=actor))["start_workflow_run"]


def test_record_start_as_starter_replays_the_same_request(schema, callers):
    admin, actor, _ = callers
    subject = vault_for(actor)
    workflow = load_workflow(document("entry"), actor=admin, subject_model=subject._meta.label)
    workflow.with_actor(admin).grant_record_access("starter", actor)
    key = str(uuid4())
    started = start_on_record(schema, workflow, subject, actor, request_key=key)
    assert started["ok"] and started["message"] == f"{workflow.name} started"
    assert start_on_record(schema, workflow, subject, actor, request_key=key) == started
    run = WorkflowRun.objects.with_actor(actor).get()
    assert (run.sqid, run.run_as_id, run.subject_object_id) == (started["id"], actor.pk, subject.pk)
    refused = start_on_record(schema, workflow, subject, actor, input={"value": 1}, request_key=key)
    assert not refused["ok"] and "different request" in refused["message"]
    assert system_queryset(WorkflowRun).count() == 1


@pytest.mark.parametrize("refusal", ["start", "subject", "model", "input", "publication"])
def test_record_start_refuses_unavailable_or_invalid_admission(schema, callers, refusal):
    admin, actor, other = callers
    subject = vault_for(other if refusal == "subject" else actor)
    workflow = load_workflow(document("entry"), actor=admin, subject_model=subject._meta.label,
                             publish=refusal != "publication")
    workflow.with_actor(admin).grant_record_access("viewer" if refusal == "start" else "starter", actor)
    result = start_on_record(schema, workflow, workflow if refusal == "model" else subject, actor,
                             input={"value": "invalid"} if refusal == "input" else {})
    assert not result["ok"] and result["id"] is None
    assert not system_queryset(WorkflowRun).exists()
    if refusal == "input":
        assert result["validation_errors"]["input.value"]


def test_record_workflow_filters_list_only_published_startable_matching_workflows(schema, callers, register_step):
    admin, actor, outsider = callers
    subject = vault_for(actor)

    class Inputs(BaseModel):
        source_id: str = Field(json_schema_extra={"relation": {"resource": "knowledge.Vault"}})

    class RecordInput(Step[Inputs, Value, None]):
        key = "record_action_input"

    register_step(RecordInput)
    for key, model, published, grant in (
        ("offered", subject._meta.label, True, "starter"),
        ("unpublished", subject._meta.label, False, "starter"),
        ("wrong_model", Workflow._meta.label, True, "starter"),
        ("viewer_only", subject._meta.label, True, "viewer"),
    ):
        workflow = load_workflow(document("entry", step=RecordInput.key), key=key, actor=admin,
                                 subject_model=model, publish=published)
        workflow.with_actor(admin).grant_record_access(grant, actor)
    query = """query($models: [String!]!) {
      workflow(where: {subject_model: {_in: $models}, can_start: {_eq: true}, is_published: {_eq: true}}) {
        id key permissions published { input_schema }
      }
    }"""
    variables = {"models": [subject._meta.label]}
    rows = result_data(execute_schema(schema, query, variables, user=actor))["workflow"]
    assert len(rows) == 1 and rows[0]["key"] == "offered" and rows[0]["permissions"] == ["start"]
    input_schema = rows[0]["published"]["input_schema"]
    assert input_schema["required"] == ["source_id"]
    assert input_schema["properties"]["source_id"]["relation"] == {"resource": "knowledge.Vault"}
    assert result_data(execute_schema(schema, query, variables, user=outsider))["workflow"] == []
    offered = Workflow.objects.with_actor(actor).get(key="offered")
    assert not start_on_record(schema, offered, subject, actor)["ok"]
    assert start_on_record(schema, offered, subject, actor, input={"source_id": subject.sqid})["ok"]


def test_published_graph_with_unregistered_steps_projects_no_start_contract(schema, callers):
    from angee.workflows.testing.drivers import register_steps

    admin, actor, _ = callers
    subject = vault_for(actor)

    class Retired(Step[Value, Value, None]):
        key = "record_action_retired"

    with register_steps(Retired):
        workflow = load_workflow(document("entry", step=Retired.key), key="retired", actor=admin,
                                 subject_model=subject._meta.label, publish=True)
    workflow.with_actor(admin).grant_record_access("starter", actor)
    rows = result_data(execute_schema(schema, """query($models: [String!]!) {
      workflow(where: {subject_model: {_in: $models}, can_start: {_eq: true}, is_published: {_eq: true}}) {
        key published { input_schema }
      }
    }""", {"models": [subject._meta.label]}, user=actor))["workflow"]
    assert rows == [{"key": "retired", "published": {"input_schema": None}}]


def test_record_start_accepts_a_workflow_for_the_canonical_mti_ancestor(schema, callers):
    from tests.mtidemo.models import MtiChild, MtiParent

    admin, actor, _ = callers
    with system_context(reason="test manual workflow MTI subject"):
        subject = MtiChild.objects.create(title="Shared record", detail="Concrete record")
        write_relationships([RelationshipTuple(to_object_ref(subject), "reader", to_subject_ref(actor))])
    workflow = load_workflow(document("entry"), actor=admin, subject_model=MtiParent._meta.label)
    workflow.with_actor(admin).grant_record_access("starter", actor)
    rows = result_data(execute_schema(schema, """query($models: [String!]!) {
      workflow(where: {subject_model: {_in: $models}, can_start: {_eq: true}, is_published: {_eq: true}}) { id }
    }""", {"models": [MtiParent._meta.label, MtiChild._meta.label]}, user=actor))["workflow"]
    assert rows == [{"id": workflow.sqid}]
    assert start_on_record(schema, workflow, subject, actor)["ok"]
    assert WorkflowRun.objects.with_actor(actor).get().subject_model_class is MtiParent
