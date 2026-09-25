"""Bridge admission composes native identity retention and actor checks."""

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rebac import system_context

from angee.integrate.models import Bridge
from angee.integrate.sync import SyncDispatch
from angee.testing.models import WorkflowRun
from angee.workflows import managers as workflow_managers
from angee.workflows.attempts import JsonPresence
from angee.workflows_integrate.admission import admit_bridge_cycle
from tests.conftest import make_integration
from tests.messaging_models import Channel
from tests.workflows import admit_workflow_actor, workflow_with_steps


@pytest.fixture
def cycle(composed_tables: None, no_workflow_queue: None) -> tuple[Any, Any, Any, str]:
    with system_context(reason="test bridge admission"):
        bridge = make_integration("cycle-admission", model=Channel)
        bridge.mark_sync_queued(now=timezone.now())
        owner = bridge.owner
        publication = workflow_with_steps(
            key="test-bridge-cycle",
            steps=({"key": "start", "step_class": "fixture"},),
            edges=(),
        )
        admit_workflow_actor(publication, owner)
    return bridge, publication, owner, bridge.sync_progress["queued_at"]


def _admit(cycle: tuple[Any, Any, Any, str], *, input: Any = None, **kwargs: Any) -> Any:
    bridge, workflow, owner, occurrence = cycle
    return admit_bridge_cycle(
        bridge,
        workflow=workflow,
        occurrence_key=occurrence,
        actor=owner,
        input=JsonPresence(True, input),
        **kwargs,
    )


def test_duplicate_delivery_retains_run_and_pointer(cycle: tuple[Any, Any, Any, str]) -> None:
    bridge, _, _, _ = cycle
    first = _admit(cycle)
    again = _admit(cycle)
    assert first.pk == again.pk
    with system_context(reason="test bridge admission evidence"):
        assert WorkflowRun.objects.for_subject(bridge).count() == 1
        bridge.refresh_from_db()
    assert bridge.sync_run_id == first.pk
    assert bridge.next_sync_at is None
    assert bridge.sync_stage == bridge.SyncStage.SYNCING


def test_dispatch_claim_fences_stale_callers_and_queue_clears_settled_pointer(
    cycle: tuple[Any, Any, Any, str],
) -> None:
    bridge, _, _, _ = cycle
    with system_context(reason="test dispatch claim compare-and-set"):
        stale = Channel.objects.get(pk=bridge.pk)
        assert bridge.claim_dispatch(17)
        started_at = bridge.last_sync_started_at
        assert not stale.claim_dispatch(18)
        assert not bridge.claim_dispatch(17)
        bridge.refresh_from_db()
        assert bridge.sync_run_id == 17
        assert bridge.last_sync_started_at == started_at
        assert not bridge.settle_dispatch(18, result=99)
        assert bridge.settle_dispatch(17, result=3)
        assert not bridge.settle_dispatch(17, result=99)
        assert bridge.last_sync_items == 3
        assert bridge.sync_run_id == 17
        bridge.mark_sync_queued(now=timezone.now())
        assert bridge.sync_run_id is None
        assert not bridge.sync_is_dispatched


def test_conflicting_frozen_input_rejects_same_identity(cycle: tuple[Any, Any, Any, str]) -> None:
    _admit(cycle, input={"scope": "one"})
    with pytest.raises(ValidationError, match="frozen input"):
        _admit(cycle, input={"scope": "two"})


def test_prepare_runs_after_workflow_locks_before_bridge_and_frozen_input(
    cycle: tuple[Any, Any, Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, workflow, owner, occurrence = cycle
    events: list[str] = []
    snapshot = {"scope": "one"}
    native_queryset = workflow_managers.system_queryset
    queryset_class = type(Channel.objects.get_queryset())
    native_lock = queryset_class.lock_if_supported

    def system_queryset(model: Any, **kwargs: Any) -> Any:
        if kwargs.get("lock"):
            events.append(model._meta.model_name)
        return native_queryset(model,  **kwargs)

    def lock_bridge(queryset: Any, *args: Any, **kwargs: Any) -> Any:
        if queryset.model is Channel:
            events.append("bridge")
        return native_lock(queryset, *args, **kwargs)

    def prepare() -> None:
        assert connection.in_atomic_block
        assert "workflow" in events and "workflowrun" in events
        assert "bridge" not in events
        events.append("prepare")

    def sync_input(current: Channel) -> dict[str, str]:
        assert events[-2:] == ["prepare", "bridge"]
        events.append("input")
        return snapshot

    monkeypatch.setattr(workflow_managers, "system_queryset", system_queryset)
    monkeypatch.setattr(queryset_class, "lock_if_supported", lock_bridge)
    monkeypatch.setattr(Channel, "sync_workflow_input", sync_input)

    def admit() -> Any:
        return admit_bridge_cycle(
            bridge, workflow=workflow, occurrence_key=occurrence, actor=owner, prepare=prepare
        )

    first = admit()
    assert first.input == {"scope": "one"}
    events.clear()
    assert admit().pk == first.pk
    events.clear()
    snapshot["scope"] = "two"
    with pytest.raises(ValidationError, match="frozen input"):
        admit()


def test_validate_new_rejects_second_active_cycle_even_on_another_lineage(cycle: tuple[Any, Any, Any, str]) -> None:
    bridge, _, owner, occurrence = cycle
    _admit(cycle)
    other = workflow_with_steps(key="other-cycle", steps=({"key": "start", "step_class": "fixture"},), edges=())
    admit_workflow_actor(other, owner)
    with pytest.raises(ValidationError, match="already has an active cycle"):
        admit_bridge_cycle(
            bridge,
            workflow=other,
            occurrence_key=occurrence + "-next",
            actor=owner,
            input=JsonPresence(True, None),
        )


def test_actor_is_active_integration_owner_never_workflow_author(cycle: tuple[Any, Any, Any, str]) -> None:
    bridge, workflow, owner, occurrence = cycle
    assert workflow.created_by_id != owner.pk
    run = _admit(cycle)
    assert run.created_by_id == owner.pk
    author = workflow.created_by
    with pytest.raises(PermissionDenied, match="Integration owner"):
        admit_bridge_cycle(
            bridge,
            workflow=workflow,
            occurrence_key=occurrence,
            actor=author,
            input=JsonPresence(True, None),
        )
    with system_context(reason="test inactive Integration owner"):
        owner.is_active = False
        owner.save(update_fields=["is_active"])
    with pytest.raises(PermissionDenied, match="active Integration owner"):
        _admit(cycle)


def test_declared_key_dispatches_without_early_settlement(cycle: tuple[Any, Any, Any, str], monkeypatch: Any) -> None:
    bridge, workflow, _, _ = cycle
    monkeypatch.setattr(Channel, "sync_workflow_key", workflow.key)
    starts: list[int] = []
    native_start = Channel.mark_sync_started

    def mark_sync_started(self: Channel, *, now: Any) -> None:
        starts.append(self.pk)
        native_start(self, now=now)

    monkeypatch.setattr(Channel, "mark_sync_started", mark_sync_started)
    with override_settings(ANGEE_BRIDGE_SYNC_DISPATCH="angee.workflows_integrate.admission.dispatch_bridge_cycle"):
        with system_context(reason="test bridge dispatch"):
            assert bridge.run_sync(now=timezone.now()) is SyncDispatch.DISPATCHED
            assert starts == [bridge.pk]
            bridge.refresh_from_db()
            run_pointer = bridge.sync_run_id
            assert bridge.last_sync_status != "ok"
            bridge.mark_sync_queued(now=timezone.now() + timedelta(minutes=1))
            assert bridge.sync_stage == bridge.SyncStage.SYNCING
            assert bridge.sync_run_id == run_pointer
    monkeypatch.setattr("angee.integrate.models.task_locks_are_cross_process", lambda: True)
    monkeypatch.setattr(Bridge, "is_syncing", property(lambda self: False))
    assert bridge.effective_sync_stage == bridge.SyncStage.SYNCING
