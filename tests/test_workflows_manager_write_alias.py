"""Write-manager regressions retain the selected connection after admission."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import router
from django.utils import timezone
from rebac import system_context

from angee.workflows import managers
from angee.workflows.attempts import AttemptInput, AttemptResult, AttemptResultKind, JsonPresence, WorkflowScope
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.states import RunOrigin, StepRunStatus
from tests.test_workflow_test_snapshots import _draft, _ReconcilingTestStep
from tests.test_workflows_engine import _WriteSplitRouter
from tests.test_workflows_gates import _decision_for, _gate_config, _open_gate_run
from tests.test_workflows_triggers import _schedule_trigger
from tests.workflows import (
    Decision,
    Step,
    StepAttempt,
    StepRun,
    Trigger,
    Workflow,
    WorkflowDispatch,
    WorkflowRun,
    admit_workflow_actor,
    workflow_with_steps,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def quiet_manager_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep committed publication callbacks independent of transport availability."""

    monkeypatch.setattr(managers, "enqueue_dispatch_publisher", lambda **kwargs: None)


@pytest.mark.parametrize("bound", [False, True])
def test_start_keeps_anchor_or_bound_alias_through_initial_step_and_dispatch(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    bound: bool,
) -> None:
    """Publication lookup, owner creation, entry StepRun and ADVANCE share one alias."""

    actor = get_user_model().objects.create_user(username=f"start-alias-{bound}")
    workflow = workflow_with_steps(
        actor=actor,
        steps=({"key": "entry", "step_class": "fixture", "config": {}},),
        edges=(),
    )
    admit_workflow_actor(workflow, actor)
    owner = WorkflowRun.objects.db_manager("default") if bound else WorkflowRun.objects
    if bound:
        workflow._state.db = "wrong-anchor"
    with monkeypatch.context() as patch:
        patch.setattr(router, "routers", [_WriteSplitRouter()])
        run = owner.start(workflow, subject=None, actor=actor, input=JsonPresence(True, {"frozen": True}))

    with system_context(reason="manager alias start assertions"):
        persisted = WorkflowRun.objects.using("default").get(pk=run.pk)
        step_run = StepRun.objects.using("default").get(run_id=run.pk)
        dispatch = WorkflowDispatch.objects.using("default").get(run_id=run.pk)
    assert persisted.workflow_id == workflow.pk
    assert persisted.input == {"frozen": True}
    assert step_run.status == StepRunStatus.SCHEDULED
    assert dispatch.kind == WorkflowDispatchKind.ADVANCE
    assert run._state.db == "default"


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("node_scope", [False, True])
def test_start_test_keeps_alias_through_snapshot_lookup_and_duplicate_request(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    bound: bool,
    node_scope: bool,
) -> None:
    """An existing immutable snapshot isolates admission from native full_clean routing."""

    actor = get_user_model().objects.create_user(username=f"start-test-alias-{bound}")
    workflow, step = _draft(owner=actor)
    admit_workflow_actor(workflow, actor)
    snapshot = Workflow.objects.test_snapshot(workflow, expected_revision=workflow.draft_revision)
    owner = WorkflowRun.objects.db_manager("default") if bound else WorkflowRun.objects
    if bound:
        workflow._state.db = "wrong-anchor"
    with monkeypatch.context() as patch:
        patch.setattr(router, "routers", [_WriteSplitRouter()])
        run = owner.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="same-snapshot",
            subject=None,
            actor=actor,
            scope=WorkflowScope.NODE if node_scope else WorkflowScope.WHOLE,
            selected_step=step if node_scope else None,
        )
        repeated = owner.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="same-snapshot",
            subject=None,
            actor=actor,
            scope=WorkflowScope.NODE if node_scope else WorkflowScope.WHOLE,
            selected_step=step if node_scope else None,
        )

    assert run.pk == repeated.pk
    assert run.workflow_id == snapshot.pk
    assert run.origin == RunOrigin.TEST
    with system_context(reason="manager alias test launch assertions"):
        assert StepRun.objects.using("default").filter(run_id=run.pk).count() == 1
        assert WorkflowDispatch.objects.using("default").filter(run_id=run.pk).count() == 1


def test_setup_plan_keeps_snapshot_node_and_draft_lookup_on_selected_alias(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot-node setup resolves both Step identities through valid alias-bound queries."""

    actor = get_user_model().objects.create_user(username="snapshot-node-plan-alias")
    workflow, source_step = _draft(owner=actor)
    admit_workflow_actor(workflow, actor)
    snapshot = Workflow.objects.test_snapshot(workflow, expected_revision=workflow.draft_revision)
    with system_context(reason="snapshot node fixture"):
        snapshot_step = Step.objects.using("default").get(workflow_id=snapshot.pk, key=source_step.key)
    with monkeypatch.context() as patch:
        patch.setattr(router, "routers", [_WriteSplitRouter()])
        plan = WorkflowRun.objects.db_manager("default").test_setup_plan(
            snapshot,
            expected_revision=snapshot.draft_revision,
            actor=actor,
            scope=WorkflowScope.NODE,
            selected_step=snapshot_step,
        )

    assert plan.source_step_id == source_step.sqid
    assert plan.snapshot_step_id == snapshot_step.sqid


@pytest.mark.parametrize("bound", [False, True])
def test_start_recovery_keeps_alias_through_authorization_graph_and_durable_start(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    bound: bool,
) -> None:
    """Recovery must continue past source admission to persist its new execution rows."""

    actor = get_user_model().objects.create_user(username=f"start-recovery-alias-{bound}")
    workflow, step = _draft(owner=actor)
    admit_workflow_actor(workflow, actor)
    with system_context(reason="manager recovery source fixture"):
        source_run = WorkflowRun.objects.create(workflow=workflow, status="running", created_by=actor)
        source_step = StepRun.objects.create(run=source_run, step=step, status=StepRunStatus.SCHEDULED)
    source = StepAttempt.objects.claim(source_step, input=AttemptInput(), claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(source.pk, lease_token=source.lease_token, at=timezone.now())
    StepAttempt.objects.finalize(
        source.pk,
        lease_token=source.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="retained failure"),
        recorded_at=timezone.now(),
    )
    monkeypatch.setattr(Step, "resolve_impl", lambda self, field: _ReconcilingTestStep)
    owner = WorkflowRun.objects.db_manager("default") if bound else WorkflowRun.objects
    if bound:
        source._state.db = "wrong-anchor"
    with monkeypatch.context() as patch:
        patch.setattr(router, "routers", [_WriteSplitRouter()])
        recovery = owner.start_recovery(source, request_key="retry", actor=actor)
        repeated = owner.start_recovery(source, request_key="retry", actor=actor)

    assert recovery.pk == repeated.pk
    assert recovery.recovery_source_attempt_id == source.pk
    with system_context(reason="manager recovery alias assertions"):
        recovered_step = StepRun.objects.using("default").get(run_id=recovery.pk)
        dispatch = WorkflowDispatch.objects.using("default").get(run_id=recovery.pk)
    assert recovered_step.step_id == step.pk
    assert recovered_step.status == StepRunStatus.SCHEDULED
    assert dispatch.kind == WorkflowDispatchKind.ADVANCE


def test_schedule_maintenance_keeps_alias_through_priming_claim_and_start(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both legacy maintenance and durable schedule admission finish on the writer."""

    now = timezone.now().replace(microsecond=0)
    primed = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=None)
    claimed = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=now)
    started = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=now)
    with monkeypatch.context() as patch:
        patch.setattr(router, "routers", [_WriteSplitRouter()])
        owner = Trigger.objects.db_manager("default")
        assert owner.prime_due_schedules(timestamp=now) == 1
        assert owner.claim_due_schedule(claimed.pk, timestamp=now) is not None
        admitted = owner.start_due_schedule(started.pk, timestamp=now)

    assert admitted is not None
    run, due_at = admitted
    assert due_at == now
    with system_context(reason="manager schedule alias assertions"):
        for trigger in (primed, claimed, started):
            trigger.refresh_from_db(using="default")
            assert trigger.next_fire_at == now + timedelta(seconds=60)
        assert WorkflowDispatch.objects.using("default").filter(run_id=run.pk).exists()
    assert claimed.hourly_fire_count == 1
    assert started.hourly_fire_count == 1


def test_raw_delete_rejects_actor_hidden_retained_decision_under_split_router(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw SQL cannot evade retention through a hidden actor scope or read replica."""

    assignee = get_user_model().objects.create_user(username="raw-delete-assignee")
    stranger = get_user_model().objects.create_user(username="raw-delete-hidden-stranger")
    workflow = workflow_with_steps(
        steps=({"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    hidden = Decision.objects.with_actor(stranger).using("default").filter(pk=decision.pk).scoped()
    assert not hidden.exists()
    # The explicit raw-delete alias must also replace the pre-existing binding.
    hidden = hidden.using("wrong-binding")
    with monkeypatch.context() as patch:
        patch.setattr(router, "routers", [_WriteSplitRouter()])
        with pytest.raises(TypeError, match="Retained workflow Decisions"):
            hidden._raw_delete(using="default")

    with system_context(reason="actor-hidden raw-delete retention assertion"):
        retained = Decision.objects.using("default").get(pk=decision.pk)
    assert retained.suspension_attempt_id is not None
    assert retained.declaration_index is not None
