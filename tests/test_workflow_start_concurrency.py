"""PostgreSQL serialization contracts for pinned workflow starts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from typing import Any

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine
from angee.workflows import models as workflow_models
from tests.workflows import Step, StepRun, Trigger, Workflow, WorkflowRun, start_run

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL start serialization contract"),
]


def _thread(call: Any) -> Any:
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
        return call()
    finally:
        connections.close_all()


def test_two_due_scans_claim_one_schedule_occurrence(workflow_engine_tables: None) -> None:
    now = timezone.now().replace(microsecond=0)
    with system_context(reason="scheduled start race setup"):
        workflow = Workflow.objects.create(name="Scheduled start race")
        Step.objects.create(
            workflow=workflow,
            key="start",
            name="Start",
            step_class="wait",
            config={"until": (now + timedelta(hours=2)).isoformat()},
            is_entry=True,
        )
        workflow.publish()
        trigger = Trigger.objects.create(
            workflow=workflow,
            kind=workflow_models.TriggerKind.SCHEDULE,
            config={"interval_seconds": 3600},
        )
        trigger.enable()
        trigger.refresh_from_db()
        assert trigger.next_fire_at is not None
        now = trigger.next_fire_at
    starting = Barrier(2)

    def start_due() -> int | None:
        starting.wait(timeout=5)
        result = Trigger.objects.start_due_schedule(trigger.pk, timestamp=now)
        return None if result is None else result[0].pk

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, start_due), pool.submit(_thread, start_due))
        outcomes = [future.result(timeout=10) for future in futures]

    assert sum(value is not None for value in outcomes) == 1
    with system_context(reason="scheduled start race verification"):
        assert WorkflowRun.objects.filter(trigger=trigger).count() == 1
    trigger.refresh_from_db()
    assert trigger.next_fire_at == now + timedelta(hours=1)


def test_failure_path_and_direct_start_share_parent_first_lock_order(
    workflow_engine_tables: None,
) -> None:
    wait_until = (timezone.now() + timedelta(hours=2)).isoformat()
    with system_context(reason="linked start race setup"):
        error_workflow = Workflow.objects.create(name="Error target")
        Step.objects.create(
            workflow=error_workflow,
            key="start",
            name="Start",
            step_class="wait",
            config={"until": wait_until},
            is_entry=True,
        )
        error_workflow.publish()
        parent_workflow = Workflow.objects.create(name="Parent", error_workflow=error_workflow)
        Step.objects.create(
            workflow=parent_workflow,
            key="start",
            name="Start",
            step_class="wait",
            config={"until": wait_until},
            is_entry=True,
        )
        parent_workflow.publish()
    parent_run = start_run(parent_workflow)
    with system_context(reason="linked start race setup"):
        parent_step = StepRun.objects.get(run=parent_run)
    parent_locked = Event()
    direct_waiting_for_parent = Event()

    def failure_path() -> None:
        with system_context(reason="failure path start race"), transaction.atomic():
            locked_run = WorkflowRun.objects.lock_if_supported().select_related("workflow").get(pk=parent_run.pk)
            locked_step = StepRun.objects.lock_if_supported().get(pk=parent_step.pk)
            parent_locked.set()
            assert direct_waiting_for_parent.wait(timeout=5)
            engine._start_error_workflow(locked_run, failed_step_run=locked_step)

    def direct_start() -> int:
        assert parent_locked.wait(timeout=5)

        def observe_parent_lock(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
            if "FOR UPDATE" in sql.upper() and WorkflowRun._meta.db_table in sql:
                direct_waiting_for_parent.set()
            return execute(sql, params, many, context)

        with connection.execute_wrapper(observe_parent_lock):
            return engine.start(
                error_workflow,
                subject=parent_run,
                actor=None,
                parent_step_run=parent_step,
                origin=workflow_models.RunOrigin.ERROR_WORKFLOW,
            ).pk

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, failure_path), pool.submit(_thread, direct_start))
        [future.result(timeout=10) for future in futures]

    with system_context(reason="linked start race verification"):
        child = WorkflowRun.objects.get(parent_step_run=parent_step)
        assert child.dispatches.count() == 1
