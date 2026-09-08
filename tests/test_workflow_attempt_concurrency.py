"""PostgreSQL serialization contracts for retained workflow attempts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rebac import system_context

from angee.workflows.attempts import AttemptResult, AttemptResultKind, LeaseRevocationReason
from angee.workflows.models import RunStatus
from tests.workflows import StepAttempt, StepRun, WorkflowRun, workflow_with_steps

pytest_plugins = ("tests.workflows",)
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL row-lock contract"),
]


def _thread(call: Any) -> Any:
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
        with system_context(reason="test retained attempt concurrency"):
            return call()
    finally:
        connections.close_all()


@pytest.fixture()
def scheduled_step_run(workflow_engine_tables: None) -> StepRun:
    del workflow_engine_tables
    workflow = workflow_with_steps(steps=({"key": "start", "step_class": "agent_session"},), edges=())
    with system_context(reason="prepare attempt concurrency"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        return StepRun.objects.create(run=run, step=workflow.steps.get(key="start"))


def test_concurrent_allocation_produces_one_active_attempt(scheduled_step_run: StepRun) -> None:
    start = Barrier(2)

    def allocate() -> int:
        start.wait(timeout=5)
        return StepAttempt.objects.allocate(scheduled_step_run).pk

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, allocate), pool.submit(_thread, allocate))
        outcomes: list[int | type[Exception]] = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except ValidationError as error:
                outcomes.append(type(error))

    assert sum(isinstance(value, int) for value in outcomes) == 1
    assert outcomes.count(ValidationError) == 1


def test_result_and_revocation_are_serialized_without_losing_evidence(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    start = Barrier(2)

    def finalize() -> Any:
        start.wait(timeout=5)
        return StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(AttemptResultKind.PREPARATION_ERROR, error="failed"),
            recorded_at=timezone.now(),
        )

    def revoke() -> Any:
        start.wait(timeout=5)
        return StepAttempt.objects.revoke(
            attempt.pk,
            lease_token=attempt.lease_token,
            reason=LeaseRevocationReason.HEARTBEAT_LOST,
            at=timezone.now(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        finalized = pool.submit(_thread, finalize)
        revoked = pool.submit(_thread, revoke)
        finalization = finalized.result(timeout=10)
        revocation = revoked.result(timeout=10)

    with system_context(reason="verify attempt concurrency"):
        attempt.refresh_from_db()
    assert attempt.result_recorded_at is not None
    assert finalization.recorded
    assert finalization.applied != revocation.revoked
    assert revocation.already_recorded != revocation.revoked
