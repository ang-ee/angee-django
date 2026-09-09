"""PostgreSQL concurrency contracts for durable workflow publication."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone
from rebac import system_context

from angee.workflows.dispatch import DispatchPreflightDisposition, publish_due
from tests.workflows import Workflow, WorkflowDispatch, WorkflowRun

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL dispatch serialization contract"),
]


def _thread(call: Any) -> Any:
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
        return call()
    finally:
        connections.close_all()


def test_two_publishers_may_duplicate_send_without_losing_telemetry(workflow_engine_tables: None) -> None:
    with system_context(reason="dispatch concurrency setup"):
        workflow = Workflow.objects.create(name="Dispatch concurrency")
        run = WorkflowRun.objects.create(workflow=workflow)
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    publishing = Barrier(2)

    def publish() -> dict[str, int]:
        def send(envelope: object) -> None:
            del envelope
            publishing.wait(timeout=5)

        return publish_due(send, now=now, limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, publish), pool.submit(_thread, publish))
        outcomes = [future.result(timeout=10) for future in futures]

    assert outcomes == [
        {"selected": 1, "sent": 1, "failed": 0},
        {"selected": 1, "sent": 1, "failed": 0},
    ]
    with system_context(reason="verify dispatch concurrency"):
        dispatch.refresh_from_db()
    assert dispatch.send_count == 2
    assert dispatch.consumed_at is None


def test_two_consumers_serialize_to_ready_then_duplicate(workflow_engine_tables: None) -> None:
    with system_context(reason="dispatch consumption race setup"):
        workflow = Workflow.objects.create(name="Dispatch consumption race")
        run = WorkflowRun.objects.create(workflow=workflow)
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    starting = Barrier(2)

    def consume() -> str:
        starting.wait(timeout=5)
        with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
            dispatch_id=dispatch.pk, lease_token=None, at=now, using="default"
        ) as preflight:
            if preflight.disposition == DispatchPreflightDisposition.READY:
                WorkflowDispatch.objects._consume_locked(dispatch.pk, at=now)
            return preflight.disposition

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, consume), pool.submit(_thread, consume))
        outcomes = {future.result(timeout=10) for future in futures}

    assert outcomes == {
        DispatchPreflightDisposition.READY,
        DispatchPreflightDisposition.DUPLICATE,
    }
