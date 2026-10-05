"""Independent decision answers race cancellation without holding a question group."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from time import monotonic, sleep

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.test.utils import CaptureQueriesContext

from angee.base.mixins import StaleRevisionError
from angee.base.scoping import system_queryset
from angee.decisions.testing.models import Decision
from angee.workflows.runner import runner
from angee.workflows.testing.drivers import run_until
from angee.workflows.testing.models import StepRun, WorkflowRun
from tests.test_workflows_review import answer, questions, start_review
from tests.test_workflows_review import review as review

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


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


@pytest.mark.parametrize("winner", ["answer", "cancel"])
def test_answer_races_cancel_without_lock_inversion(review, winner):
    actor, people, _, _ = review
    run, step = start_review(review)
    decision = questions(step)[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with WorkflowRun.objects.hold(run.pk):
            if winner == "answer":
                contender, pid = submit(pool, lambda: WorkflowRun.objects.cancel(run, actor=actor))
                wait_for_lock(pid, contender)
                answer(decision, people[0])
            else:
                assert WorkflowRun.objects.cancel(run, actor=actor).canceled
                contender, pid = submit(pool, lambda: answer(decision, people[0]))
                wait_for_lock(pid, contender)
        if winner == "answer":
            assert contender.result(timeout=10).canceled
        else:
            with pytest.raises(StaleRevisionError):
                contender.result(timeout=10)
    run_until(run)
    assert run.status == "canceled"
    assert system_queryset(StepRun).get(pk=step.pk).status == "canceled"
    assert system_queryset(Decision).get(pk=decision.pk).verdict == (["approve"] if winner == "answer" else [])
    assert runner.wake_decisions() == 0


def test_busy_run_wakes_on_the_next_sweep(review):
    actor, people, _, _ = review
    run, step = start_review(review)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with WorkflowRun.objects.hold(run.pk):
            future, _ = submit(pool, lambda: answer(questions(step)[0], people[0]))
            assert not future.result(timeout=10).is_open
            assert system_queryset(StepRun).get(pk=step.pk).status == "waiting"
    assert runner.wake_decisions() == 1
    run_until(run)
    assert run.status == "succeeded"


def test_answered_waiter_locks_only_the_step(review, monkeypatch):
    """Recovery never joins or locks the decision through the step's query."""
    _, people, _, _ = review
    run, step = start_review(review)
    with monkeypatch.context() as patch:
        patch.setattr(type(runner), "wake_decisions", lambda *args, **kwargs: 0)
        answer(questions(step)[0], people[0])
    with transaction.atomic(), CaptureQueriesContext(connection) as captured:
        row = system_queryset(StepRun).answered_decisions().select_for_update(no_key=True).get(pk=step.pk)
        assert row.pk == step.pk
    locked = [query["sql"] for query in captured if "FOR NO KEY UPDATE" in query["sql"]]
    assert len(locked) == 1 and " JOIN " not in locked[0] and "DISTINCT" not in locked[0]
    assert runner.wake_decisions() == 1
    run_until(run)
    assert run.status == "succeeded"
