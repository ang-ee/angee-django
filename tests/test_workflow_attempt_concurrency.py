"""PostgreSQL serialization contracts for retained workflow attempts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rebac import system_context

from angee.workflows.attempts import (
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    InvocationAdmission,
    LeaseRevocationReason,
)
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


def test_concurrent_claim_reuses_one_active_attempt(scheduled_step_run: StepRun) -> None:
    start = Barrier(2)
    claimed_at = timezone.now()

    def claim() -> tuple[int, bool]:
        start.wait(timeout=5)
        result = StepAttempt.objects.claim(
            scheduled_step_run,
            input=AttemptInput(present=True, value={"stable": True}),
            claimed_at=claimed_at,
        )
        return result.attempt.pk, result.newly_claimed

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, claim), pool.submit(_thread, claim))
        outcomes = [future.result(timeout=10) for future in futures]

    assert len({attempt_id for attempt_id, _ in outcomes}) == 1
    assert sorted(newly_claimed for _, newly_claimed in outcomes) == [False, True]


def test_concurrent_physical_delivery_is_admitted_once(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    start = Barrier(2)

    def admit() -> InvocationAdmission:
        start.wait(timeout=5)
        return StepAttempt.objects.admit_invocation(
            attempt.pk,
            lease_token=attempt.lease_token,
            at=timezone.now(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, admit), pool.submit(_thread, admit))
        outcomes = [future.result(timeout=10) for future in futures]

    assert sorted(outcomes) == sorted(
        [InvocationAdmission.FIRST_START, InvocationAdmission.ALREADY_STARTED]
    )


def test_result_and_revocation_are_serialized_without_losing_evidence(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    assert StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=timezone.now()
    ) == InvocationAdmission.FIRST_START
    start = Barrier(2)

    def finalize() -> Any:
        start.wait(timeout=5)
        return StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(AttemptResultKind.ERROR, error="failed"),
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
