"""Complete Decision operations retain settlement, delegation and delivery together."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.signals import pre_save
from django.utils import timezone
from rebac import system_context, to_subject_ref
from rebac.models import active_relationship_model

from angee.workflows import managers
from angee.workflows.attempts import (
    DecisionRecordAccess,
    DecisionSpec,
    DecisionSubmission,
    RecoveryCapability,
    RecoveryMode,
)
from angee.workflows.managers import DecisionQuerySet
from angee.workflows.states import StepRunStatus, Verdict
from angee.workflows.steps import GateStep, StepExecutionMode, StepResult
from tests import test_workflows_gates as gate_tests
from tests.conftest import create_platform_admin
from tests.messaging_models import Party
from tests.test_workflows_gates import (
    _decision_for,
    _decisions_for,
    _gate_config,
    _open_gate_run,
)
from tests.workflows import (
    Decision,
    FixtureStep,
    StepRun,
    WorkflowDispatch,
    WorkflowRun,
    advance_once,
    execute_started,
    run_to_terminal,
    start_run,
    workflow_with_steps,
)

User = get_user_model()
workflow_gate_record_access_tables = gate_tests.workflow_gate_record_access_tables


@pytest.fixture(autouse=True)
def quiet_decision_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(managers, "enqueue_dispatch_publisher", lambda **kwargs: None)


def test_decide_settlement_grants_and_dispatch_commit_together(
    workflow_gate_record_access_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_gate_record_access_tables, no_workflow_queue
    owner = create_platform_admin("decision-atomic-owner")
    resolver = User.objects.create_user(username="decision-atomic-resolver")
    with system_context(reason="decision atomic target fixture"):
        record = Party.objects.create(display_name="Pending review", created_by=owner)

    def suspend(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(
            decisions=(
                DecisionSpec(
                    action="review",
                    assignees=(str(to_subject_ref(resolver)),),
                    record_access=(DecisionRecordAccess(model=record._meta.label, id=str(record.sqid)),),
                ),
            ),
        )

    monkeypatch.setattr(FixtureStep, "run", suspend)
    workflow = workflow_with_steps(
        name="Atomic decision",
        steps=({"key": "gate", "step_class": "fixture", "config": {}},),
        edges=(),
    )
    run = start_run(workflow, actor=owner)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "gate")
    relationships = active_relationship_model()
    grant = {"relation": "pending_decision", "subject_id": str(decision.pk)}
    dispatch_manager = type(WorkflowDispatch.objects)
    schedule = dispatch_manager.schedule_advance
    with system_context(reason="decision atomic before"):
        dispatch_count = WorkflowDispatch.objects.count()
        assert relationships.objects.filter(**grant).exists()

    def fail_delivery(self: Any, retained_run: Any, **kwargs: Any) -> Any:
        assert StepRun.objects.get(pk=decision.step_run_id).status == StepRunStatus.SUCCEEDED
        decision.suspension_attempt.refresh_from_db()
        assert decision.suspension_attempt.decision_settlement["decision_ids"] == [decision.pk]
        assert not relationships.objects.filter(**grant).exists()
        schedule(self, retained_run, **kwargs)
        raise RuntimeError("delivery persistence failed")

    monkeypatch.setattr(dispatch_manager, "schedule_advance", fail_delivery)
    with pytest.raises(RuntimeError, match="delivery persistence failed"):
        Decision.objects.decide(
            decision.pk,
            actor=resolver,
            resolution=DecisionSubmission(Verdict.COMPLETED, {}),
        )
    with system_context(reason="decision atomic rollback"):
        decision.refresh_from_db()
        decision.suspension_attempt.refresh_from_db()
        assert decision.verdict == Verdict.PENDING
        assert decision.suspension_attempt.decision_settlement == {}
        assert StepRun.objects.get(pk=decision.step_run_id).status == StepRunStatus.WAITING
        assert relationships.objects.filter(**grant).exists()
        assert WorkflowDispatch.objects.count() == dispatch_count

    monkeypatch.setattr(dispatch_manager, "schedule_advance", schedule)
    result = Decision.objects.decide(
        decision.pk,
        actor=resolver,
        resolution=DecisionSubmission(Verdict.COMPLETED, {}),
    )
    assert result.validation_error is None
    assert not result.decision.is_sudo()
    assert result.decision.actor() == to_subject_ref(resolver)
    assert result.decision.step_run.actor() == to_subject_ref(resolver)
    assert result.decision.suspension_attempt.actor() == to_subject_ref(resolver)
    with system_context(reason="decision atomic committed"):
        assert StepRun.objects.get(pk=decision.step_run_id).status == StepRunStatus.SUCCEEDED
        assert not relationships.objects.filter(**grant).exists()
        assert WorkflowDispatch.objects.count() > dispatch_count


@pytest.mark.parametrize("verdict,action", [(Verdict.COMPLETED, "complete"), (Verdict.REJECTED, "reject")])
def test_manager_decide_accepts_positive_and_negative_collection_results(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    verdict: Verdict,
    action: str,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    actors = [User.objects.create_user(username=f"decision-{action}-{index}") for index in range(2)]
    workflow = workflow_with_steps(
        name="Collection decision",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config(actors, None, [], policy="all_done"),
            },
        ),
        edges=(),
    )
    run = _open_gate_run(workflow)
    decisions = _decisions_for(run, "gate")
    for decision, actor in zip(decisions, actors, strict=True):
        result = Decision.objects.decide(
            decision.pk,
            actor=actor,
            resolution=DecisionSubmission(verdict, {"action": action}),
        )
        assert result.validation_error is None
    with system_context(reason="decision collection result"):
        gate = StepRun.objects.get(pk=decisions[0].step_run_id)
        assert gate.status == StepRunStatus.SUCCEEDED
        assert [row["verdict"] for row in gate.output["resolutions"]] == [verdict, verdict]


def test_manager_decide_checks_actor_inside_system_scope(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    reviewer = User.objects.create_user(username="decision-pinned-reviewer")
    stranger = User.objects.create_user(username="decision-pinned-stranger")
    workflow = workflow_with_steps(
        name="Pinned decision",
        steps=({"key": "gate", "step_class": "gate", "config": _gate_config([reviewer], None, [])},),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    with system_context(reason="enclosing engine persistence"), pytest.raises(PermissionDenied):
        Decision.objects.decide(
            decision.pk,
            actor=stranger,
            resolution=DecisionSubmission(Verdict.COMPLETED, {"action": "complete"}),
        )


def test_decision_conditional_update_requires_one_row_and_reentry_sees_terminal(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    actor = User.objects.create_user(username="decision-rowcount-reviewer")
    workflow = workflow_with_steps(
        name="Decision rowcount",
        steps=({"key": "gate", "step_class": "gate", "config": _gate_config([actor], None, [])},),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    submit = DecisionSubmission(Verdict.COMPLETED, {"action": "complete"})
    conditional_update = DecisionQuerySet.resolve_pending
    monkeypatch.setattr(DecisionQuerySet, "resolve_pending", lambda *args, **kwargs: 0)
    with pytest.raises(ValidationError, match="no longer pending"):
        Decision.objects.decide(decision.pk, actor=actor, resolution=submit)
    monkeypatch.setattr(DecisionQuerySet, "resolve_pending", conditional_update)
    observed = []

    def reenter(sender: Any, instance: Any, **kwargs: Any) -> None:
        del sender, kwargs
        if instance.pk != decision.pk:
            return
        repeated = Decision.objects.decide(decision.pk, actor=actor, resolution=submit)
        observed.append(repeated.decision.verdict)
        with pytest.raises(TypeError, match="DecisionManager"):
            instance.save(update_fields=["resolution"])

    pre_save.connect(reenter, sender=Decision, weak=False)
    try:
        Decision.objects.decide(decision.pk, actor=actor, resolution=submit)
    finally:
        pre_save.disconnect(reenter, sender=Decision)
    assert observed == [Verdict.COMPLETED]


def test_invalid_decision_generation_rejects_a_stale_counter(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    actor = User.objects.create_user(username="decision-generation-reviewer")
    workflow = workflow_with_steps(
        name="Decision invalid generation",
        steps=({"key": "gate", "step_class": "gate", "config": _gate_config([actor], None, [])},),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    with system_context(reason="decision generation operations"):
        stale = Decision.objects.get(pk=decision.pk)
        assert decision.record_invalid_resolution()
        assert not stale.record_invalid_resolution()
        assert decision.record_invalid_resolution()
        decision.refresh_from_db()
        assert decision.attempts == 2
    exhausted = Decision.objects.decide(
        decision.pk,
        actor=actor,
        resolution=DecisionSubmission(Verdict.COMPLETED, {"action": "unsupported"}),
    )
    assert exhausted.validation_error is not None
    assert exhausted.decision.attempts == 3
    assert exhausted.decision.verdict == Verdict.EXPIRED


@pytest.mark.parametrize("verdict", [Verdict.EXPIRED, Verdict.ESCALATED])
def test_timed_decision_uses_deadline_and_generation_without_human_actor(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    verdict: Verdict,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    actor = User.objects.create_user(username="decision-expiry-reviewer")
    deadline = timezone.now() + timedelta(hours=1)
    workflow = workflow_with_steps(
        name="Timed decision",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config([actor], None, [], expires_at=deadline, escalate_at=deadline),
            },
        ),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    assert not Decision.objects.resolve_timed(
        decision.pk, generation=0, verdict=verdict, at=deadline - timedelta(seconds=1)
    )
    assert not Decision.objects.resolve_timed(decision.pk, generation=1, verdict=verdict, at=deadline)
    assert Decision.objects.resolve_timed(decision.pk, generation=0, verdict=verdict, at=deadline)
    assert not Decision.objects.resolve_timed(decision.pk, generation=0, verdict=verdict, at=deadline)
    with Decision.objects.locked_resolution(decision.pk, actor=None, expected_verdict=verdict) as expired:
        assert expired.verdict == verdict
        assert expired.resolved_by == ""
        assert expired.resolution_actor_subject() is None


def test_locked_resolution_rejects_wrong_resolver_and_unrelated_gate_for_map_consumer(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    reviewer = User.objects.create_user(username="decision-map-reviewer")
    stranger = User.objects.create_user(username="decision-map-stranger")
    workflow = workflow_with_steps(
        name="Mapped Decision application",
        steps=(
            {"key": "gate", "step_class": "gate", "config": _gate_config([reviewer], None, [])},
            {"key": "prepare", "step_class": "fixture", "config": {}},
            {"key": "map", "step_class": "map", "config": {"target_step": "body", "items": "input"}},
            {"key": "body", "step_class": "fixture", "config": {}},
        ),
        edges=(("gate", "prepare", "completed"), ("prepare", "map", "prepared")),
    )
    unrelated = workflow_with_steps(
        name="Unrelated Decision",
        steps=({"key": "gate", "step_class": "gate", "config": _gate_config([reviewer], None, [])},),
        edges=(),
    )
    run = _open_gate_run(workflow)
    decision = _decision_for(run, "gate")
    other = _decision_for(_open_gate_run(unrelated), "gate")
    for selected in (decision, other):
        Decision.objects.decide(
            selected.pk,
            actor=reviewer,
            resolution=DecisionSubmission(Verdict.COMPLETED, {"action": "complete"}),
        )
    with pytest.raises(PermissionDenied, match="resolving actor"):
        with Decision.objects.locked_resolution(decision.pk, actor=stranger):
            pytest.fail("Wrong resolver entered the application transaction.")
    applied: list[int] = []

    def apply(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        if step_run.step.key == "prepare":
            return StepResult.done([{"item": 1}], outcome="prepared")
        with pytest.raises(ValidationError, match="predecessor consumer"):
            with Decision.objects.locked_resolution(
                other.pk,
                actor=reviewer,
                consumer_step_run_id=step_run.pk,
            ):
                pytest.fail("Unrelated gate entered the application transaction.")
        with Decision.objects.locked_resolution(
            decision.pk,
            actor=reviewer,
            consumer_step_run_id=step_run.pk,
        ) as retained:
            applied.append(retained.pk)
        return StepResult.done(outcome="done")

    monkeypatch.setattr(FixtureStep, "run", apply)
    run_to_terminal(run)
    assert applied == [decision.pk]


def test_locked_resolution_follows_fresh_recovery_evidence_to_original_gate(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    reviewer = create_platform_admin("decision-recovery-reviewer")
    workflow = workflow_with_steps(
        name="Recover Decision application",
        steps=(
            {"key": "gate", "step_class": "gate", "config": _gate_config([reviewer], None, [])},
            {"key": "apply", "step_class": "fixture", "config": {}},
        ),
        edges=(("gate", "apply", "completed"),),
    )
    run = start_run(workflow, actor=reviewer)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "gate")
    Decision.objects.decide(
        decision.pk,
        actor=reviewer,
        resolution=DecisionSubmission(Verdict.COMPLETED, {"action": "complete"}),
    )
    applied: list[int] = []

    def apply(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        if step_run.run.origin != "recovery":
            raise RuntimeError("Retry this application from retained evidence.")
        selected = Decision.objects.predecessor_decision(step_run, GateStep)
        assert selected.pk == decision.pk
        with Decision.objects.locked_resolution(
            selected.pk,
            actor=reviewer,
            consumer_step_run_id=step_run.pk,
        ) as retained:
            applied.append(retained.pk)
        return StepResult.done(outcome="done")

    monkeypatch.setattr(FixtureStep, "run", apply)
    monkeypatch.setattr(FixtureStep, "execution_mode", StepExecutionMode.DATABASE_COMMAND)
    monkeypatch.setattr(
        FixtureStep,
        "recovery_capability",
        classmethod(lambda cls, *, attempt: RecoveryCapability(RecoveryMode.FRESH)),
    )
    run_to_terminal(run, allow_failed={run.pk})
    with system_context(reason="decision recovery source"):
        source = StepRun.objects.get(run=run, step__key="apply").current_attempt
    assert source.error == "Retry this application from retained evidence."
    recovery = WorkflowRun.objects.start_recovery(source, request_key="decision-apply-retry", actor=reviewer)
    run_to_terminal(recovery)
    assert applied == [decision.pk]
