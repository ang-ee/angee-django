"""Tests for the workflow runtime engine."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from rebac import system_context

from angee.workflows import models as workflow_models
from angee.workflows.steps import HandlerStep, StepResult
from tests.conftest import _clear_model_tables, _create_missing_tables
from tests.test_workflows import Edge, Step, Trigger, Workflow

AbstractWorkflowRun = getattr(workflow_models, "WorkflowRun", None)
AbstractStepRun = getattr(workflow_models, "StepRun", None)


if AbstractWorkflowRun is not None:

    class WorkflowRun(AbstractWorkflowRun):
        """Concrete workflow run model for source-addon engine tests."""

        class Meta(AbstractWorkflowRun.Meta):
            """Django options for the concrete test workflow run model."""

            abstract = False
            app_label = "workflows"
            db_table = "test_workflows_workflow_run"
            rebac_resource_type = "workflows/run"
            rebac_id_attr = "sqid"


else:
    WorkflowRun = None


if AbstractStepRun is not None:

    class StepRun(AbstractStepRun):
        """Concrete workflow step-run journal model for source-addon engine tests."""

        class Meta(AbstractStepRun.Meta):
            """Django options for the concrete test step-run model."""

            abstract = False
            app_label = "workflows"
            db_table = "test_workflows_step_run"
            rebac_resource_type = "workflows/step_run"
            rebac_id_attr = "sqid"


else:
    StepRun = None


AbstractDecision = getattr(workflow_models, "Decision", None)


if AbstractDecision is not None:

    class Decision(AbstractDecision):
        """Concrete decision model for source-addon runtime tests."""

        class Meta(AbstractDecision.Meta):
            """Django options for the concrete test decision model."""

            abstract = False
            app_label = "workflows"
            db_table = "test_workflows_decision"
            rebac_resource_type = "workflows/decision"
            rebac_id_attr = "sqid"


else:
    Decision = None


def runtime_models() -> tuple[type[Any], type[Any]]:
    """Return concrete runtime models, failing loudly while Slice 3 is absent."""

    if WorkflowRun is None or StepRun is None:
        pytest.fail("WorkflowRun and StepRun runtime models must be implemented.")
    return WorkflowRun, StepRun


def run_statuses() -> tuple[Any, Any]:
    """Return runtime status enums, failing loudly while Slice 3 is absent."""

    run_status = getattr(workflow_models, "RunStatus", None)
    step_run_status = getattr(workflow_models, "StepRunStatus", None)
    if run_status is None or step_run_status is None:
        pytest.fail("RunStatus and StepRunStatus must be implemented.")
    return run_status, step_run_status


@pytest.fixture()
def workflow_engine_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete workflow runtime tables and sync their REBAC schema."""

    del transactional_db
    run_model, step_run_model = runtime_models()
    models: tuple[type[Any], ...] = (Workflow, Step, Edge, Trigger, run_model, step_run_model)
    if Decision is not None:
        models = (*models, Decision)
    created = _create_missing_tables(models)
    call_command("rebac", "sync", verbosity=0)
    _clear_model_tables(models)
    try:
        yield
    finally:
        _clear_model_tables(models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.fixture()
def no_workflow_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep engine tests synchronous by replacing queue enqueue hooks."""

    from angee.workflows import engine

    monkeypatch.setattr(engine, "enqueue_advance", lambda run_id: None)
    monkeypatch.setattr(engine, "enqueue_advance_at", lambda run_id, when: None)
    monkeypatch.setattr(engine, "enqueue_execute", lambda step_run_id: None)


@pytest.fixture()
def handler_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Make the abstract handler testable by journaling calls from config."""

    calls: list[dict[str, Any]] = []

    def run(self: HandlerStep, step_run: Any) -> StepResult:
        del self
        config = dict(step_run.step.config)
        calls.append(
            {
                "key": step_run.step.key,
                "input": step_run.input,
                "attempt": step_run.attempt,
            }
        )
        if config.get("mode") == "error":
            raise RuntimeError(str(config.get("error", "handler failed")))
        output = {
            "key": step_run.step.key,
            "input": step_run.input,
            **dict(config.get("output", {})),
        }
        return StepResult.done(output=output, outcome=str(config.get("outcome", "done")))

    monkeypatch.setattr(HandlerStep, "run", run)
    return calls


def workflow_with_steps(
    *,
    name: str = "Engine",
    max_steps: int = 1000,
    steps: tuple[dict[str, Any], ...],
    edges: tuple[tuple[str, str, str], ...],
) -> Workflow:
    """Create and publish a workflow definition graph."""

    with system_context(reason="test workflows engine definition"):
        draft = Workflow.objects.create(name=name, max_steps=max_steps)
        by_key = {}
        for index, spec in enumerate(steps):
            by_key[spec["key"]] = Step.objects.create(
                workflow=draft,
                key=spec["key"],
                name=spec.get("name", spec["key"].title()),
                step_class=spec.get("step_class", "handler"),
                config=spec.get("config", {}),
                join_rule=spec.get("join_rule", workflow_models.JoinRule.ALL_SUCCESS),
                is_entry=index == 0 if "is_entry" not in spec else spec["is_entry"],
            )
        for source, target, condition in edges:
            Edge.objects.create(
                workflow=draft,
                source=by_key[source],
                target=by_key[target],
                condition=condition,
            )
        return draft.publish()


def start_run(workflow: Workflow) -> Any:
    """Start a run without relying on a live queue."""

    from angee.workflows import engine

    return engine.start(workflow, subject=None, actor=None)


def advance_once(run: Any, *, now: Any | None = None) -> list[Any]:
    """Advance one run and return started rows."""

    from angee.workflows import engine

    _, step_run_status = run_statuses()
    if now is None:
        engine.advance(run.pk)
    else:
        engine.advance(run.pk, now=now)
    with system_context(reason="test workflows engine read started"):
        return list(StepRun.objects.filter(run=run, status=step_run_status.STARTED).order_by("pk"))


def execute_started(run: Any, *, now: Any | None = None, limit: int | None = None) -> None:
    """Execute currently started step-runs synchronously."""

    from angee.workflows import engine

    _, step_run_status = run_statuses()
    with system_context(reason="test workflows engine read started"):
        rows = list(StepRun.objects.filter(run=run, status=step_run_status.STARTED).order_by("pk"))
    if limit is not None:
        rows = rows[:limit]
    for row in rows:
        if now is None:
            engine.execute(row.pk)
        else:
            engine.execute(row.pk, now=now)


def run_to_terminal(run: Any, *, max_cycles: int = 20) -> Any:
    """Drive a run synchronously until it reaches a terminal state."""

    run_status, step_run_status = run_statuses()
    for _ in range(max_cycles):
        run.refresh_from_db()
        if run.status in {run_status.SUCCEEDED, run_status.FAILED, run_status.CANCELED}:
            return run
        advance_once(run)
        execute_started(run)
        run.refresh_from_db()
        with system_context(reason="test workflows engine active check"):
            active = StepRun.objects.filter(
                run=run,
                status__in=[step_run_status.SCHEDULED, step_run_status.STARTED],
            ).exists()
        if not active:
            advance_once(run)
    run.refresh_from_db()
    return run


def step_run_for(run: Any, key: str) -> Any:
    """Return one step-run row under elevated test read context."""

    with system_context(reason="test workflows engine step_run read"):
        return StepRun.objects.get(run=run, step__key=key)


def step_for(workflow: Workflow, key: str) -> Step:
    """Return one workflow step under elevated test read context."""

    with system_context(reason="test workflows engine step read"):
        return Step.objects.get(workflow=workflow, key=key)


@pytest.mark.django_db(transaction=True)
def test_two_step_run_completes_end_to_end(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """A run pins the published version, injects output, and succeeds."""

    del workflow_engine_tables, no_workflow_queue
    run_status, step_run_status = run_statuses()
    workflow = workflow_with_steps(
        steps=(
            {"key": "start", "config": {"outcome": "next", "output": {"value": 7}}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("start", "finish", "next"),),
    )

    run = run_to_terminal(start_run(workflow))

    assert run.status == run_status.SUCCEEDED
    assert run.workflow == workflow
    assert run.steps_taken == 2
    with system_context(reason="test workflows engine result read"):
        rows = list(StepRun.objects.filter(run=run).select_related("step").order_by("step__key"))
    assert [row.status for row in rows] == [step_run_status.SUCCEEDED, step_run_status.SUCCEEDED]
    finish = step_run_for(run, "finish")
    assert finish.input["value"] == 7
    assert [call["key"] for call in handler_calls] == ["start", "finish"]


@pytest.mark.django_db(transaction=True)
def test_crash_replay_does_not_reexecute_completed_steps(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Replaying advance/execute after a completed step reuses journaled output."""

    del workflow_engine_tables, no_workflow_queue
    workflow = workflow_with_steps(
        steps=(
            {"key": "start", "config": {"outcome": "next", "output": {"recorded": "yes"}}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("start", "finish", "next"),),
    )
    run = start_run(workflow)

    start_row = advance_once(run)[0]
    execute_started(run)
    from angee.workflows import engine

    engine.execute(start_row.pk)
    engine.advance(run.pk)
    engine.advance(run.pk)
    execute_started(run)
    run_to_terminal(run)

    assert [call["key"] for call in handler_calls] == ["start", "finish"]
    assert step_run_for(run, "finish").input["recorded"] == "yes"


@pytest.mark.django_db(transaction=True)
def test_duplicate_advance_claims_a_scheduled_step_once(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Duplicate advance calls are idempotent while a step is already claimed."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    _, step_run_status = run_statuses()
    workflow = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done"}},),
        edges=(),
    )
    run = start_run(workflow)

    advance_once(run)
    advance_once(run)
    run.refresh_from_db()

    assert run.steps_taken == 1
    with system_context(reason="test workflows engine duplicate read"):
        assert StepRun.objects.filter(run=run, status=step_run_status.STARTED).count() == 1
        assert StepRun.objects.filter(run=run, step__key="start").count() == 1


@pytest.mark.django_db(transaction=True)
def test_conditional_branch_skip_cascades_through_all_success_join(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Untaken branch targets skip, and default joins skip behind them."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    step_run_status = run_statuses()[1]
    workflow = workflow_with_steps(
        steps=(
            {"key": "entry", "config": {"outcome": "left"}},
            {"key": "left", "config": {"outcome": "done"}},
            {"key": "right", "config": {"outcome": "done"}},
            {"key": "join", "config": {"outcome": "done"}},
        ),
        edges=(
            ("entry", "left", "left"),
            ("entry", "right", "right"),
            ("left", "join", ""),
            ("right", "join", ""),
        ),
    )

    run = run_to_terminal(start_run(workflow))

    assert step_run_for(run, "right").status == step_run_status.SKIPPED
    assert step_run_for(run, "join").status == step_run_status.SKIPPED


@pytest.mark.django_db(transaction=True)
def test_none_failed_min_one_success_join_cures_post_branch_skip(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """A post-branch join can run when one branch succeeded and the other skipped."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    run_status, step_run_status = run_statuses()
    workflow = workflow_with_steps(
        steps=(
            {"key": "entry", "config": {"outcome": "left"}},
            {"key": "left", "config": {"outcome": "done"}},
            {"key": "right", "config": {"outcome": "done"}},
            {
                "key": "join",
                "join_rule": workflow_models.JoinRule.NONE_FAILED_MIN_ONE_SUCCESS,
                "config": {"outcome": "done"},
            },
        ),
        edges=(
            ("entry", "left", "left"),
            ("entry", "right", "right"),
            ("left", "join", ""),
            ("right", "join", ""),
        ),
    )

    run = run_to_terminal(start_run(workflow))

    assert run.status == run_status.SUCCEEDED
    assert step_run_for(run, "right").status == step_run_status.SKIPPED
    assert step_run_for(run, "join").status == step_run_status.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_one_success_join_runs_without_waiting_for_all_siblings(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """The one_success join schedules as soon as one upstream succeeds."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    _, step_run_status = run_statuses()
    workflow = workflow_with_steps(
        steps=(
            {"key": "entry", "config": {"outcome": "done"}},
            {"key": "fast", "config": {"outcome": "done"}},
            {"key": "slow", "config": {"outcome": "done"}},
            {
                "key": "join",
                "join_rule": workflow_models.JoinRule.ONE_SUCCESS,
                "config": {"outcome": "done"},
            },
        ),
        edges=(
            ("entry", "fast", ""),
            ("entry", "slow", ""),
            ("fast", "join", ""),
            ("slow", "join", ""),
        ),
    )
    run = start_run(workflow)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run, limit=1)

    advance_once(run)

    assert step_run_for(run, "join").status == step_run_status.STARTED
    assert step_run_for(run, "slow").status == step_run_status.STARTED


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("rule", "upstream_statuses", "expected"),
    [
        (workflow_models.JoinRule.ALL_SUCCESS, ("succeeded", "succeeded"), "started"),
        (workflow_models.JoinRule.ALL_SUCCESS, ("succeeded", "skipped"), "skipped"),
        (workflow_models.JoinRule.ONE_DONE, ("failed", "waiting"), "started"),
        (workflow_models.JoinRule.ALL_DONE, ("failed", "canceled"), "started"),
        (workflow_models.JoinRule.NONE_FAILED, ("skipped", "skipped"), "started"),
        (workflow_models.JoinRule.NONE_FAILED_MIN_ONE_SUCCESS, ("skipped", "skipped"), "absent"),
        (workflow_models.JoinRule.NONE_FAILED_MIN_ONE_SUCCESS, ("skipped", "succeeded"), "started"),
    ],
)
def test_join_rule_truth_table(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    rule: Any,
    upstream_statuses: tuple[str, str],
    expected: str,
) -> None:
    """Join readiness is derived from upstream sibling StepRun statuses."""

    del workflow_engine_tables, no_workflow_queue
    _, step_run_status = run_statuses()
    workflow = workflow_with_steps(
        steps=(
            {"key": "left", "is_entry": True, "config": {"outcome": "done"}},
            {"key": "right", "is_entry": False, "config": {"outcome": "done"}},
            {"key": "join", "is_entry": False, "join_rule": rule, "config": {"outcome": "done"}},
        ),
        edges=(("left", "join", ""), ("right", "join", "")),
    )
    left = step_for(workflow, "left")
    right = step_for(workflow, "right")
    with system_context(reason="test workflows join table"):
        run = WorkflowRun.objects.create(workflow=workflow, status=workflow_models.RunStatus.RUNNING)
        left_row = StepRun.objects.create(
            run=run,
            step=left,
            status=getattr(step_run_status, upstream_statuses[0].upper()),
            output={"left": True},
            outcome="done",
        )
        right_row = StepRun.objects.create(
            run=run,
            step=right,
            status=getattr(step_run_status, upstream_statuses[1].upper()),
            output={"right": True},
            outcome="done",
        )
    advance_once(run)

    with system_context(reason="test workflows engine join read"):
        join_row = StepRun.objects.filter(run=run, step__key="join").first()
    if expected == "absent":
        assert join_row is None
    else:
        assert join_row is not None
        assert join_row.status == getattr(step_run_status, expected.upper())
        if expected == "started":
            with system_context(reason="test workflows engine join previous read"):
                assert set(join_row.previous.all()) == {left_row, right_row}


@pytest.mark.django_db(transaction=True)
def test_content_routing_uses_outcome_edges(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Only edges matching the source outcome are taken."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    step_run_status = run_statuses()[1]
    workflow = workflow_with_steps(
        steps=(
            {"key": "classify", "config": {"outcome": "pdf"}},
            {"key": "pdf", "config": {"outcome": "done"}},
            {"key": "image", "config": {"outcome": "done"}},
        ),
        edges=(("classify", "pdf", "pdf"), ("classify", "image", "image")),
    )

    run = run_to_terminal(start_run(workflow))

    assert step_run_for(run, "pdf").status == step_run_status.SUCCEEDED
    assert step_run_for(run, "image").status == step_run_status.SKIPPED


@pytest.mark.django_db(transaction=True)
def test_max_steps_fails_run_before_claiming_next_step(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """The engine, not a step impl, enforces the pinned max_steps bound."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    run_status, step_run_status = run_statuses()
    workflow = workflow_with_steps(
        max_steps=1,
        steps=(
            {"key": "start", "config": {"outcome": "next"}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("start", "finish", "next"),),
    )

    run = start_run(workflow)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    run.refresh_from_db()

    assert run.status == run_status.FAILED
    assert "max_steps" in run.error
    assert step_run_for(run, "finish").status == step_run_status.SCHEDULED


@pytest.mark.django_db(transaction=True)
def test_timer_wait_resumes_from_wake_sweep(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """A durable wait step becomes executable when the sweep sees its wake time."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    run_status, step_run_status = run_statuses()
    now = timezone.now()
    wake_at = now + timedelta(hours=1)
    workflow = workflow_with_steps(
        steps=(
            {"key": "wait", "step_class": "wait", "config": {"until": wake_at.isoformat()}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("wait", "finish", "timer"),),
    )
    run = start_run(workflow)
    advance_once(run, now=now)
    execute_started(run, now=now)
    advance_once(run, now=now)
    run.refresh_from_db()

    wait_row = step_run_for(run, "wait")
    assert wait_row.status == step_run_status.WAITING
    assert run.wake_at == wake_at

    from angee.workflows import engine

    engine.sweep(now=wake_at + timedelta(seconds=1))
    execute_started(run, now=wake_at + timedelta(seconds=1))
    run_to_terminal(run)
    run.refresh_from_db()

    assert step_run_for(run, "wait").status == step_run_status.SUCCEEDED
    assert run.status == run_status.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_cancellation_propagates_to_journal_and_child_runs(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Canceling a run cancels durable waits, scheduled rows, and child runs."""

    del workflow_engine_tables, no_workflow_queue
    run_status, step_run_status = run_statuses()
    workflow = workflow_with_steps(
        steps=(
            {"key": "entry", "config": {"outcome": "done"}},
            {"key": "queued", "config": {"outcome": "done"}},
            {
                "key": "waiting",
                "step_class": "gate",
                "config": {"action": "approve", "slots": [{"assignee": "auth/user:test-waiting"}]},
            },
        ),
        edges=(("entry", "queued", ""), ("queued", "waiting", "")),
    )
    entry = step_for(workflow, "entry")
    queued = step_for(workflow, "queued")
    waiting = step_for(workflow, "waiting")
    with system_context(reason="test workflows cancel setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=workflow_models.RunStatus.RUNNING)
        started = StepRun.objects.create(run=run, step=entry, status=step_run_status.STARTED)
        scheduled = StepRun.objects.create(run=run, step=queued, status=step_run_status.SCHEDULED)
        waiting_row = StepRun.objects.create(run=run, step=waiting, status=step_run_status.WAITING)
        child = WorkflowRun.objects.create(
            workflow=workflow,
            parent_step_run=waiting_row,
            status=workflow_models.RunStatus.WAITING,
        )
        StepRun.objects.create(run=child, step=waiting, status=step_run_status.WAITING)

    from angee.workflows import engine

    engine.cancel(run)

    run.refresh_from_db()
    child.refresh_from_db()
    started.refresh_from_db()
    scheduled.refresh_from_db()
    waiting_row.refresh_from_db()
    assert run.status == run_status.CANCELED
    assert child.status == run_status.CANCELED
    assert scheduled.status == step_run_status.CANCELED
    assert waiting_row.status == step_run_status.CANCELED
    assert started.status == step_run_status.STARTED
    assert started.resume_state["cancel_requested"] is True
