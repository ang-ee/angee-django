"""Execution composition over real driver commits; authored for supervisor execution."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from rebac import system_context

from angee.base.identity import public_id_of
from angee.integrate.records import DiscrepancyKind, DiscrepancyStatus, StreamKind
from angee.integrate.streams import RecordChange, StreamDefinition, StreamPage, open_stream
from angee.workflows.attempts import AttemptResultKind
from angee.workflows.models import StepRunStatus
from angee.workflows.steps import StepExecutionMode, TransientStepError
from angee.workflows_integrate import steps as integrate_steps
from angee.workflows_integrate.steps import BoundedStreamStage, CoverageGate
from tests.integrate_models import RecordLink, SyncDiscrepancy, SyncStream
from tests.messaging_models import Channel
from tests.test_integrate_streams import AppliedRecord, MemoryAdapter
from tests.test_integrate_streams import stream_bridge as stream_bridge
from tests.workflows import (
    Decision,
    StepAttempt,
    WorkflowDispatch,
    advance_once,
    execute_started,
    start_run,
    workflow_with_steps,
)
from tests.workflows import no_workflow_queue as no_workflow_queue
from tests.workflows import workflow_engine_tables as workflow_engine_tables

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def stage_registry() -> Iterator[None]:
    """Compose these operations through the same registry used by installed addons."""

    with override_settings(
        ANGEE_WORKFLOW_STEP_CLASSES={
            **settings.ANGEE_WORKFLOW_STEP_CLASSES,
            "integrate_stream": "angee.workflows_integrate.steps.BoundedStreamStage",
            "integrate_coverage": "angee.workflows_integrate.steps.CoverageGate",
        }
    ):
        yield


def _start_stage(bridge: Channel, *, coverage: bool = False, retry: bool = False) -> tuple[Any, Any]:
    value = {"bridge": {"model": bridge._meta.label_lower, "id": public_id_of(bridge)}}
    value.update({"streams": [{"key": "records"}]} if coverage else {"key": "records", "page_bound": 2})
    config = {"retry": {"max_attempts": 3, "backoff": {"wait": 7}}} if retry else {}
    workflow = workflow_with_steps(
        actor=bridge.owner,
        subject_declaration=bridge._meta.label_lower,
        steps=(
            {
                "key": "stage",
                "step_class": "integrate_coverage" if coverage else "integrate_stream",
                "input_binding": {"kind": "constant", "value": value},
                "config": config,
            },
        ),
        edges=(),
    )
    run = start_run(workflow, subject=bridge, actor=bridge.owner)
    return run, advance_once(run)[0]


@pytest.mark.parametrize("final_records", [(), ("second",)])
@pytest.mark.parametrize("retry_between_pages", [False, True])
def test_stage_retains_cycle_total_through_wait_and_retry(
    final_records: tuple[str, ...],
    retry_between_pages: bool,
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MemoryAdapter(
        pages=[
            StreamPage(("first",), {"offset": 1}, exhausted=False),
            *([ConnectionError("retry next page")] if retry_between_pages else []),
            StreamPage(final_records, {"offset": 2}),
        ]
    )
    monkeypatch.setattr(Channel, "backend", property(lambda self: adapter))
    run, step_run = _start_stage(stream_bridge, retry=retry_between_pages)

    execute_started(run)
    step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.WAITING
    with system_context(reason="test stream stage cursor"):
        stream = SyncStream.objects.get()
    assert step_run.resume_state == {
        "stream": public_id_of(stream),
        "generation": stream.generation,
        "cycle_items": 1,
    }
    assert stream.cursor == {"offset": 1}

    advance_once(run)
    execute_started(run)
    step_run.refresh_from_db()
    if retry_between_pages:
        assert step_run.status == StepRunStatus.STARTED
        assert step_run.resume_state["cycle_items"] == 1
        with system_context(reason="test stream cycle total retry"):
            successor = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        execute_started(run, now=successor.available_at)
        step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED
    assert step_run.output["counts"] == {"page_items": len(final_records), "cycle_items": 1 + len(final_records)}
    assert step_run.output["discrepancy_ids"] == []
    assert step_run.output["evidence"][0]["id"] == public_id_of(stream)
    assert BoundedStreamStage.execution_mode is StepExecutionMode.STANDARD


def test_semantic_failure_quarantines_and_continues_later_records(
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MemoryAdapter(
        pages=[StreamPage(("bad", "good"), {"offset": 2})],
        semantic_key="bad",
    )
    monkeypatch.setattr(Channel, "backend", property(lambda self: adapter))
    run, step_run = _start_stage(stream_bridge)

    execute_started(run)

    step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED
    with system_context(reason="test quarantine stage"):
        discrepancy = SyncDiscrepancy.objects.get()
        assert discrepancy.status == DiscrepancyStatus.OPEN
        assert SyncStream.objects.get().cursor == {"offset": 2}
    assert AppliedRecord.objects.filter(key="good").exists()
    assert not AppliedRecord.objects.filter(key="bad").exists()
    assert step_run.output["counts"] == {"page_items": 1, "cycle_items": 1}
    assert step_run.output["discrepancy_ids"] == [public_id_of(discrepancy)]


def test_infrastructure_failure_raises_and_engine_retains_declared_retry(
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MemoryAdapter(pages=[ConnectionError("transport unavailable")])
    monkeypatch.setattr(Channel, "backend", property(lambda self: adapter))
    run, step_run = _start_stage(stream_bridge, retry=True)
    raised = []
    invoke = BoundedStreamStage.run

    def capture_error(self: Any, step_run: Any, *, now: Any, using: str | None = None) -> Any:
        try:
            return invoke(self, step_run, now=now, using=using)
        except TransientStepError as error:
            raised.append(str(error))
            raise

    monkeypatch.setattr(BoundedStreamStage, "run", capture_error)
    with system_context(reason="test original stream attempt"):
        original = StepAttempt.objects.get(pk=step_run.current_attempt_id)

    execute_started(run)

    step_run.refresh_from_db()
    original.refresh_from_db()
    assert raised == ["transport unavailable"]
    assert original.result_kind == AttemptResultKind.TRANSIENT_ERROR
    assert step_run.status == StepRunStatus.STARTED
    with system_context(reason="test retained stream retry"):
        successor = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(step_attempt=successor)
        assert SyncStream.objects.get().cursor == {}
    assert successor.retry_of_id == original.pk
    assert successor.ordinal == original.ordinal + 1
    assert dispatch.available_at == successor.available_at
    assert successor.available_at == original.result_recorded_at + timedelta(seconds=7)


def test_crash_after_commit_replays_from_stream_cursor(
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SimulatedCrash(BaseException):
        pass

    class CursorAdapter(MemoryAdapter):
        def extract(self, stream: Any, page_bound: int, *, using: str | None = None) -> StreamPage:
            position = stream.cursor.get("offset", 0)
            self.extracted += 1
            return StreamPage((f"record-{position}",), {"offset": position + 1}, exhausted=position == 1)

    adapter = CursorAdapter()
    monkeypatch.setattr(Channel, "backend", property(lambda self: adapter))
    run, step_run = _start_stage(stream_bridge)
    manager_class = type(StepAttempt.objects)
    finalize = manager_class.finalize

    def crash_finalize(*args: Any, **kwargs: Any) -> None:
        raise SimulatedCrash()

    monkeypatch.setattr(manager_class, "finalize", crash_finalize)
    with pytest.raises(SimulatedCrash):
        execute_started(run)
    monkeypatch.setattr(manager_class, "finalize", finalize)
    with system_context(reason="test committed stream before replay"):
        assert SyncStream.objects.get().cursor == {"offset": 1}
    step_run.refresh_from_db()
    assert step_run.resume_state == {}

    result = BoundedStreamStage().run(step_run, now=timezone.now())

    assert result.kind == "done"
    # The driver's earlier commit survives the crash; its unfinalized telemetry
    # does not. Count only the page reported by the replayed pulse.
    assert result.output["counts"] == {"page_items": 1, "cycle_items": 1}
    assert adapter.extracted == 2
    assert sorted(AppliedRecord.objects.values_list("key", flat=True)) == ["record-0", "record-1"]
    with system_context(reason="test committed stream after replay"):
        assert SyncStream.objects.get().cursor == {"offset": 2}


def test_retry_prepares_cycle_when_first_attempt_never_reached_first_page(
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = open_stream(stream_bridge, StreamDefinition("records", kind=StreamKind.RECORD_REPLICA))
    link = RecordLink.objects.observe(stream, "repaired")
    SyncDiscrepancy.objects.record(stream, link=link, kind=DiscrepancyKind.SEMANTIC, code="retry-on-baseline")
    adapter = MemoryAdapter(pages=[StreamPage((RecordChange("repaired", {}, "repaired"),), {"offset": 1})])
    monkeypatch.setattr(Channel, "backend", property(lambda self: adapter))
    prepare = integrate_steps.begin_stream_cycle
    calls = []

    def interrupted_prepare(stream: Any, adapter: Any = None, *, using: str | None = None) -> Any:
        calls.append(stream.pk)
        if len(calls) == 1:
            raise ConnectionError("first preparation interrupted")
        return prepare(stream, adapter, using=using)

    monkeypatch.setattr(integrate_steps, "begin_stream_cycle", interrupted_prepare)
    run, step_run = _start_stage(stream_bridge, retry=True)
    execute_started(run)
    step_run.refresh_from_db()
    with system_context(reason="test unprepared cycle retry"):
        successor = StepAttempt.objects.get(pk=step_run.current_attempt_id)

    execute_started(run, now=successor.available_at)

    step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.WAITING
    assert step_run.resume_state["cycle_items"] == 0
    assert calls == [stream.pk, stream.pk]
    with system_context(reason="test prepared retry baseline"):
        current = SyncStream.objects.current_for_bridge(stream_bridge, "records").get()
        assert current.generation == stream.generation + 1
        assert SyncDiscrepancy.objects.get().stream_id == current.pk
    advance_once(run, now=step_run.wait_until)
    execute_started(run, now=step_run.wait_until)
    step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED
    assert step_run.output["counts"] == {"page_items": 1, "cycle_items": 1}
    assert calls == [stream.pk, stream.pk]
    with system_context(reason="test reset wait retains cycle preparation"):
        assert SyncStream.objects.current_for_bridge(stream_bridge, "records").get().pk == current.pk


def test_stage_rejects_a_bridge_other_than_the_admitted_subject(
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    _, step_run = _start_stage(stream_bridge)
    step_run.input["bridge"]["id"] = "unknown-bridge"
    with pytest.raises(ValidationError, match="admitted workflow subject"):
        BoundedStreamStage().run(step_run, now=timezone.now())


@pytest.mark.parametrize("kind", [DiscrepancyKind.SEMANTIC, DiscrepancyKind.MISSING_DEPENDENCY])
def test_coverage_waits_for_unresolved_semantic_and_dependency_rows(
    kind: str,
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    stream = open_stream(stream_bridge, StreamDefinition("records"))
    discrepancy = SyncDiscrepancy.objects.record(stream, kind=kind, code="blocked")
    run, step_run = _start_stage(stream_bridge, coverage=True)

    execute_started(run)
    step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.WAITING
    assert step_run.resume_state == {"streams": [public_id_of(stream)]}
    assert CoverageGate.execution_mode is StepExecutionMode.DATABASE_COMMAND

    SyncDiscrepancy.objects.resolve(discrepancy)
    advance_once(run, now=step_run.wait_until)
    execute_started(run, now=step_run.wait_until)
    step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED


def test_coverage_raises_one_native_decision_per_conflict(
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    stream = open_stream(stream_bridge, StreamDefinition("records"))
    rows = [
        SyncDiscrepancy.objects.record(stream, kind=DiscrepancyKind.CONFLICT, code=f"conflict-{index}")
        for index in range(2)
    ]
    run, step_run = _start_stage(stream_bridge, coverage=True)

    execute_started(run)

    step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.WAITING
    with system_context(reason="test coverage conflict Decisions"):
        decisions = list(Decision.objects.filter(step_run=step_run).order_by("pk"))
    assert len(decisions) == 2
    assert {decision.payload["discrepancy"] for decision in decisions} == {public_id_of(row) for row in rows}
    assert step_run.resume_state["_resume_after_decisions"] is True
    # Re-evaluating the data predicate cannot recreate those Decisions or accept
    # an unresolved row merely because the operator has already reviewed it.
    result = CoverageGate().run(step_run, now=timezone.now())
    assert result.kind == "wait"
    assert result.decisions == ()


def test_coverage_includes_conflicts_retained_through_epoch_reset(
    stream_bridge: Channel,
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    stream = open_stream(stream_bridge, StreamDefinition("records"))
    discrepancy = SyncDiscrepancy.objects.record(stream, kind=DiscrepancyKind.CONFLICT, code="prior-epoch")
    successor = SyncStream.objects.bump_generation(stream)
    run, step_run = _start_stage(stream_bridge, coverage=True)

    execute_started(run)

    step_run.refresh_from_db()
    discrepancy.refresh_from_db()
    assert step_run.status == StepRunStatus.WAITING
    assert discrepancy.stream_id == successor.pk
    with system_context(reason="test retained conflict coverage"):
        assert Decision.objects.get(step_run=step_run).payload["discrepancy"] == public_id_of(discrepancy)
