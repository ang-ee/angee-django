"""PostgreSQL concurrency contracts for durable workflow publication."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from typing import Any

import pytest
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rebac import system_context

from angee.testing.models import Workflow, WorkflowDispatch, WorkflowRun
from angee.workflows import engine
from angee.workflows.dispatch import DispatchTarget, publish_due

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


def test_two_publishers_may_duplicate_send_without_losing_telemetry(composed_tables: None) -> None:
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


def test_two_consumers_commit_one_domain_effect(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with system_context(reason="dispatch consumption race setup"):
        workflow = Workflow.objects.create(name="Dispatch consumption race")
        run = WorkflowRun.objects.create(workflow=workflow)
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    starting = Barrier(2)

    def apply(target: DispatchTarget, *, at: datetime) -> bool:
        target.row.deliveries += 1
        target.row.save(update_fields=["deliveries", "updated_at"])
        target.result["claimed"] = 1
        return True

    monkeypatch.setattr(engine, "advance_locked", apply)

    def consume() -> dict[str, int]:
        starting.wait(timeout=5)
        return WorkflowDispatch.objects.deliver(dispatch.pk, expected_target_id=run.pk, now=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, consume), pool.submit(_thread, consume))
        outcomes = [future.result(timeout=10) for future in futures]

    assert sorted(outcomes, key=lambda result: result["claimed"]) == [{"claimed": 0}, {"claimed": 1}]
    with system_context(reason="verify exactly one concurrent dispatch effect"):
        run.refresh_from_db()
        dispatch.refresh_from_db()
    assert run.deliveries == 1
    assert dispatch.consumed_at == now
