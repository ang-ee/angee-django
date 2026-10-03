"""Real PostgreSQL map races, bounded planning and wide-map recovery."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from django.db import close_old_connections, connection, connections, reset_queries
from django.test.utils import CaptureQueriesContext

from angee.base.scoping import system_queryset
from angee.workflows.runner import runner
from angee.workflows.steps import StepMode
from angee.workflows.testing.drivers import run_until
from angee.workflows.testing.models import StepAttempt, WorkflowRun
from tests.queries import is_rebac_revision_read
from tests.test_workflows_map import MapEcho, body_rows, start_map
from tests.test_workflows_map import map_steps as map_steps

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def in_connection(call, *args):
    """Confine each concurrent worker to its own native database connection."""
    close_old_connections()
    try:
        return call(*args)
    finally:
        connections.close_all()


@pytest.mark.parametrize("total", [2, 4])
def test_concurrent_items_plan_next_rows_and_settle_parent_once(execution, map_steps, register_step, settings, total):
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 2
    barrier, entered = Barrier(2), Event()

    class ConcurrentItem(MapEcho):
        key = "map_concurrent"
        mode = StepMode.IO

        def run(self, ctx):
            assert not connection.in_atomic_block
            entered.set()
            barrier.wait(timeout=15)
            return ctx.done(ctx.input)

    register_step(ConcurrentItem)
    run, mapped = start_map(actor, [{"value": index} for index in range(total)], body=ConcurrentItem.key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for offset in range(0, total, 2):
            pending = list(body_rows(run).filter(status="ready"))
            assert [row.map_index for row in pending] == [offset, offset + 1]
            entered.clear()
            left = pool.submit(in_connection, runner.execute, pending[0].pk)
            assert entered.wait(10)
            right = pool.submit(in_connection, runner.execute, pending[1].pk)
            assert left.result(timeout=20) and right.result(timeout=20)
            assert body_rows(run).count() == min(offset + 4, total)
            assert body_rows(run).exclude(status="succeeded").count() <= 2
    mapped.refresh_from_db()
    assert mapped.status == "ready" and mapped.attempt == 1
    assert runner.execute(mapped.pk)
    assert not runner.execute(mapped.pk)
    run.refresh_from_db()
    assert run.status == "succeeded"
    assert [item["index"] for item in run.output] == list(range(total))
    assert system_queryset(StepAttempt).filter(step_run=mapped).count() == 2
    assert system_queryset(StepAttempt).filter(step_run__run=run, step_run__node_key="items.body").count() == total


def test_cancel_racing_last_item_fences_its_late_result(execution, map_steps, register_step):
    actor, _sent = execution
    entered, release = Event(), Event()

    class LastItem(MapEcho):
        key = "map_last_item"
        mode = StepMode.IO

        def run(self, ctx):
            entered.set()
            assert release.wait(15)
            return ctx.done(ctx.input)

    register_step(LastItem)
    run, mapped = start_map(actor, [{"value": 7}], body=LastItem.key)
    item = body_rows(run).get()
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(in_connection, runner.execute, item.pk)
        assert entered.wait(10)
        try:
            with WorkflowRun.objects.hold(run.pk):
                result = WorkflowRun.objects.cancel(run, actor=actor)
                assert result.canceled and result.steps == 2
                release.set()
        finally:
            release.set()
        assert worker.result(timeout=15) is False
    run.refresh_from_db()
    mapped.refresh_from_db()
    item.refresh_from_db()
    assert run.status == mapped.status == item.status == "canceled"
    assert item.output == {} and mapped.output == {}
    assert system_queryset(StepAttempt).get(step_run=item).result == "superseded"
    assert not any(runner.tick().values())


def test_thousand_items_stay_within_concurrency_and_query_bound(
    execution, map_steps, settings, record_testsuite_property,
):
    """Each full item execution, including commit dispatch, has bounded SQL work."""
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 4
    run, mapped = start_map(actor, [{"value": index} for index in range(1000)])
    query_counts = []
    completed = 0
    while completed < 1000:
        pending = list(body_rows(run).filter(status="ready"))
        assert 0 < len(pending) <= 4
        for item in pending:
            # Measure each item before Django's bounded query log can wrap.
            reset_queries()
            with CaptureQueriesContext(connection) as queries:
                assert runner.execute(item.pk)
            query_counts.append(len(queries))
            # The native write owner resolves one installed
            # policy per write. Its 11 write gates read one schema witness each;
            # retain a separate bound on the execution's remaining statements.
            witnesses = sum(is_rebac_revision_read(query["sql"]) for query in queries)
            assert witnesses <= 11, (item.map_index, witnesses)
            assert 0 < len(queries) - witnesses <= 100, (item.map_index, len(queries) - witnesses)
            completed += 1
            assert body_rows(run).exclude(status="succeeded").count() <= 4
    run_until(run, max_steps=1000)
    assert run.status == "succeeded" and len(run.output) == 1000
    assert [item["index"] for item in run.output] == list(range(1000))
    mapped.refresh_from_db()
    assert (mapped.map_total, mapped.map_settled) == (1000, 1000)
    record_testsuite_property("map_items", completed)
    record_testsuite_property("map_concurrency", 4)
    record_testsuite_property("maximum_queries_per_item", max(query_counts))


def test_tick_drains_more_than_one_hundred_due_items_without_starvation(
    execution, map_steps, register_step, settings,
):
    """One bounded tick reaches every wait when a map exceeds the old 100-row batch."""
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 128

    class DueItem(MapEcho):
        key = "map_due_item"

        def run(self, ctx):
            if not ctx.state:
                return ctx.wait(until=ctx.now - timedelta(seconds=1), state={"awake": True})
            return ctx.done(ctx.input)

    register_step(DueItem)
    run, mapped = start_map(actor, [{"value": index} for index in range(128)], body=DueItem.key)
    run_until(run, max_steps=1000)
    assert body_rows(run).filter(status="waiting").count() == 128
    assert runner.tick()["woken"] == 128
    run_until(run, max_steps=1000)
    assert run.status == "succeeded"
    assert len(run.output) == 128
    mapped.refresh_from_db()
    assert mapped.map_settled == mapped.map_total == 128


def test_tick_reaches_another_run_beyond_one_hundred_locked_map_items(
    execution, map_steps, register_step, settings,
):
    """The widened candidate bound passes an entire busy map to wake another run."""
    actor, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 128

    class DueItem(MapEcho):
        key = "map_busy_due_item"

        def run(self, ctx):
            if not ctx.state:
                return ctx.wait(until=ctx.now - timedelta(seconds=1), state={"awake": True})
            return ctx.done(ctx.input)

    register_step(DueItem)
    busy, _mapped = start_map(actor, [{"value": index} for index in range(128)], body=DueItem.key)
    run_until(busy, max_steps=1000)
    other, _other_map = start_map(actor, [{"value": 0}], body=DueItem.key)
    run_until(other, max_steps=1000)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with WorkflowRun.objects.hold(busy.pk):
            contender = pool.submit(in_connection, runner.tick)
            assert contender.result(timeout=20)["woken"] == 1
            assert body_rows(busy).filter(status="waiting").count() == 128
    run_until(other, max_steps=1000)
    assert other.status == "succeeded"
    assert runner.tick()["woken"] == 128
    run_until(busy, max_steps=1000)
    assert busy.status == "succeeded"
