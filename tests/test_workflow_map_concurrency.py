"""PostgreSQL races for retained Map generation ownership."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rebac import system_context

from angee.workflows.attempts import AttemptResultKind
from angee.workflows.models import StepRunStatus
from angee.workflows.steps import HandlerStep, StepResult
from tests.test_workflow_retained_map import _map_workflow
from tests.workflows import StepAttempt, StepRun, advance_once, execute_started, start_run

pytest_plugins = ("tests.workflows",)
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL Map serialization contract",
    ),
]


def test_concurrent_map_aggregate_records_one_current_generation(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    run = start_run(_map_workflow(item="one", explicit=False))
    advance_once(run)
    execute_started(run)
    with system_context(reason="load concurrent Map aggregation"):
        controller = StepRun.objects.get(run=run, step__key="map")
        expansion_id = controller.current_attempt_id
    starting = Barrier(2)

    def aggregate() -> str:
        close_old_connections()
        try:
            starting.wait(timeout=5)
            try:
                result = StepAttempt.objects.record_map_aggregate(
                    controller.pk,
                    expansion_attempt_id=expansion_id,
                    at=timezone.now(),
                )
            except ValidationError:
                return "fenced"
            return "recorded" if result is not None else "waiting"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(aggregate) for _ in range(2)]
        outcomes = sorted(future.result(timeout=10) for future in futures)

    assert outcomes == ["fenced", "recorded"]
    with system_context(reason="verify concurrent Map aggregation"):
        controller.refresh_from_db()
        attempts = list(StepAttempt.objects.filter(step_run=controller).order_by("ordinal"))
    assert controller.status == StepRunStatus.SUCCEEDED
    assert [attempt.result_kind for attempt in attempts] == [
        str(AttemptResultKind.WAIT),
        str(AttemptResultKind.DONE),
    ]
