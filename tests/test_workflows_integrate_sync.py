"""Tests for workflow/Bridge sync admission composition."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from angee.integrate.models import Bridge
from angee.workflows.attempts import JsonPresence
from angee.workflows.states import RunOrigin, RunStatus, WorkflowPurpose
from angee.workflows_integrate.models import (
    IntegrationSyncWorkflow,
    IntegrationSyncWorkflowRun,
    IntegrationWorkflowBridge,
)
from angee.workflows_integrate.sync import (
    IntegrationSyncDefinition,
    IntegrationSyncOccurrence,
    IntegrationSyncTerminalOutcome,
    _LaunchCapability,
    integration_sync_launch_capability,
    sync_cycle_dedup_key,
    workflow_run_execution_ref,
    workflow_run_id_from_execution_ref,
)


class _WorkflowBase:
    def validate_run_launch(self, **facts: Any) -> None:
        self.super_launch_facts = facts


class ComposedWorkflow(IntegrationSyncWorkflow, _WorkflowBase):
    class Meta:
        app_label = "tests"
        abstract = False


class _RunBase:
    def deliver_terminal_effect(self, *, at: Any) -> None:
        self.super_terminal_at = at


class ComposedRun(IntegrationSyncWorkflowRun, _RunBase):
    class Meta:
        app_label = "tests"
        abstract = False


class CapabilityBridge(Bridge):
    class Meta:
        app_label = "tests"
        abstract = False

    def _admit_integration_sync_workflow(self, capability: Any, **facts: Any) -> None:
        self.admitted = (capability, facts)

    def deliver_integration_sync_terminal(self, run: Any, *, at: Any) -> None:
        self.delivered = (run, at)


class OtherCapabilityBridge(CapabilityBridge):
    class Meta:
        app_label = "tests"
        abstract = False


def _occurrence() -> IntegrationSyncOccurrence:
    return IntegrationSyncOccurrence(
        kind="scheduled",
        key="poll:2026-09-12T12:00:00Z",
        occurred_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        window_key="poll:2026-09-12T12:00:00Z",
    )


def _capability(bridge: CapabilityBridge, envelope: dict[str, Any]) -> _LaunchCapability:
    return _LaunchCapability(
        alias="default",
        connection_id=0,
        atomic_id=0,
        bridge_model=CapabilityBridge,
        bridge_id=int(bridge.pk),
        actor_id=7,
        definition=IntegrationSyncDefinition(key="odoo-sync", version_id=11, digest="a" * 64),
        dedup_key="integration-sync:cycle",
        occurrence_id="poll:2026-09-12T12:00:00Z",
        envelope=envelope,
    )


def _workflow(*, purpose: WorkflowPurpose, pk: int | None = None) -> ComposedWorkflow:
    workflow = ComposedWorkflow()
    workflow.pk = pk
    workflow.purpose = purpose
    workflow.key = "odoo-sync"
    workflow.error_workflow_id = None
    return workflow


def _run(*, workflow: Any, origin: RunOrigin, status: RunStatus, subject: Any) -> ComposedRun:
    run = ComposedRun()
    run.workflow = workflow
    run.origin = origin
    run.status = status
    run.subject = subject
    return run


def test_cycle_dedup_uses_only_bridge_and_issued_occurrence() -> None:
    """Changing pinned facts cannot manufacture a second cycle identity."""

    bridge = CapabilityBridge(id=9)
    occurrence = _occurrence()
    first = sync_cycle_dedup_key(bridge, occurrence)

    assert first == sync_cycle_dedup_key(bridge, occurrence)
    assert first != sync_cycle_dedup_key(CapabilityBridge(id=10), occurrence)
    assert first != sync_cycle_dedup_key(
        bridge,
        IntegrationSyncOccurrence(
            kind="scheduled",
            key="poll:next",
            occurred_at=occurrence.occurred_at + timedelta(minutes=5),
            window_key="poll:next",
        ),
    )
    assert first == sync_cycle_dedup_key(
        bridge,
        IntegrationSyncOccurrence(
            kind="scheduled",
            key=occurrence.key,
            occurred_at=occurrence.occurred_at + timedelta(hours=1),
            window_key="changed-window",
        ),
    )


def test_occurrence_and_definition_reject_malformed_identity() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        IntegrationSyncOccurrence(
            kind="manual", key="request:1", occurred_at=datetime(2026, 9, 12), window_key="request:1"
        ).canonical()
    with pytest.raises(ValueError, match="SHA-256"):
        IntegrationSyncDefinition(key="odoo-sync", version_id=1, digest="wrong")


def test_workflow_execution_reference_parser_is_closed() -> None:
    run = type("Run", (), {"pk": 17})()
    assert workflow_run_execution_ref(run) == "workflow-run:17"
    assert workflow_run_id_from_execution_ref("workflow-run:17") == 17
    assert workflow_run_id_from_execution_ref("workflow:17") is None
    assert workflow_run_id_from_execution_ref("workflow-run:-1") is None


def test_non_sync_workflow_keeps_ordinary_launch_behavior() -> None:
    workflow = _workflow(purpose=WorkflowPurpose.AUTOMATION)
    workflow.validate_run_launch(marker="ordinary")
    assert workflow.super_launch_facts == {"marker": "ordinary"}


@pytest.mark.parametrize(
    "override",
    [
        {"exact_version": False},
        {"origin": RunOrigin.MANUAL},
        {"trigger": object()},
        {"parent_step_run": object()},
        {"dedup_key": "different"},
        {"occurrence_id": "different"},
        {"input": JsonPresence(False, None)},
    ],
)
def test_sync_workflow_rejects_every_generic_launch_shape(override: dict[str, Any]) -> None:
    bridge = CapabilityBridge(id=3)
    envelope = {"schema_version": 1}
    capability = _capability(bridge, envelope)
    workflow = _workflow(
        pk=capability.definition.version_id,
        purpose=WorkflowPurpose.INTEGRATION_SYNC,
    )
    facts = {
        "subject": bridge,
        "actor": type("Actor", (), {"pk": 7})(),
        "origin": RunOrigin.INTEGRATION_SYNC,
        "trigger": None,
        "parent_step_run": None,
        "dedup_key": capability.dedup_key,
        "occurrence_id": capability.occurrence_id,
        "input": JsonPresence(True, envelope),
        "retained_run": None,
        "exact_version": True,
    }
    facts.update(override)
    with integration_sync_launch_capability(capability), pytest.raises(
        ValidationError, match="exact Bridge launch capability"
    ):
        workflow.validate_run_launch(**facts)


def test_sync_workflow_accepts_only_matching_private_capability() -> None:
    bridge = CapabilityBridge(id=3)
    envelope = {"schema_version": 1}
    capability = _capability(bridge, envelope)
    workflow = _workflow(pk=11, purpose=WorkflowPurpose.INTEGRATION_SYNC)
    actor = type("Actor", (), {"pk": 7})()
    facts = {
        "subject": bridge,
        "actor": actor,
        "origin": RunOrigin.INTEGRATION_SYNC,
        "trigger": None,
        "parent_step_run": None,
        "dedup_key": capability.dedup_key,
        "occurrence_id": capability.occurrence_id,
        "input": JsonPresence(True, envelope),
        "retained_run": None,
        "exact_version": True,
    }

    with pytest.raises(ValidationError, match="exact Bridge launch capability"):
        workflow.validate_run_launch(**facts)
    with integration_sync_launch_capability(capability):
        workflow.validate_run_launch(**facts)

    assert bridge.admitted[0] is capability
    assert bridge.admitted[1]["workflow"] is workflow
    assert workflow.super_launch_facts == facts


def test_sync_workflow_rejects_same_pk_from_another_bridge_model() -> None:
    bridge = CapabilityBridge(id=3)
    capability = _capability(bridge, {"schema_version": 1})
    workflow = _workflow(pk=11, purpose=WorkflowPurpose.INTEGRATION_SYNC)
    with integration_sync_launch_capability(capability), pytest.raises(
        ValidationError, match="bound Bridge subject"
    ):
        workflow.validate_run_launch(
            subject=OtherCapabilityBridge(id=3),
            actor=type("Actor", (), {"pk": 7})(),
            origin=RunOrigin.INTEGRATION_SYNC,
            trigger=None,
            parent_step_run=None,
            dedup_key=capability.dedup_key,
            occurrence_id=capability.occurrence_id,
            input=JsonPresence(True, capability.envelope),
            retained_run=None,
            exact_version=True,
        )


def test_workflow_sync_dispatch_fails_closed_off_default_database() -> None:
    bridge = CapabilityBridge(id=5)
    bridge._state.db = "replica"
    with pytest.raises(ValidationError, match="default database"):
        IntegrationWorkflowBridge.dispatch_workflow_sync(
            bridge,
            now=datetime(2026, 9, 12, 12, tzinfo=UTC),
            occurrence=_occurrence(),
        )


def test_sync_workflow_forbids_error_workflow_even_with_capability() -> None:
    bridge = CapabilityBridge(id=3)
    capability = _capability(bridge, {"schema_version": 1})
    workflow = _workflow(pk=11, purpose=WorkflowPurpose.INTEGRATION_SYNC)
    workflow.error_workflow_id = 99
    with integration_sync_launch_capability(capability), pytest.raises(
        ValidationError, match="exact Bridge launch capability"
    ):
        workflow.validate_run_launch(
            subject=bridge,
            actor=type("Actor", (), {"pk": 7})(),
            origin=RunOrigin.INTEGRATION_SYNC,
            trigger=None,
            parent_step_run=None,
            dedup_key=capability.dedup_key,
            occurrence_id=capability.occurrence_id,
            input=JsonPresence(True, capability.envelope),
            retained_run=None,
            exact_version=True,
        )


def test_terminal_donor_affects_only_explicit_sync_runs() -> None:
    bridge = CapabilityBridge(id=5)
    workflow = type("Workflow", (), {"purpose": WorkflowPurpose.INTEGRATION_SYNC})()
    run = _run(
        workflow=workflow,
        origin=RunOrigin.INTEGRATION_SYNC,
        status=RunStatus.SUCCEEDED,
        subject=bridge,
    )
    at = datetime(2026, 9, 12, 12, tzinfo=UTC)

    run.deliver_terminal_effect(at=at)

    assert run.super_terminal_at == at
    assert bridge.delivered == (run, at)


def test_terminal_outcome_rejects_ambiguous_or_invalid_counts() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        IntegrationSyncTerminalOutcome()
    with pytest.raises(ValueError, match="non-negative"):
        IntegrationSyncTerminalOutcome(items=-1)
