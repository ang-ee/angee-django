"""Record work, holds and the linear projection share the execution owners."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.models.functions import Now
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
    assert result["trigger_event"]["record_id"] == record.sqid
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
    actor, _, _, question = review
    run, step = start_review(review)
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 1
    system_queryset(WorkflowRun).filter(pk=run.pk).update(created_at=Now() - timedelta(days=40))
    assert WorkflowRun.objects.prune() == 0
    assert system_queryset(StepAttempt).filter(step_run=step).exists()
    if retired_actor:
        system_queryset(type(actor)).filter(pk=actor.pk).update(is_active=False)
    with system_context(reason="test delete reviewed record"):
        question.review_subject.delete()
    run.refresh_from_db()
    assert run.status == "canceled"
    decision = questions(step)[0]
    assert decision.verdict == [] and decision.answered_by_id == actor.pk
    assert runner.wake_decisions() == 0


def test_retention_keeps_a_failed_run_with_an_open_parallel_question(review, settings, register_step):
    class Fail(Step[None, None, None]):
        key = "timeline_parallel_failure"
        def run(self, ctx):
            raise ValueError("Needs retry")
    register_step(Fail)
    run, step = start_review(review, graph={"nodes": {
        "entry": {"step": "echo", "next": {"done": ["review", "z_failure"]}},
        "review": {"step": "question"}, "z_failure": {"step": Fail.key},
    }})
    assert run.status == "failed" and questions(step)[0].is_open
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 1
    system_queryset(WorkflowRun).filter(pk=run.pk).update(finished_at=Now() - timedelta(days=40))
    assert WorkflowRun.objects.prune() == 0
    assert system_queryset(StepAttempt).filter(step_run__run=run).exists()
    assert system_queryset(WorkflowRun).get(pk=run.pk).prune_reason == "A decision is still open."


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
