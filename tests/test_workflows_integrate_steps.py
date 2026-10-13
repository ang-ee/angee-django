"""Bridge paging and split coverage use real workflow and integration owners."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.db.models.functions import Now
from django.utils import timezone
from pydantic import ValidationError
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.decisions.testing.models import Decision
from angee.integrate.impl import BridgeImpl
from angee.integrate.states import ConflictKeep, StreamDirection, StreamKind, StreamPhase, SyncFailureKind
from angee.integrate.streams import (
    ApplyResult,
    CursorInvalid,
    RecordChange,
    StreamDefinition,
    StreamPage,
    advance_stream,
    open_stream,
)
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.workflows.runner import runner
from angee.workflows.testing.drivers import decide, load_workflow, run_until
from angee.workflows.testing.models import StepAttempt, StepRun, WorkflowRun
from angee.workflows_integrate import steps
from angee.workflows_integrate.steps import ConflictReview, CoverageInput, Rescan, StreamStage
from angee.workflows_integrate.testing.models import SyncCycleTestBridge
from tests.conftest import make_integration
from tests.stream_adapters import applied_history, failure_state


class WorkerLost(BaseException):
    """Interrupt the worker after an independently committed driver page."""


@pytest.fixture
def cycle(execution, register_step, monkeypatch):
    """A real owner-scoped bridge with deterministic bounded transport."""
    administrator, _ = execution
    bridge = make_integration("workflow-stream", model=SyncCycleTestBridge)
    extracted, applied, rescanned = [], [], []
    local_hashes = {}
    transport = SimpleNamespace(
        identity_reads=True,
        partitions=("",),
        size=2,
        fail=False,
        pages=None,
        direction=StreamDirection.PULL,
    )

    class Adapter(BridgeImpl):
        @property
        def supports_identity_reads(self):
            return transport.identity_reads

        def streams(self, *, deadline=None):
            return tuple(
                StreamDefinition(
                    "records",
                    partition=partition,
                    kind=StreamKind.RECORD_REPLICA,
                    direction=transport.direction,
                )
                for partition in transport.partitions
            )

        def record(self, stream, offset):
            key = f"{stream.partition}:{offset}"
            return RecordChange(
                external_key=key,
                source_payload={"offset": offset},
                source_hash=f"source-{offset}",
                local_hash=local_hashes.get(key, ""),
                mapped_payload={"offset": offset},
                projection={"offset": offset} if key in local_hashes else None,
            )

        def extract(self, stream, page_bound, *, deadline=None):
            assert not connection.in_atomic_block
            if transport.fail:
                raise ConnectionError("The transport is unavailable.")
            offset = stream.cursor.get("offset", 0)
            extracted.append((stream.partition, stream.generation, offset, page_bound))
            if transport.pages is not None:
                page = transport.pages.pop(0)
                if isinstance(page, Exception):
                    raise page
                return page
            end = min(transport.size, offset + page_bound)
            return StreamPage(
                records=tuple(self.record(stream, index) for index in range(offset, end)),
                cursor={"offset": end},
                exhausted=end == transport.size,
            )

        def read_keys(self, stream, keys):
            assert not connection.in_atomic_block
            rescanned.append((stream.partition, tuple(keys)))
            return tuple(self.record(stream, int(key.rsplit(":", 1)[1])) for key in keys)

        def apply_record(self, stream, record):
            assert connection.in_atomic_block
            applied.append(record.external_key)
            local_hashes[record.external_key] = record.source_hash
            return ApplyResult(mapped_payload=record.mapped_payload)

    monkeypatch.setattr(SyncCycleTestBridge, "backend", property(Adapter), raising=False)
    for step in (StreamStage, Rescan, ConflictReview):
        register_step(step)

    def start(step, value, *, config=None, mapped=False):
        node = {"step": "map", "body": {"step": step.key}} if mapped else {"step": step.key}
        if config is not None:
            node["config"] = config
        workflow = load_workflow(
            {"nodes": {"entry": node}, "results": [{"from": "entry"}]},
            key=bridge.sync_workflow_key,
            actor=administrator,
            subject_model=bridge._meta.label,
        )
        workflow.grant_record_access("starter", bridge.owner)
        return WorkflowRun.objects.start(workflow, actor=bridge.owner, subject=bridge, input=value)

    return SimpleNamespace(
        bridge=bridge,
        adapter=Adapter(bridge),
        transport=transport,
        start=start,
        extracted=extracted,
        applied=applied,
        rescanned=rescanned,
    )


def step_row(run):
    return system_queryset(StepRun).get(run=run)


def wake(run):
    """Move only time forward; wakeup and dispatch remain engine-owned."""
    with system_context(reason="test bridge coverage deadline"):
        StepRun.objects.filter(run=run, status="waiting", waiting_kind="time").update(
            wake_at=Now() - timedelta(seconds=1),
        )
    assert runner.wake() == 1


def conflict(cycle, stream, offset):
    with system_context(reason="test retained sync conflict"):
        link = RecordLink.objects.observe(stream, f"{stream.partition}:{offset}")
        return link.record_failure(
            kind=SyncFailureKind.CONFLICT, code=f"conflict_{offset}", source_hash=f"source-{offset}"
        )


def test_stream_pages_commit_cursors_and_sum_only_finalized_counts(cycle):
    run = cycle.start(StreamStage, {"key": "records", "page_bound": 1})
    assert runner.execute(step_row(run).pk)
    retained = step_row(run)
    stream = SyncStream.objects.current_for_bridge(cycle.bridge, "records").get()
    assert stream.cursor == {"offset": 1}
    assert retained.status == "ready" and retained.page_index == 1
    assert retained.state["cycle_items"] == 1 and retained.state["resets"] == 0
    assert len(retained.state["progress"]) == 64 and "cursor" not in retained.state

    run_until(run)

    assert run.status == "succeeded"
    assert run.output["counts"] == {"page_items": 1, "cycle_items": 2}
    assert cycle.extracted == [("", 1, 0, 1), ("", 1, 1, 1)]
    assert applied_history().count() == 2
    cycle.bridge.refresh_from_db()
    assert cycle.bridge.last_sync_items == 2


def test_lost_lease_resumes_after_the_committed_cursor(cycle, monkeypatch):
    real_advance = steps.advance_stream
    lost = False

    def commit_then_lose_worker(*args, **kwargs):
        nonlocal lost
        page = real_advance(*args, **kwargs)
        if not lost:
            lost = True
            raise WorkerLost()
        return page

    monkeypatch.setattr(steps, "advance_stream", commit_then_lose_worker)
    run = cycle.start(StreamStage, {"key": "records", "page_bound": 1})
    row = step_row(run)
    with pytest.raises(WorkerLost):
        runner.execute(row.pk)
    assert SyncStream.objects.current_for_bridge(cycle.bridge, "records").get().cursor == {"offset": 1}
    assert step_row(run).state == {}
    with system_context(reason="test lost stream worker lease"):
        StepRun.objects.filter(pk=row.pk).update(deadline_at=Now() - timedelta(seconds=1))
    assert runner.reap() == 1
    wake(run)

    run_until(run)

    assert run.status == "succeeded"
    assert cycle.applied == [":0", ":1"]
    assert cycle.extracted == [("", 1, 0, 1), ("", 1, 1, 1)]
    assert applied_history().count() == 2
    # Settlement counts acknowledge outputs; the first page committed independently.
    assert run.output["counts"]["cycle_items"] == 1
    assert list(
        system_queryset(StepAttempt).filter(step_run=row).order_by("number").values_list("result", flat=True)
    ) == ["timed_out", "succeeded"]


def test_map_keeps_each_partition_cursor_and_collects_its_counts(cycle):
    cycle.transport.partitions = ("east", "west")
    run = cycle.start(
        StreamStage,
        {
            "items": [
                {"key": "records", "partition": partition, "page_bound": 1} for partition in cycle.transport.partitions
            ]
        },
        mapped=True,
    )

    run_until(run)

    assert run.status == "succeeded"
    assert [item["output"]["counts"]["cycle_items"] for item in run.output] == [2, 2]
    assert sorted(cycle.applied) == ["east:0", "east:1", "west:0", "west:1"]
    cycle.bridge.refresh_from_db()
    assert cycle.bridge.last_sync_items == 4


def test_transport_failure_uses_native_retry_without_advancing_cursor(cycle):
    cycle.transport.fail = True
    run = cycle.start(StreamStage, {"key": "records", "page_bound": 1})
    assert runner.execute(step_row(run).pk)
    row = step_row(run)
    assert row.status == "waiting" and row.retries == 1
    assert SyncStream.objects.current_for_bridge(cycle.bridge, "records").get().cursor == {}
    cycle.transport.fail = False
    wake(run)

    run_until(run)

    assert run.status == "succeeded"
    assert run.output["counts"]["cycle_items"] == 2


def test_rescan_rotates_bounded_identity_reads_before_waiting(cycle):
    cycle.transport.partitions = ("east", "west")
    for partition in cycle.transport.partitions:
        stream = open_stream(cycle.bridge, "records", partition, cycle.adapter)
        advance_stream(stream, cycle.adapter)
        with system_context(reason="test due sync identities"):
            for offset in range(2):
                failure_state(
                    RecordLink.objects.get(stream=stream, external_key=f"{partition}:{offset}").record_failure(
                        kind=SyncFailureKind.SEMANTIC, code=f"retry_{offset}", source_hash=f"source-{offset}"
                    ),
                    retry_at=timezone.now(),
                )
    run = cycle.start(
        Rescan,
        {"streams": [{"key": "records", "partition": partition} for partition in cycle.transport.partitions]},
        config={"rescan_bound": 1},
    )

    assert runner.execute(step_row(run).pk)
    assert cycle.rescanned == [("east", ("east:0",))]
    assert step_row(run).state == {"rescan_index": 1}
    assert runner.execute(step_row(run).pk)
    assert cycle.rescanned == [("east", ("east:0",)), ("west", ("west:0",))]
    assert step_row(run).waiting_kind == "time"
    assert (
        sum(
            len(row.sync_failures(open_only=True))
            for row in SyncStream.objects.current_for_bridge(cycle.bridge, "records").with_actor(cycle.bridge.owner)
        )
        == 2
    )
    wake(run)

    run_until(run)

    assert run.status == "succeeded"
    assert [keys for _, keys in cycle.rescanned] == [("east:0",), ("west:0",), ("east:1",), ("west:1",)]
    assert not any(
        row.sync_failures(open_only=True)
        for row in SyncStream.objects.current_for_bridge(cycle.bridge, "records").with_actor(cycle.bridge.owner)
    )
    assert run.output["counts"] == {"streams": 2}


@pytest.mark.parametrize("fallback", [False, True])
def test_rescan_completes_requested_baseline_one_page_per_attempt(cycle, fallback):
    stream = open_stream(cycle.bridge, "records", "", cycle.adapter)
    advance_stream(stream, cycle.adapter)
    if fallback:
        cycle.transport.identity_reads = False
        with system_context(reason="test rescan baseline fallback"):
            failure_state(
                RecordLink.objects.get(stream=stream, external_key=":0").record_failure(
                    kind=SyncFailureKind.SEMANTIC, code="retry_baseline", source_hash="source-0"
                ),
                retry_at=timezone.now(),
            )
    else:
        SyncStream.objects.request_resync(stream)
    run = cycle.start(Rescan, {"streams": [{"key": "records"}]}, config={"rescan_bound": 1})

    assert runner.execute(step_row(run).pk)
    replacement = SyncStream.objects.current_for_bridge(cycle.bridge, "records").get()
    assert replacement.generation == 2 and replacement.phase == StreamPhase.BASELINE
    assert replacement.cursor == ({} if fallback else {"offset": 1})
    assert runner.execute(step_row(run).pk)
    replacement.refresh_from_db()
    assert replacement.cursor == {"offset": 1 if fallback else 2}
    assert replacement.phase == (StreamPhase.BASELINE if fallback else StreamPhase.DELTA)

    run_until(run)

    assert run.status == "succeeded"
    replacement.refresh_from_db()
    assert replacement.cursor == {"offset": 2} and replacement.phase == StreamPhase.DELTA


def test_rescan_waits_for_conflicts_without_applying_them(cycle):
    stream = open_stream(cycle.bridge, "records", "", cycle.adapter)
    advance_stream(stream, cycle.adapter)
    failure = conflict(cycle, stream, 0)
    cycle.applied.clear()
    run = cycle.start(Rescan, {"streams": [{"key": "records"}]})

    run_until(run)

    assert run.status == "waiting" and step_row(run).waiting_kind == "time"
    assert cycle.rescanned == [] and cycle.applied == []
    failure = failure_state(failure)
    assert failure.is_open


def test_conflicts_share_one_multiple_decision_and_retry_with_its_answer(cycle):
    stream = open_stream(cycle.bridge, "records", "", cycle.adapter)
    advance_stream(stream, cycle.adapter)
    conflicts = [conflict(cycle, stream, index) for index in range(2)]
    run = cycle.start(ConflictReview, {"streams": [{"key": "records"}]})
    run_until(run)
    row = step_row(run)
    decision = system_queryset(Decision).get(pk=row.decision_id)
    assert decision.records.with_actor(cycle.bridge.owner).count() == 2 and row.waiting_kind == "decision"
    decide(decision, actor=cycle.bridge.owner, chosen=[record.link.public_id for record in conflicts])
    run_until(run)
    assert run.status == "failed"
    assert len(SyncStream.objects.with_actor(cycle.bridge.owner).get(pk=stream.pk).sync_failures(open_only=True)) == 2
    for failure in conflicts:
        failure.resolve_conflict(keep=ConflictKeep.REMOTE)
    StepRun.objects.retry_step(row, actor=cycle.bridge.owner)
    run_until(run)
    assert run.status == "succeeded" and run.output["failure_ids"] == []
    assert system_queryset(Decision).count() == 1


def test_unresolved_conflict_fails_without_asking_another_question(cycle):
    stream = open_stream(cycle.bridge, "records", "", cycle.adapter)
    advance_stream(stream, cycle.adapter)
    conflict(cycle, stream, 0)
    run = cycle.start(ConflictReview, {"streams": [{"key": "records"}]})
    run_until(run)
    decision = system_queryset(Decision).get(pk=step_row(run).decision_id)
    decide(decision, actor=cycle.bridge.owner, chosen=[decision.proposal["alternatives"][0]["key"]])
    run_until(run)
    assert run.status == "failed" and system_queryset(Decision).count() == 1
    assert len(SyncStream.objects.with_actor(cycle.bridge.owner).get(pk=stream.pk).sync_failures(open_only=True)) == 1


@pytest.mark.parametrize("step", [StreamStage, Rescan])
def test_sync_cycles_refuse_a_stream_that_writes_back(cycle, step):
    """No cycle stage pushes, so a two-way partition is refused rather than left with refusals nothing retries."""
    cycle.transport.direction = StreamDirection.BIDIRECTIONAL
    if step is Rescan:
        open_stream(cycle.bridge, "records", "", cycle.adapter)
    value = {"key": "records"} if step is StreamStage else {"streams": [{"key": "records"}]}
    run = cycle.start(step, value)

    run_until(run)

    assert run.status == "failed"
    assert not cycle.extracted and not cycle.rescanned
    failure = system_queryset(StepAttempt).filter(step_run=step_row(run)).latest("number")
    assert "Workflow sync cycles pull only" in failure.error


def test_coverage_rejects_duplicate_partitions():
    with pytest.raises(ValidationError, match="distinct"):
        CoverageInput(streams=[{"key": "records"}, {"key": "records"}])


@pytest.mark.parametrize("step", [StreamStage, Rescan])
@pytest.mark.parametrize("fault", ["stalled", "reset"])
def test_sync_steps_stop_stalled_pages_and_repeated_baseline_resets(cycle, step, fault):
    """Both drivers must terminate malformed continuation rather than page forever."""
    cycle.transport.pages = (
        [CursorInvalid(), CursorInvalid()]
        if fault == "reset"
        else [
            StreamPage((), {}, exhausted=False),
            StreamPage((), {}, exhausted=False),
        ]
    )
    if step is Rescan:
        open_stream(cycle.bridge, "records", "", cycle.adapter)
    value = {"key": "records"} if step is StreamStage else {"streams": [{"key": "records"}]}
    run = cycle.start(step, value)

    run_until(run, max_steps=3)

    assert run.status == "failed"
    assert len(cycle.extracted) == 2
    row = step_row(run)
    assert row.attempt == 2 and row.page_index == 1
    failure = system_queryset(StepAttempt).get(step_run=row, number=2)
    assert ("fresh baseline" if fault == "reset" else "repeated a page") in failure.error
    assert "cursor" not in row.state
    cycle.bridge.refresh_from_db()
    assert cycle.bridge.sync_error == "Sync workflow failed."


@pytest.mark.parametrize("step", [StreamStage, Rescan])
def test_same_cursor_with_changed_replica_hashes_makes_progress(cycle, step):
    """Opaque cursors can remain fixed while their replica observations advance."""
    cycle.transport.pages = [
        StreamPage((RecordChange("record", {}, "first", mapped_payload={"value": "first"}),), {}, exhausted=False),
        StreamPage(
            (
                RecordChange(
                    "record",
                    {},
                    "second",
                    local_hash="first",
                    mapped_payload={"value": "second"},
                    projection={"value": "first"},
                ),
            ),
            {},
            exhausted=False,
        ),
        StreamPage((), {}),
    ]
    if step is Rescan:
        open_stream(cycle.bridge, "records", "", cycle.adapter)
    value = {"key": "records"} if step is StreamStage else {"streams": [{"key": "records"}]}
    run = cycle.start(step, value)

    run_until(run, max_steps=4)

    assert run.status == "succeeded"
    assert cycle.applied == ["record", "record"]
    assert list(applied_history().order_by("history_date").values_list("source_hash", flat=True)) == [
        "first",
        "second",
    ]
