"""Bridge admission composes native identity retention and actor checks."""

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.utils import timezone
from rebac import system_context

from angee.base.db import related_on
from angee.base.identity import public_id_of
from angee.integrate.models import Bridge
from angee.integrate.sync import SyncDispatch
from angee.workflows.attempts import JsonPresence
from angee.workflows_integrate.admission import admit_bridge_cycle
from tests.conftest import make_integration
from tests.messaging_models import Channel
from tests.workflows import WorkflowRun, admit_workflow_actor, workflow_with_steps


@pytest.fixture
def cycle(record_sync_tables: None, workflow_engine_tables: None, no_workflow_queue: None) -> tuple[Any, Any, Any, str]:
    with system_context(reason="test bridge admission"):
        bridge = make_integration("cycle-admission", model=Channel)
        bridge.mark_sync_queued(now=timezone.now())
        owner = related_on(bridge, "owner", using="default")
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
        using="default",
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
    assert bridge.sync_progress["details"]["run"] == public_id_of(first)
    assert bridge.next_sync_at is None
    assert bridge.sync_stage == bridge.SyncStage.SYNCING


def test_conflicting_frozen_input_rejects_same_identity(cycle: tuple[Any, Any, Any, str]) -> None:
    _admit(cycle, input={"scope": "one"})
    with pytest.raises(ValidationError, match="frozen input"):
        _admit(cycle, input={"scope": "two"})


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
            using="default",
        )


def test_actor_is_active_integration_owner_never_workflow_author(cycle: tuple[Any, Any, Any, str]) -> None:
    bridge, workflow, owner, occurrence = cycle
    assert workflow.created_by_id != owner.pk
    run = _admit(cycle)
    assert run.created_by_id == owner.pk
    author = related_on(workflow, "created_by", using="default")
    with pytest.raises(PermissionDenied, match="Integration owner"):
        admit_bridge_cycle(
            bridge,
            workflow=workflow,
            occurrence_key=occurrence,
            actor=author,
            input=JsonPresence(True, None),
            using="default",
        )
    with system_context(reason="test inactive Integration owner"):
        owner.is_active = False
        owner.save(update_fields=["is_active"])
    with pytest.raises(PermissionDenied, match="active Integration owner"):
        _admit(cycle)


def test_nondefault_authorization_fails_before_admission(cycle: tuple[Any, Any, Any, str]) -> None:
    bridge, workflow, owner, occurrence = cycle
    with pytest.raises(ValidationError, match="default"):
        admit_bridge_cycle(
            bridge,
            workflow=workflow,
            occurrence_key=occurrence,
            actor=owner,
            input=JsonPresence(),
            using="secondary",
        )


def test_declared_key_dispatches_without_early_settlement(cycle: tuple[Any, Any, Any, str], monkeypatch: Any) -> None:
    bridge, workflow, _, _ = cycle
    monkeypatch.setattr(Channel, "sync_workflow_key", workflow.key)
    with override_settings(ANGEE_BRIDGE_SYNC_DISPATCH="angee.workflows_integrate.admission.dispatch_bridge_cycle"):
        with system_context(reason="test bridge dispatch"):
            assert bridge.run_sync(now=timezone.now()) is SyncDispatch.DISPATCHED
            bridge.refresh_from_db()
            run_pointer = bridge.sync_progress["details"]["run"]
            assert bridge.last_sync_status != "ok"
            bridge.mark_sync_queued(now=timezone.now() + timedelta(minutes=1))
            assert bridge.sync_stage == bridge.SyncStage.SYNCING
            assert bridge.sync_progress["details"]["run"] == run_pointer
    monkeypatch.setattr("angee.integrate.models.task_locks_are_cross_process", lambda: True)
    monkeypatch.setattr(Bridge, "is_syncing", property(lambda self: False))
    assert bridge.effective_sync_stage == bridge.SyncStage.SYNCING
