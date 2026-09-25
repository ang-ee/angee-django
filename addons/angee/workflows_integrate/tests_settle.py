"""Terminal engine delivery settles only the Bridge's retained current cycle."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone
from rebac import system_context

from angee.base.identity import public_id_of
from angee.integrate.sync import BridgeProgressReporter
from angee.testing.models import StepAttempt, WorkflowDispatch
from angee.workflows import engine
from angee.workflows.attempts import JsonPresence
from angee.workflows.configs import WorkflowStepConfig
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.states import RunStatus
from angee.workflows.steps import StepResult, TransientStepError
from angee.workflows_integrate.admission import admit_bridge_cycle
from angee.workflows_integrate.settle import settle_bridge_run
from angee.workflows_integrate.steps import BoundedStreamStage, StreamStageOutput
from tests.conftest import make_integration
from tests.messaging_models import Channel
from tests.workflows import admit_workflow_actor, advance_once, execute_started, workflow_with_steps

pytestmark = pytest.mark.django_db(transaction=True)


class SettlementStreamConfig(WorkflowStepConfig):
    """Only the retained test outcome differs from the stream's native config."""

    mode: str = "success"


class SettlementStream(BoundedStreamStage):
    """Exercise stream output settlement without performing a transport page."""

    config_model = SettlementStreamConfig

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del self, now
        step = step_run.step
        assert step is not None
        if step.config.get("mode") == "failure":
            raise RuntimeError("private provider response must stay in workflow evidence")
        return StepResult.done(
            output=StreamStageOutput(
                counts={"page_items": 4, "cycle_items": 9},
                discrepancy_ids=[],
                evidence=[],
            ).model_dump(mode="json")
        )


@pytest.fixture
def settlement_bridge(
    composed_tables: None,
    no_workflow_queue: None,
    settings: Any,
) -> Channel:
    """Compose existing record/workflow fixtures and the declared handler seam."""

    del composed_tables, no_workflow_queue
    settings.ANGEE_WORKFLOW_SUBJECT_SETTLERS = {
        "angee.integrate.models.Bridge": "angee.workflows_integrate.settle.settle_bridge_run",
    }
    settings.ANGEE_WORKFLOW_STEP_CLASSES = {
        **settings.ANGEE_WORKFLOW_STEP_CLASSES,
        SettlementStream.key: f"{__name__}.SettlementStream",
    }
    return make_integration("workflow-settlement", model=Channel)


def _admit(bridge: Channel, *, occurrence: str, mode: str = "success") -> Any:
    with system_context(reason="test Bridge cycle publication"):
        actor = bridge.owner
    config: dict[str, Any] = {"mode": mode}
    if mode == "retry_exhaustion":
        config["retry"] = {"max_attempts": 2, "backoff": {"wait": 7}}
    workflow = workflow_with_steps(
        actor=actor,
        subject_declaration="messaging.channel",
        steps=(
            {
                "key": "stream",
                "step_class": SettlementStream.key,
                "config": config,
                "input_binding": {
                    "kind": "constant",
                    "value": {
                        "bridge": {"model": bridge._meta.label_lower, "id": public_id_of(bridge)},
                        "key": "records",
                    },
                },
            },
            {"key": "unrelated", "is_entry": False, "config": {"output": {"counts": {"cycle_items": 99}}}},
        ),
        edges=(("stream", "unrelated", ""),),
    )
    admit_workflow_actor(workflow, actor)
    return admit_bridge_cycle(
        bridge,
        workflow=workflow,
        occurrence_key=occurrence,
        actor=actor,
        input=JsonPresence(present=True, value={}),
    )


def _finish(run: Any, *, mode: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Use the retained engine paths, leaving subject delivery pending for inspection."""

    clock = [timezone.now()]
    monkeypatch.setattr(engine.timezone, "now", lambda: clock[0])
    if mode == "cancel":
        engine.cancel(run, actor=run.admission_actor())
    else:
        if mode == "retry_exhaustion":

            def fail(self: SettlementStream, step_run: Any, *, now: datetime) -> StepResult:
                del self, step_run, now
                raise TransientStepError("private retry response")

            monkeypatch.setattr(SettlementStream, "run", fail)
        advance_once(run, now=clock[0])
        execute_started(run, now=clock[0])
        if mode == "retry_exhaustion":
            with system_context(reason="test Bridge retry remains unsettled"):
                assert not WorkflowDispatch.objects.filter(run=run, kind=WorkflowDispatchKind.RUN_SETTLE).exists()
                attempts = list(
                    StepAttempt.objects.filter(
                        step_run__run=run,
                        step_run__step__key="stream",
                    ).order_by("ordinal")
                )
                assert len(attempts) == 2
                assert attempts[1].retry_of_id == attempts[0].pk
                assert attempts[1].available_at == clock[0] + timedelta(seconds=7)
            clock[0] += timedelta(seconds=7)
            execute_started(run, now=clock[0])
        advance_once(run, now=clock[0])
        execute_started(run, now=clock[0], key="unrelated")
        advance_once(run, now=clock[0])
    with system_context(reason="test Bridge terminal intent"):
        run.refresh_from_db()
        assert run.is_terminal
        return WorkflowDispatch.objects.get(run=run, kind=WorkflowDispatchKind.RUN_SETTLE)


@pytest.mark.parametrize("mode", ["success", "failure", "retry_exhaustion", "cancel"])
@pytest.mark.parametrize("stage", ["queued", "discovering", "syncing"])
def test_each_terminal_path_settles_once_and_clears_busy_stage(
    settlement_bridge: Channel,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    stage: str,
) -> None:
    bridge = settlement_bridge
    run = _admit(bridge, occurrence="first", mode=mode)
    pointer = run.pk
    with system_context(reason="test Bridge busy stage before settlement"):
        BridgeProgressReporter(bridge).report(stage, details={"records": 7})
    calls: list[str] = []
    native_success, native_error = Channel.record_sync, Channel.record_sync_error

    def record_sync(self: Channel, result: int, *, now: datetime) -> None:
        calls.append("success")
        native_success(self, result, now=now)

    def record_sync_error(self: Channel, error: Exception, *, now: datetime) -> None:
        calls.append("error")
        native_error(self, error, now=now)

    monkeypatch.setattr(Channel, "record_sync", record_sync)
    monkeypatch.setattr(Channel, "record_sync_error", record_sync_error)
    dispatch = _finish(run, mode=mode, monkeypatch=monkeypatch)
    with system_context(reason="test Bridge waits for durable settlement"):
        bridge.refresh_from_db()
        assert bridge.sync_stage == stage
    assert engine.settle_run_dispatch(dispatch.pk, expected_run_id=run.pk) == {"settled": 1}
    assert engine.settle_run_dispatch(dispatch.pk, expected_run_id=run.pk) == {"settled": 0}
    settle_bridge_run(run)
    settle_bridge_run(run)
    with system_context(reason="test Bridge settlement exactly once"):
        bridge.refresh_from_db()
        dispatch.refresh_from_db()
        assert WorkflowDispatch.objects.filter(run=run, kind=WorkflowDispatchKind.RUN_SETTLE).count() == 1
    assert dispatch.consumed_at is not None
    assert bridge.sync_run_id == pointer
    assert bridge.sync_stage not in (bridge.SyncStage.QUEUED, *bridge.LIVE_SYNC_STAGES)
    if mode == "success":
        assert run.status == RunStatus.SUCCEEDED
        assert calls == ["success"]
        assert bridge.sync_stage == bridge.SyncStage.COMPLETED
        assert bridge.last_sync_status == "ok"
        assert bridge.last_sync_items == 9
    else:
        assert run.status == (RunStatus.CANCELED if mode == "cancel" else RunStatus.FAILED)
        assert calls == ["error"]
        assert bridge.sync_stage == bridge.SyncStage.FAILED
        assert bridge.last_sync_status == "error"
        assert bridge.sync_error == ("Sync workflow was canceled." if mode == "cancel" else "Sync workflow failed.")
        assert "private" not in str(bridge.sync_progress)


def test_late_terminal_delivery_cannot_settle_a_newer_bridge_cycle(
    settlement_bridge: Channel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = settlement_bridge
    old_run = _admit(bridge, occurrence="old")
    old_dispatch = _finish(old_run, mode="success", monkeypatch=monkeypatch)
    new_run = _admit(bridge, occurrence="new")
    pointer = new_run.pk

    assert engine.settle_run_dispatch(old_dispatch.pk, expected_run_id=old_run.pk) == {"settled": 1}
    settle_bridge_run(old_run)
    with system_context(reason="test late settlement preserves current cycle"):
        bridge.refresh_from_db()
    assert bridge.sync_run_id == pointer
    assert bridge.sync_stage == bridge.SyncStage.SYNCING
    assert bridge.last_sync_status == ""

    with system_context(reason="test current cycle owner"):
        actor = bridge.owner
    engine.cancel(new_run, actor=actor)
    with system_context(reason="test current cycle cancellation intent"):
        dispatch = WorkflowDispatch.objects.get(run=new_run, kind=WorkflowDispatchKind.RUN_SETTLE)
    assert engine.settle_run_dispatch(dispatch.pk, expected_run_id=new_run.pk) == {"settled": 1}
    with system_context(reason="test current cycle settles after late delivery"):
        bridge.refresh_from_db()
    assert bridge.sync_stage == bridge.SyncStage.FAILED
    assert bridge.sync_run_id == pointer


@pytest.mark.parametrize("details", [None, [], "malformed"])
def test_malformed_telemetry_does_not_prevent_terminal_settlement(
    settlement_bridge: Channel,
    monkeypatch: pytest.MonkeyPatch,
    details: Any,
) -> None:
    bridge = settlement_bridge
    run = _admit(bridge, occurrence="malformed-progress")
    dispatch = _finish(run, mode="success", monkeypatch=monkeypatch)
    with system_context(reason="test malformed Bridge progress"):
        bridge.refresh_from_db()
        bridge.sync_progress = {**bridge.sync_progress, "details": details}
        bridge.save(update_fields=["sync_progress", "updated_at"])

    assert engine.settle_run_dispatch(dispatch.pk, expected_run_id=run.pk) == {"settled": 1}
    with system_context(reason="test malformed telemetry does not own dispatch identity"):
        bridge.refresh_from_db()
        dispatch.refresh_from_db()
    assert dispatch.consumed_at is not None
    assert bridge.sync_progress["details"] == details
    assert bridge.sync_run_id == run.pk
    assert bridge.sync_stage == bridge.SyncStage.COMPLETED
    assert bridge.last_sync_status == "ok"
    assert bridge.last_sync_items == 9
