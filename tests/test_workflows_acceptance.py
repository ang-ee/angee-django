"""Acceptance scenario for workflow runs, decisions, gates, and journal rows."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context, to_subject_ref

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.steps import DecisionSpec, HandlerStep, StepResult
from tests.workflows import (
    Decision,
    Edge,
    Step,
    StepRun,
    Workflow,
    advance_once,
    execute_started,
    start_run,
    step_run_for,
)

User = get_user_model()
pytest_plugins = ("tests.workflows",)


def test_run_reopens_invalid_decision_then_completes_gate_and_journal(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run resumes from decisions, records the journal DAG, and succeeds."""

    del workflow_engine_tables, no_workflow_queue
    assignee = User.objects.create_user(username="workflow-decision-assignee")
    assignee_ref = str(to_subject_ref(assignee))

    def run_handler(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        if step_run.step.key == "entry":
            return StepResult.suspend(
                resume_state={"phase": "awaiting-password"},
                decisions=[
                    DecisionSpec(
                        assignees=(assignee_ref,),
                        action="enter-password",
                        max_attempts=3,
                        decision_schema={
                            "type": "object",
                            "required": ["password"],
                            "properties": {"password": {"type": "string", "const": "correct"}},
                        },
                    )
                ],
            )
        if step_run.step.key == "produce":
            return StepResult.done(
                output={"produced": True, "entry": step_run.input},
                outcome="ready",
            )
        return StepResult.done(output={}, outcome="done")

    monkeypatch.setattr(HandlerStep, "run", run_handler)
    workflow = _workflow_for_acceptance(assignee_ref)

    run = start_run(workflow)
    advance_once(run)
    execute_started(run)
    entry = step_run_for(run, "entry")
    password_decision = _decision_for(entry)

    engine.decide(password_decision, "complete", payload={"password": "wrong"}, actor=assignee)
    password_decision.refresh_from_db()
    entry.refresh_from_db()
    with system_context(reason="test workflow acceptance journal after invalid"):
        assert list(StepRun.objects.filter(run=run).values_list("pk", flat=True)) == [entry.pk]
    assert password_decision.verdict == workflow_models.Verdict.PENDING
    assert password_decision.attempts == 1
    assert entry.status == workflow_models.StepRunStatus.WAITING

    engine.decide(password_decision, "complete", payload={"password": "correct"}, actor=assignee)
    password_decision.refresh_from_db()
    entry.refresh_from_db()
    assert password_decision.verdict == workflow_models.Verdict.COMPLETED
    assert password_decision.attempts == 1
    assert password_decision.resolution == {"password": "correct"}
    assert password_decision.resolved_by == assignee_ref
    assert entry.status == workflow_models.StepRunStatus.SUCCEEDED

    advance_once(run)
    execute_started(run)
    produce = step_run_for(run, "produce")
    assert produce.status == workflow_models.StepRunStatus.SUCCEEDED
    assert produce.output["produced"] is True

    advance_once(run)
    execute_started(run)
    review = step_run_for(run, "review")
    review_decision = _decision_for(review)
    engine.decide(review_decision, "complete", payload={"approved": True}, actor=assignee)
    review_decision.refresh_from_db()
    review.refresh_from_db()
    advance_once(run)
    run.refresh_from_db()

    assert review_decision.verdict == workflow_models.Verdict.COMPLETED
    assert review_decision.resolution == {"approved": True}
    assert review.status == workflow_models.StepRunStatus.SUCCEEDED
    assert review.outcome == "completed"
    assert run.status == workflow_models.RunStatus.SUCCEEDED
    with system_context(reason="test workflow acceptance journal dag"):
        assert list(produce.previous.all()) == [entry]
        assert list(review.previous.all()) == [produce]
        assert list(StepRun.objects.filter(run=run).order_by("pk").values_list("step__key", flat=True)) == [
            "entry",
            "produce",
            "review",
        ]


def _workflow_for_acceptance(assignee_ref: str) -> Workflow:
    with system_context(reason="test workflow acceptance definition"):
        draft = Workflow.objects.create(name="Decision acceptance")
        entry = Step.objects.create(workflow=draft, key="entry", name="Entry", is_entry=True)
        produce = Step.objects.create(workflow=draft, key="produce", name="Produce")
        review = Step.objects.create(
            workflow=draft,
            key="review",
            name="Review",
            step_class="gate",
            config={
                "policy": "one_done",
                "action": "review-output",
                "slots": [{"assignee": assignee_ref}],
            },
            join_rule=workflow_models.JoinRule.ALL_SUCCESS,
        )
        Edge.objects.create(workflow=draft, source=entry, target=produce, condition="completed")
        Edge.objects.create(workflow=draft, source=produce, target=review, condition="ready")
        return draft.publish()


def _decision_for(step_run: Any) -> Any:
    with system_context(reason="test workflow acceptance decision"):
        return Decision.objects.get(step_run=step_run)
