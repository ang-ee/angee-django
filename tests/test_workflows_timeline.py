"""Record work, holds and the linear projection share the execution owners."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.functions import Now
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.workflows.decision_steps import AskDecision, DecisionStep
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRecord, StepRun, Trigger, WorkflowRun
from tests.conftest import execute_schema, result_data, vault_for
from tests.test_workflows_children import child_graph as child_graph
from tests.test_workflows_review import questions, start_review
from tests.test_workflows_review import review as review
from tests.test_workflows_review_graphql import schema as schema
from tests.test_workflows_triggers import capture
from tests.test_workflows_triggers import trigger_resource_schema as trigger_resource_schema
from tests.test_workflows_triggers import trigger_setup as trigger_setup

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


def timeline(schema, actor, records):
    return result_data(execute_schema(schema, """query($records:[TimelineRecordInput!]!) {
      record_timeline(records:$records) { open_decision_count records { record_id
        decisions { id } runs { id parent_step { id run { id } } graph { nodes { key plan step_run {
          id hold notes { tone message } records { operation record_id } child_runs { id } } } } } } }
    }""", {"records": [{"model": record._meta.label, "id": record.sqid} for record in records]}, user=actor))[
        "record_timeline"
    ]


@pytest.mark.parametrize("operation,canceled", [("read", False), ("created", True), ("changed", True)])
def test_deletion_only_enqueues_active_runs_that_modify_the_record(review, register_step, operation, canceled):
    actor, _, sent, question = review
    target = vault_for(actor, name="Deletion target")

    class Touch(question):
        key = "deletion_touch"

        def ask(self, ctx):
            ctx.record(target, operation=operation)
            return super().ask(ctx)

    register_step(Touch)
    workflow = load_workflow({"nodes": {"entry": {"step": Touch.key}}}, actor=actor)
    run = start_run(workflow, actor=actor, subject=question.review_subject, input={})
    run_until(run)
    assert run.status == "waiting", [(s.status, s.output) for s in system_queryset(StepRun).filter(run=run)]
    sent.clear()
    system_queryset(type(target)).filter(pk=target.pk).delete()
    cancellations = [payload["kwargs"]["run_id"] for name, payload in sent if name == "workflows.cancel"]
    assert cancellations == ([run.pk] if canceled else [])


def test_timeline_bounds_inputs_and_calls_the_set_owner_once(schema, review, monkeypatch):
    actor, _, _, question = review
    run, _ = start_review(review)
    from angee.workflows.managers import WorkflowRunQuerySet

    calls = []
    original = WorkflowRunQuerySet.about

    def about(self, records, **kwargs):
        calls.append(records)
        return original(self, records, **kwargs)

    monkeypatch.setattr(WorkflowRunQuerySet, "about", about)
    record = question.review_subject
    assert timeline(schema, actor, [record, record])["records"][0]["runs"][0]["id"] == run.sqid
    assert len(calls) == 1 and len(calls[0]) == 2
    latest = [start_run(run.version.workflow, actor=actor, subject=record, input={}, request_key=f"bounded-{index}")
              for index in range(22)]
    result = timeline(schema, actor, [record])["records"][0]["runs"]
    assert len(result) == 20 and result[-1]["id"] == latest[-1].sqid
    assert run.sqid not in {item["id"] for item in result}
    result = execute_schema(
        schema, "query($records:[TimelineRecordInput!]!) { record_timeline(records:$records) { open_decision_count } }",
        {"records": [{"model": record._meta.label, "id": record.sqid}] * 101}, user=actor,
    )
    assert result.errors and "at most 100" in result.errors[0].message


def test_attention_read_does_not_load_the_run_graph(schema, review, monkeypatch):
    actor, _, _, question = review
    start_review(review)
    from angee.workflows import schema as timeline_schema

    def refuse_graph(*args, **kwargs):
        raise AssertionError("The eager probe must not materialize runs")

    monkeypatch.setattr(timeline_schema, "run_type_get_queryset", refuse_graph)
    record = question.review_subject
    result = result_data(execute_schema(schema, """query($records:[TimelineRecordInput!]!) {
      record_timeline(records:$records, include_runs:false) { has_runs open_decision_count
        records { record_id runs { id } decisions { id is_open proposal } } }
    }""", {"records": [{"model": record._meta.label, "id": record.sqid}]}, user=actor))["record_timeline"]
    assert result["has_runs"] and result["open_decision_count"] == 1
    assert result["records"][0]["runs"] == []


@pytest.mark.parametrize("include_runs", [True, False])
def test_timeline_open_decision_records_are_readable(schema, review, include_runs):
    _actor, people, _, question = review
    _, step = start_review(review)
    record = question.review_subject
    data = result_data(execute_schema(schema, """query($records:[TimelineRecordInput!]!, $include_runs:Boolean!) {
      record_timeline(records:$records, include_runs:$include_runs) { records {
        decisions { id is_open records { record_model record_id } }
      } }
    }""", {"records": [{"model": record._meta.label, "id": record.sqid}], "include_runs": include_runs},
        user=people[0]))["record_timeline"]["records"][0]
    assert data["decisions"] == [{
        "id": questions(step)[0].sqid, "is_open": True,
        "records": [{"record_model": record._meta.label, "record_id": record.sqid}],
    }]


def test_about_uses_separate_indexed_identity_queries(review):
    actor, _, _, question = review
    run, _ = start_review(review)
    with CaptureQueriesContext(connection) as captured:
        assert WorkflowRun.objects.with_actor(actor).about(question.review_subject).get().pk == run.pk
    sql = "\n".join(query["sql"] for query in captured)
    assert 'JOIN "test_workflows_step_record"' not in sql
    assert '"content_type_id"' in sql and '"subject_content_type_id"' in sql


def test_timeline_projects_run_subject_trigger_records_and_decision(schema, trigger_setup, register_step):
    actor, _, record, _ = trigger_setup

    class Question(DecisionStep[None, None, None]):
        key = "timeline_trigger_question"

        def ask(self, ctx):
            return ctx.ask(DecisionRequest(
                kind="Confirm", records=(ctx.subject,), assignees=(actor,),
                proposal=DecisionProposal(alternatives=[{"key": "keep", "label": "Keep", "outcome": "done"}]),
            ))

    register_step(Question)
    workflow = load_workflow({"nodes": {"entry": {"step": Question.key}}},
                             key="timeline-trigger", actor=actor, subject_model=record._meta.label)
    with actor_context(actor):
        trigger = Trigger.objects.create(workflow=workflow, source="record_changed", model_label=record._meta.label)
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    assert Trigger.objects.drain() == 1
    run = WorkflowRun.objects.with_actor(actor).get(trigger_event__trigger=trigger)
    run_until(run)
    query = """query($records: [TimelineRecordInput!]!) {
      record_timeline(records: $records) { records { decisions { id } runs {
        id subject_model subject_id trigger_event { record_model record_id trigger { display_name } }
        graph { nodes { key rank step_run { hold records { record_model record_id }
          decision { id is_open records { record_model record_id } } } } }
      } } }
    }"""
    data = result_data(execute_schema(schema, query, {"records": [
        {"model": record._meta.label, "id": record.sqid},
    ]}, user=actor))["record_timeline"]["records"][0]
    result = data["runs"][0]
    assert (result["subject_model"], result["subject_id"]) == (record._meta.label, record.sqid)
    assert (result["trigger_event"]["record_model"], result["trigger_event"]["record_id"]) == (
        record._meta.label, record.sqid,
    )
    step = result["graph"]["nodes"][0]["step_run"]
    assert step["hold"] == "decision" and step["decision"]["is_open"]
    assert step["records"][0]["record_id"] == record.sqid
    assert step["decision"]["records"][0]["record_id"] == record.sqid
    assert data["decisions"] == [{"id": step["decision"]["id"]}]
    from angee.graphql.events import ChangeRelatedRecord

    concern = ChangeRelatedRecord(record._meta.label, record.sqid)
    with system_context(reason="tests.timeline.change_concerns"):
        stored = WorkflowRun.objects.get(sqid=run.sqid)
        assert concern in stored.change_related_records()
        assert concern in stored.step_runs.get(node_key="entry").change_related_records()
        assert concern in stored.step_runs.get(node_key="entry").decision.change_related_records()


def test_symbolic_subject_actions_ask_hold_apply_and_record_both_directions(review, register_step, schema):
    actor, people, _, question = review
    register_step(AskDecision)
    target = question.review_subject
    workflow = load_workflow({"nodes": {"ask": {"step": "ask_decision", "config": {
        "kind": "rename", "assignees": [people[0].sqid],
        "proposal": {"alternatives": [{"key": "rename", "label": "Rename", "outcome": "renamed",
            "actions": {"subject": {"fields": {"name": {"set": "Chosen"}}}}}]},
    }}}, "results": [{"from": "ask", "when": ["renamed"], "as": "renamed"}]}, actor=actor)
    run = start_run(workflow, actor=actor, subject=target)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    assert step.hold == "decision"
    decision = step.decision
    assert target.sqid in decision.proposal["alternatives"][0]["actions"]
    decision.decide(actor=people[0], chosen=["rename"])
    run_until(run)
    target.refresh_from_db()
    assert target.name == "Chosen" and run.status == "succeeded" and run.outcome == "renamed"
    operations = StepRecord.objects.with_actor(actor).filter(step_run=step).values_list("operation", flat=True)
    assert set(operations) == {"read", "changed"}
    assert WorkflowRun.objects.with_actor(actor).about(target).get().pk == run.pk
    data = timeline(schema, actor, [target])
    assert data["records"][0]["runs"][0]["id"] == run.sqid


def test_three_holds_are_queryable(schema, review, child_graph, register_step):
    actor, _, _, _ = review
    decision_run, _ = start_review(review)
    _, _, _, build = child_graph
    parent, _ = build()
    run_until(parent)
    class Fail(Step[None, None, None]):
        key = "timeline_fail"
        def run(self, ctx):
            raise ValueError("Needs retry")
    register_step(Fail)
    failed = start_run(load_workflow({"nodes": {"fail": {"step": Fail.key}}}, actor=actor), actor=actor)
    run_until(failed)
    query = "query($where:steprun_bool_exp) { steprun(where:$where) { hold } }"
    for hold in ("decision", "run", "error"):
        data = result_data(execute_schema(schema, query, {"where": {"hold": {"_eq": hold}}}, user=actor))
        assert data["steprun"] and all(row["hold"] == hold for row in data["steprun"])


def test_a_routed_failure_is_done_and_does_not_hold_the_next_question(review, schema, register_step):
    actor, _people, _sent, question = review
    class Fail(Step[None, None, None]):
        key = "timeline_routed_failure"
        def run(self, ctx):
            raise ValueError("Use the fallback")
    register_step(Fail)
    run, _step = start_review(review, graph={"nodes": {
        "failure": {"step": Fail.key, "next": {"error": "review"}},
        "review": {"step": "question", "input": {}},
    }})
    failed = system_queryset(StepRun).get(run=run, node_key="failure")
    assert run.status == "waiting" and failed.hold is None
    data = timeline(schema, actor, [question.review_subject])["records"][0]["runs"][0]
    assert {node["key"]: node["plan"] for node in data["graph"]["nodes"]} == {"failure": "done", "review": "current"}
    assert data["graph"]["nodes"][0]["step_run"]["hold"] is None
    filtered = result_data(execute_schema(schema, '{ steprun(where:{hold:{_eq:"error"}}) { id hold } }', user=actor))
    assert filtered["steprun"] == []


def test_timeline_child_ancestors_and_record_selection(child_graph, schema):
    actor, _, admitted, build = child_graph
    record = vault_for(actor)
    other = vault_for(actor, name="Other record")
    parent, _ = build(subject=record)
    run_until(parent)
    child = admitted[0]
    second = start_run(load_workflow({"nodes": {"entry": {"step": "echo"}}}, actor=actor), actor=actor, subject=record)
    run_until(second)
    data = timeline(schema, actor, [record, other])
    assert len(data["records"]) == 2
    assert [run["id"] for run in data["records"][0]["runs"]] == [parent.sqid, child.sqid, second.sqid]
    assert data["records"][1]["runs"] == []


def test_linear_plan_certain_join_and_optional_branches(review, register_step, schema):
    actor, _, _, question = review
    class End(Step[None, None, None]):
        key = "timeline_end"
        def run(self, ctx):
            ctx.note("Used the saved value", tone="info")
            return ctx.done(ctx.input)
    register_step(End)
    run, _ = start_review(review, graph={"nodes": {
        "review": {"step": "question", "next": {"approved": "left", "rejected": "right", "done": "finish"}},
        "left": {"step": End.key, "next": {"done": "finish"}},
        "right": {"step": End.key, "next": {"done": "finish"}}, "finish": {"step": End.key, "input": {}},
    }})
    nodes = timeline(schema, actor, [question.review_subject])["records"][0]["runs"][0]["graph"]["nodes"]
    assert {node["key"]: node["plan"] for node in nodes} == {
        "review": "current", "left": "optional", "right": "optional", "finish": "certain",
    }


@pytest.mark.parametrize("retired_actor", [False, True])
def test_open_question_and_attempts_survive_retention_then_record_deletion_withdraws(review, settings, retired_actor):
    actor, _, sent, question = review
    run, step = start_review(review)
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 1
    system_queryset(WorkflowRun).filter(pk=run.pk).update(created_at=Now() - timedelta(days=40))
    assert WorkflowRun.objects.prune() == 0
    assert system_queryset(StepAttempt).filter(step_run=step).exists()
    if retired_actor:
        system_queryset(type(actor)).filter(pk=actor.pk).update(is_active=False)
    with system_context(reason="test delete reviewed record"):
        question.review_subject.delete()
    from angee.workflows.tasks import cancel
    for name, payload in sent:
        if name == "workflows.cancel":
            cancel(**payload["kwargs"])
    run.refresh_from_db()
    assert run.status == "canceled"
    decision = questions(step)[0]
    assert decision.verdict == [] and decision.answered_by_id == actor.pk
    assert runner.wake_decisions() == 0


def test_terminal_withdrawal_allows_retention_to_prune_the_failed_run(review, settings, register_step):
    class Fail(Step[None, None, None]):
        key = "timeline_parallel_failure"
        def run(self, ctx):
            raise ValueError("Needs retry")
    register_step(Fail)
    run, step = start_review(review, graph={"nodes": {
        "entry": {"step": "echo", "next": {"done": ["review", "z_failure"]}},
        "review": {"step": "question"}, "z_failure": {"step": Fail.key},
    }})
    assert run.status == "failed" and not questions(step)[0].is_open
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 1
    system_queryset(WorkflowRun).filter(pk=run.pk).update(finished_at=Now() - timedelta(days=40))
    assert WorkflowRun.objects.prune() == 1
    assert not system_queryset(StepAttempt).filter(step_run__run_id=run.pk).exists()


def test_stop_error_hold_disables_retry_and_removes_the_future_plan(execution, register_step):
    actor, _sent = execution
    class Fail(Step[None, None, None]):
        key = "timeline_stop_error"
        def run(self, ctx):
            raise ValueError("Needs retry")
    register_step(Fail)
    run = start_run(load_workflow({"nodes": {
        "failure": {"step": Fail.key, "next": {"done": "finish"}}, "finish": {"step": "echo", "input": {}},
    }}, actor=actor), actor=actor)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    assert step.hold == "error" and step.can_retry(actor) and run.can_cancel(actor)
    WorkflowRun.objects.cancel(run, actor=actor)
    run.refresh_from_db()
    step.refresh_from_db()
    assert run.status == "failed" and run.stopped_at is not None
    assert step.hold is None and not step.can_retry(actor) and not run.can_cancel(actor)
    assert {node.node.key: node.plan for node in run.graph(actor).nodes} == {"failure": "done", "finish": "not_run"}
    with pytest.raises(ValidationError, match="cannot be retried"):
        StepRun.objects.retry_step(step, actor=actor)
