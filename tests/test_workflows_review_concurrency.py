"""Review waiters under real PostgreSQL decision, cancellation and expiry races."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from queue import Queue
from time import monotonic, sleep

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.db.models.functions import Now
from django.utils import timezone
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.signals import decision_group_settled
from angee.decisions.states import Verdict
from angee.workflows.reviews import ReviewStep
from angee.workflows.runner import runner
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import StepAttempt, StepRun, WorkflowRun
from tests.conftest import create_user
from tests.decisions_models import Decision, DecisionGroup

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


class Accept(Action, key="accept", label="Accept", verdict=Verdict.COMPLETED, outcome="accepted"):
    """One typed answer whose worker application can be observed separately."""

    note: str = "Retained"


@pytest.fixture
def waiting_review(execution, register_step):
    """Admit real review waits with captured task delivery and body applications."""
    actor, sent = execution
    reviewer = create_user("concurrent-reviewer")
    other_reviewer = create_user("concurrent-other-reviewer")
    applied = []

    class Question(ReviewStep[None, None, None, None]):
        key = "concurrent_question"
        actions = (Accept,)

        def ask(self, ctx):
            duration = ctx.input.get("expires_in")
            request = DecisionRequest(
                kind="concurrent_question", subject=None, assignees=(reviewer,), actions=self.actions,
                expires_at=None if duration is None else timezone.now() + timedelta(seconds=duration),
            )
            requests = (request,)
            if ctx.input.get("requesterless"):
                requests += (replace(request, assignees=(ctx.actor,), requester=None),)
            elif ctx.input.get("two"):
                requests += (replace(request, assignees=(other_reviewer,)),)
            return ctx.ask(*requests, policy=ctx.input.get("policy", "first"))

        def apply(self, ctx, settled):
            answered = next(answer for answer in settled if answer.action is not None)
            applied.append((ctx.step_run.pk, answered.resolver.pk))
            return ctx.done({"note": answered.action.note}, outcome="accepted")

    register_step(Question)

    def start(*, expires_in=None, run_actor=None, **input):
        workflow = load_workflow({
            "nodes": {"review": {"step": Question.key}},
            "results": [{"from": "review", "when": [outcome], "as": outcome}
                        for outcome in ("accepted", "expired")],
        }, actor=actor)
        if run_actor is not None:
            workflow.with_actor(actor).grant_record_access("starter", run_actor)
        run = WorkflowRun.objects.start(
            workflow, actor=run_actor or actor, input={"expires_in": expires_in, **input},
        )
        run_until(run)
        step = system_queryset(StepRun).get(run=run)
        decision = system_queryset(Decision).get(group_id=step.decision_group_id, index=0)
        assert step.status == "waiting" and step.waiting_kind == "decision"
        sent.clear()
        return run, step, decision

    return actor, reviewer, sent, applied, start


@pytest.fixture
def settlements():
    """Observe the production signal without replacing delivery or retaining receivers."""
    groups = []

    def received(sender, *, group, **kwargs):
        groups.append(group.pk)

    decision_group_settled.connect(received)
    try:
        yield groups
    finally:
        decision_group_settled.disconnect(received)


def answer(decision, actor):
    """Submit the observed revision through the independent decisions owner."""
    return Decision.objects.decide(decision.pk, actor=actor, revision=decision.revision, action="accept", values={})


def submit(pool, call):
    """Give each worker its own connection and expose its native lock-wait identity."""
    started = Queue()

    def invoke():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                started.put(cursor.fetchone()[0])
            return call()
        finally:
            connections.close_all()

    return pool.submit(invoke), started.get(timeout=10)


def wait_for_lock(pid, future):
    """Prove the contender reached a PostgreSQL lock before the holder commits."""
    deadline = monotonic() + 10
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
            if cursor.fetchone() == ("Lock",):
                return
        if future.done():
            pytest.fail(f"The contender finished before reaching its row lock: {future.result()!r}")
        sleep(0.01)
    pytest.fail(f"PostgreSQL backend {pid} did not reach a row-lock wait.")


@pytest.mark.parametrize("winner", ["decide", "cancel"])
def test_review_decide_races_cancel_in_both_orders_without_lock_inversion(waiting_review, winner):
    """On-commit wake never waits for a run while still retaining the group lock."""
    actor, reviewer, _sent, applied, start = waiting_review
    run, step, decision = start()
    with ThreadPoolExecutor(max_workers=1) as pool:
        if winner == "decide":
            with DecisionGroup.objects.hold(decision.group_id):
                contender, pid = submit(pool, lambda: WorkflowRun.objects.cancel(run, actor=actor))
                wait_for_lock(pid, contender)
                # Cancellation holds the run and step while waiting for this group.
                answer(decision, reviewer)
                assert not applied
            assert contender.result(timeout=10).canceled
        else:
            with WorkflowRun.objects.hold(run.pk):
                assert WorkflowRun.objects.cancel(run, actor=actor).canceled
                contender, pid = submit(pool, lambda: answer(decision, reviewer))
                wait_for_lock(pid, contender)
            with pytest.raises(ValidationError, match="changed; reload"):
                contender.result(timeout=10)
    run_until(run)
    retained = system_queryset(Decision).get(pk=decision.pk)
    assert run.status == "canceled" and not applied
    assert system_queryset(StepRun).get(pk=step.pk).status == "canceled"
    assert system_queryset(StepAttempt).filter(step_run=step).count() == 1
    assert retained.closed_reason == ("resolved" if winner == "decide" else "canceled")
    assert retained.resolution == ({"action": "accept", "note": "Retained"} if winner == "decide" else {})
    assert system_queryset(DecisionGroup).get(pk=decision.group_id).settled_at is not None
    assert runner.wake_decisions() == 0


@pytest.mark.parametrize("winner", ["decide", "expiry"])
def test_review_decide_races_expiry_and_worker_consumes_the_final_group(waiting_review, winner):
    """The worker applies a retained answer or takes expired, never both."""
    _actor, reviewer, sent, applied, start = waiting_review
    run, step, decision = start(expires_in=5)
    if winner == "expiry":
        with system_context(reason="advance review deadline after admission"):
            Decision.objects.filter(pk=decision.pk).owner_update(expires_at=Now() - timedelta(seconds=1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with DecisionGroup.objects.hold(decision.group_id):
            if winner == "decide":
                answer(decision, reviewer)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM %s - clock_timestamp()), 0) + 0.05)",
                        [decision.expires_at],
                    )
                contender, _pid = submit(pool, Decision.objects.expire_due)
                # Expiry's skip-locked claim must not wait for a decided group.
                assert contender.result(timeout=5) == 0
            else:
                assert Decision.objects.expire_due() == 1
                contender, pid = submit(pool, lambda: answer(decision, reviewer))
                wait_for_lock(pid, contender)
            assert system_queryset(StepRun).get(pk=step.pk).status == "waiting"
            assert not applied and not sent
        if winner == "expiry":
            with pytest.raises(ValidationError, match="changed; reload"):
                contender.result(timeout=10)
    assert system_queryset(StepRun).get(pk=step.pk).status == "ready"
    assert [payload["kwargs"]["step_run_id"] for name, payload in sent if name == "workflows.execute"] == [step.pk]
    run_until(run)
    assert run.status == "succeeded"
    assert run.outcome == ("accepted" if winner == "decide" else "expired")
    assert applied == ([(step.pk, reviewer.pk)] if winner == "decide" else [])
    assert system_queryset(StepAttempt).filter(step_run=step, result="succeeded").count() == 2
    assert Decision.objects.expire_due() == 0 and runner.wake_decisions() == 0


def test_apply_holds_run_before_group_and_cancel_observes_its_terminal_commit(waiting_review):
    """A blocked apply retains its run lock; later cancellation cannot undo success."""
    actor, reviewer, _sent, applied, start = waiting_review
    run, step, decision = start()
    answer(decision, reviewer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        with DecisionGroup.objects.hold(decision.group_id):
            worker, worker_pid = submit(pool, lambda: runner.execute(step.pk))
            wait_for_lock(worker_pid, worker)
            cancel, cancel_pid = submit(pool, lambda: WorkflowRun.objects.cancel(run, actor=actor))
            wait_for_lock(cancel_pid, cancel)
            assert not applied
        assert worker.result(timeout=10) is True
        assert not cancel.result(timeout=10).canceled
    run_until(run)
    assert run.status == "succeeded" and run.outcome == "accepted"
    assert applied == [(step.pk, reviewer.pk)]
    assert system_queryset(StepRun).get(pk=step.pk).status == "succeeded"
    assert system_queryset(Decision).get(pk=decision.pk).closed_reason == "resolved"


def test_concurrent_all_answers_settle_wake_and_apply_once(waiting_review, settlements):
    """Both seats contend on the same group; only its final answer wakes the step."""
    _actor, reviewer, sent, applied, start = waiting_review
    run, step, first = start(two=True, policy="all")
    second = system_queryset(Decision).get(group_id=first.group_id, index=1)
    with system_context(reason="read concurrent review participants"):
        other_reviewer = second.assignees.get()
    with ThreadPoolExecutor(max_workers=2) as pool:
        with DecisionGroup.objects.hold(first.group_id):
            left, left_pid = submit(pool, lambda: answer(first, reviewer))
            wait_for_lock(left_pid, left)
            right, right_pid = submit(pool, lambda: answer(second, other_reviewer))
            wait_for_lock(right_pid, right)
            assert not settlements and not sent and not applied
        assert left.result(timeout=10).closed_reason == "resolved"
        assert right.result(timeout=10).closed_reason == "resolved"
    assert settlements == [first.group_id]
    assert [payload["kwargs"]["step_run_id"] for name, payload in sent if name == "workflows.execute"] == [step.pk]
    assert runner.wake_decisions() == 0
    run_until(run)
    assert run.status == "succeeded" and applied == [(step.pk, reviewer.pk)]
    assert system_queryset(StepAttempt).filter(step_run=step, result="succeeded").count() == 2


def test_first_requesterless_answer_closes_its_concurrent_sibling(waiting_review, settlements):
    """An explicit requester opt-out allows the run actor to win a real seat race."""
    _admin, reviewer, sent, applied, start = waiting_review
    actor = create_user("requesterless-starter")
    run, step, first = start(requesterless=True, run_actor=actor)
    second = system_queryset(Decision).get(group_id=first.group_id, index=1)
    assert second.requester_id is None
    with ThreadPoolExecutor(max_workers=1) as pool:
        with DecisionGroup.objects.hold(first.group_id):
            contender, pid = submit(pool, lambda: answer(first, reviewer))
            wait_for_lock(pid, contender)
            assert answer(second, actor).closed_reason == "resolved"
            assert not settlements and not sent and not applied
        with pytest.raises(ValidationError, match="changed; reload"):
            contender.result(timeout=10)
    assert settlements == [first.group_id]
    assert system_queryset(Decision).get(pk=first.pk).closed_reason == "sibling_settled"
    assert [payload["kwargs"]["step_run_id"] for name, payload in sent if name == "workflows.execute"] == [step.pk]
    run_until(run)
    assert run.status == "succeeded" and applied == [(step.pk, actor.pk)]
    assert system_queryset(StepAttempt).filter(step_run=step, result="succeeded").count() == 2


def test_settlement_signal_skips_busy_run_and_tick_recovers(waiting_review, settlements):
    """After-commit delivery finishes while another connection holds the run lock."""
    _actor, reviewer, sent, applied, start = waiting_review
    run, step, decision = start()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with WorkflowRun.objects.hold(run.pk):
            contender, _pid = submit(pool, lambda: answer(decision, reviewer))
            assert contender.result(timeout=10).closed_reason == "resolved"
            assert settlements == [decision.group_id]
            assert system_queryset(StepRun).get(pk=step.pk).status == "waiting"
            assert not sent and not applied
    assert runner.tick()["decisions"] == 1
    assert [payload["kwargs"]["step_run_id"] for name, payload in sent if name == "workflows.execute"] == [step.pk]
    assert runner.tick()["decisions"] == 0
    run_until(run)
    assert run.status == "succeeded" and applied == [(step.pk, reviewer.pk)]
