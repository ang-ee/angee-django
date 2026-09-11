"""Tests for workflow decision gates and resolution paths."""

from __future__ import annotations

import importlib
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import PermissionDenied, app_settings, system_context, to_subject_ref
from rebac.models import active_relationship_model
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.attempts import AttemptResultKind
from angee.workflows.steps import DecisionSpec, HandlerStep, StepResult
from tests.conftest import SchemaAddon, execute_schema, result_data
from tests.workflows import (
    Decision,
    StepRun,
    Workflow,
    WorkflowRun,
    advance_once,
    execute_started,
    start_run,
    step_for,
    workflow_with_steps,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def executable_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep legacy handler fixtures executable while tests replace behavior as needed."""

    def run(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        return StepResult.done(outcome=str(step_run.step.config.get("outcome", "done")))

    monkeypatch.setattr(HandlerStep, "run", run)


def test_suspend_result_creates_decision_rows_and_relationship_tuples(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine-owned suspend API persists slots and writes explicit REBAC tuples."""

    del workflow_gate_tables, no_workflow_queue
    requester = User.objects.create_user(username="wdc-requester")
    assignee = User.objects.create_user(username="wdc-assignee")
    escalated = User.objects.create_user(username="wdc-escalated")

    def suspend_from_handler(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(
            resume_state={"phase": "awaiting-review"},
            decisions=[
                DecisionSpec(
                    assignees=(str(to_subject_ref(assignee)),),
                    requester=str(to_subject_ref(requester)),
                    escalation=(str(to_subject_ref(escalated)),),
                    action="complete-review",
                    payload={"title": "Review"},
                    max_attempts=3,
                )
            ],
        )

    monkeypatch.setattr(HandlerStep, "run", suspend_from_handler)
    workflow = workflow_with_steps(
        name="Gate workflow",
        steps=({"key": "handler", "step_class": "handler", "config": {}},),
        edges=(),
    )

    run = start_run(workflow)
    advance_once(run)
    execute_started(run)

    decision = _decision_for(run, "handler")
    assert decision.priority == 0
    assert decision.action == "complete-review"
    assert decision.payload == {"title": "Review"}
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.max_attempts == 3

    assert _relationship_subjects(decision, "assignee") == {str(to_subject_ref(assignee))}
    assert _relationship_subjects(decision, "requester") == {str(to_subject_ref(requester))}
    assert _relationship_subjects(decision, "escalation") == {str(to_subject_ref(escalated))}


def test_decision_target_is_actor_validated_retained_and_immutable(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Related-record identity survives attempt replay without granting target access."""

    del workflow_gate_tables, no_workflow_queue
    admin = _platform_admin("wdc-target-admin")
    assignee = User.objects.create_user(username="wdc-target-assignee")
    stranger = User.objects.create_user(username="wdc-target-stranger")
    target = workflow_with_steps(
        name="Decision target",
        steps=({"key": "target", "step_class": "handler", "config": {}},),
        edges=(),
    )
    declaration = DecisionSpec(
        assignees=(str(to_subject_ref(assignee)),),
        action="review-target",
        target_model=target._meta.label,
        target_id=str(target.sqid),
        target_tab="details",
    )

    with pytest.raises(ValidationError, match="not found"):
        Decision.objects._validated_target(declaration, actor=stranger)

    def suspend_from_handler(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(decisions=(declaration,))

    monkeypatch.setattr(HandlerStep, "run", suspend_from_handler)
    gate = workflow_with_steps(
        name="Targeted decision",
        steps=({"key": "handler", "step_class": "handler", "config": {}},),
        edges=(),
    )
    run = engine.start(gate, None, actor=admin)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "handler")

    assert (decision.target_model, decision.target_id, decision.target_tab) == (
        target._meta.label, str(target.sqid), "details",
    )
    with system_context(reason="test retained decision target attempt"):
        retained = decision.suspension_attempt.result_decisions[0]
    assert (retained["target_model"], retained["target_id"], retained["target_tab"]) == (
        target._meta.label, str(target.sqid), "details",
    )
    decision.target_id = "wfl_tampered"
    with pytest.raises(TypeError, match="immutable"):
        decision.save(update_fields=("target_id",))


def test_decision_act_blocks_requester_and_non_assignee_but_allows_non_requester_admin(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Separation of duty is parenthesized: requester is blocked, admin still wins."""

    del workflow_gate_tables, no_workflow_queue
    requester = User.objects.create_user(username="wdc-sod-requester")
    stranger = User.objects.create_user(username="wdc-sod-stranger")
    admin = _platform_admin("wdc-sod-admin")
    decision = _opened_decision([requester], requester)

    with pytest.raises(PermissionDenied):
        engine.decide(decision, "complete", actor=requester)
    with pytest.raises(PermissionDenied):
        engine.decide(decision, "complete", actor=stranger)

    engine.decide(decision, "complete", actor=admin)

    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.COMPLETED


@pytest.mark.parametrize(
    ("policy", "verdicts", "expected_outcome"),
    [
        ("one_done", ("reject",), "rejected"),
        ("all_success", ("complete", "complete", "complete"), "completed"),
        ("majority", ("complete", "reject", "complete"), "completed"),
    ],
)
def test_gate_policy_aggregates_resolutions_and_routes(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    policy: str,
    verdicts: tuple[str, ...],
    expected_outcome: str,
) -> None:
    """Gate policies aggregate pending decision slots into a step outcome."""

    del workflow_gate_tables, no_workflow_queue
    assignees = [User.objects.create_user(username=f"wdc-{policy}-{index}") for index in range(3)]
    workflow = _workflow_with_gate_routes(policy=policy, assignees=assignees)
    run = _open_gate_run(workflow)

    for decision, verb in zip(_decisions_for(run, "gate"), verdicts, strict=False):
        engine.decide(decision, verb, actor=_user_for_subject(decision, "assignee"))

    gate = _step_run(run, "gate")
    assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
    assert gate.outcome == expected_outcome

    advance_once(run)
    routed = _step_run(run, expected_outcome)
    assert routed.status == workflow_models.StepRunStatus.STARTED


def test_legacy_gate_decision_still_marks_the_suspended_step_succeeded(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Decision scoping preserves the legacy gate's terminal journal behavior."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-legacy-assignee")
    run = _open_gate_run(_workflow_with_gate_routes(policy="one_done", assignees=[assignee]))
    gate = _step_run(run, "gate")
    decision = _decision_for(run, "gate")

    assert gate.resume_state["_decision_ids"] == [decision.pk]
    engine.decide(decision, "complete", actor=assignee)

    gate.refresh_from_db()
    assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
    assert gate.outcome == "completed"


def test_settled_retained_decision_output_feeds_downstream_binding(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settled suspension exposes its decision envelope without rewriting its attempt."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-bound-decision")
    pending_assignee = User.objects.create_user(username="wdc-bound-decision-pending")

    def gate_then_consume(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        if step_run.step.key == "gate":
            return StepResult.suspend(
                resume_state={"gate": {"policy": "one_done"}},
                decisions=(
                    DecisionSpec(
                        assignees=(str(to_subject_ref(assignee)),),
                        action="approve-bound-output",
                    ),
                    DecisionSpec(
                        assignees=(str(to_subject_ref(pending_assignee)),),
                        action="approve-bound-output",
                        priority=1,
                    ),
                ),
            )
        return StepResult.done(output=step_run.input)

    monkeypatch.setattr(HandlerStep, "run", gate_then_consume)
    workflow = workflow_with_steps(
        name="Bound retained decision output",
        steps=(
            {"key": "gate", "step_class": "handler", "config": {}},
            {
                "key": "consumer",
                "step_class": "handler",
                "config": {},
                "input_binding": {"kind": "step_output", "step_key": "gate", "path": []},
            },
        ),
        edges=(("gate", "consumer", "completed"),),
    )
    run = start_run(workflow)
    advance_once(run)
    execute_started(run)
    gate = _step_run(run, "gate")
    suspension = gate.current_attempt
    decisions = _decisions_for(run, "gate")
    decision, pending = decisions

    engine.decide(decision, "complete", actor=assignee)
    advance_once(run)
    execute_started(run)

    gate.refresh_from_db()
    consumer = _step_run(run, "consumer")
    suspension.refresh_from_db()
    pending.refresh_from_db()
    assert suspension.result_kind == str(AttemptResultKind.SUSPEND)
    assert pending.verdict == workflow_models.Verdict.PENDING
    assert consumer.status == workflow_models.StepRunStatus.SUCCEEDED
    assert consumer.output == {"decisions": [decision.sqid, pending.sqid]}
    assert consumer.current_attempt.input_provenance["settled_decision_ids"] == [
        decision.pk,
        pending.pk,
    ]


def test_force_expiry_wakes_retained_decision_continuation(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-force-expire")

    def suspend(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(
            resume_state={"_resume_after_decisions": True, "gate": {"policy": "all_done"}},
            decisions=(
                DecisionSpec(
                    assignees=(str(to_subject_ref(assignee)),),
                    action="approve-tool",
                ),
            ),
        )

    monkeypatch.setattr(HandlerStep, "run", suspend)
    workflow = workflow_with_steps(
        name="Force expiry continuation",
        steps=({"key": "handler", "step_class": "handler", "config": {}},),
        edges=(),
    )
    run = start_run(workflow)
    advance_once(run)
    execute_started(run)
    row = _step_run(run, "handler")
    prior_attempt_id = row.current_attempt_id

    assert engine.expire_pending_decisions(run, resolved_by="test/session-close") == 1

    row.refresh_from_db()
    decision = _decision_for(run, "handler")
    assert decision.verdict == workflow_models.Verdict.EXPIRED
    assert row.current_attempt_id == prior_attempt_id
    assert row.resume_state["_decision_outcome"] == "completed"
    assert row.wait_until is not None


def test_resume_after_decisions_scopes_each_single_and_multi_suspension(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed step considers only the decisions created by its current suspension."""

    del workflow_gate_tables, no_workflow_queue
    first = User.objects.create_user(username="wdc-resume-first")
    second = User.objects.create_user(username="wdc-resume-second")
    third = User.objects.create_user(username="wdc-resume-third")

    def suspend_each_attempt(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        assignees = [first] if step_run.attempt == 1 else [second, third]
        return StepResult.suspend(
            resume_state={"_resume_after_decisions": True, "gate": {"policy": "all_done"}},
            decisions=tuple(
                DecisionSpec(
                    assignees=(str(to_subject_ref(assignee)),),
                    action="approve-tool",
                    priority=index,
                )
                for index, assignee in enumerate(assignees)
            ),
        )

    monkeypatch.setattr(HandlerStep, "run", suspend_each_attempt)
    workflow = workflow_with_steps(
        name="Resumable decision workflow",
        steps=({"key": "handler", "step_class": "handler", "config": {}},),
        edges=(),
    )
    run = start_run(workflow)
    advance_once(run)
    execute_started(run)

    row = _step_run(run, "handler")
    first_decision = _decisions_for(run, "handler")[0]
    assert row.resume_state["_decision_ids"] == [first_decision.pk]

    engine.decide(first_decision, "complete", actor=first)
    row.refresh_from_db()
    assert row.status == workflow_models.StepRunStatus.WAITING
    assert row.resume_state["_decision_outcome"] == "completed"

    advance_once(run)
    execute_started(run)
    row.refresh_from_db()
    all_decisions = _decisions_for(run, "handler")
    current = all_decisions[1:]
    assert len(current) == 2
    assert row.resume_state["_decision_ids"] == [decision.pk for decision in current]
    assert first_decision.pk not in row.resume_state["_decision_ids"]

    engine.decide(current[0], "complete", actor=second)
    row.refresh_from_db()
    assert row.status == workflow_models.StepRunStatus.WAITING
    assert "_decision_outcome" not in row.resume_state

    engine.decide(current[1], "complete", actor=third)
    row.refresh_from_db()
    assert row.status == workflow_models.StepRunStatus.WAITING
    assert row.resume_state["_decision_outcome"] == "completed"


def test_sequential_policy_requires_priority_order(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Sequential gates resolve seats in ascending priority order."""

    del workflow_gate_tables, no_workflow_queue
    first = User.objects.create_user(username="wdc-seq-first")
    second = User.objects.create_user(username="wdc-seq-second")
    workflow = _workflow_with_gate_routes(
        policy="sequential",
        assignees=[first, second],
        priorities=[10, 20],
    )
    run = _open_gate_run(workflow)
    first_decision, second_decision = _decisions_for(run, "gate")

    with pytest.raises(ValidationError):
        engine.decide(second_decision, "complete", actor=second)

    engine.decide(first_decision, "complete", actor=first)
    first_decision.refresh_from_db()
    second_decision.refresh_from_db()
    assert first_decision.verdict == workflow_models.Verdict.COMPLETED
    assert second_decision.verdict == workflow_models.Verdict.PENDING
    assert _step_run(run, "gate").status == workflow_models.StepRunStatus.WAITING

    engine.decide(second_decision, "complete", actor=second)
    gate = _step_run(run, "gate")
    assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
    assert gate.outcome == "completed"


def test_invalid_resolution_reopens_then_fails_at_max_attempts(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Decision schema validation increments attempts and fails terminally at max."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-password-assignee")
    workflow = workflow_with_steps(
        name="Gate workflow",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config(
                    [assignee],
                    None,
                    [],
                    max_attempts=2,
                    decision_schema={
                        "type": "object",
                        "required": ["password"],
                        "properties": {"password": {"type": "string", "const": "open-sesame"}},
                    },
                ),
            },
        ),
        edges=(),
    )
    run = _open_gate_run(workflow)
    decision = _decision_for(run, "gate")

    engine.decide(decision, "complete", payload={"password": "wrong"}, actor=assignee)
    decision.refresh_from_db()
    gate = _step_run(run, "gate")
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 1
    assert gate.status == workflow_models.StepRunStatus.WAITING

    engine.decide(decision, "complete", payload={"password": "wrong-again"}, actor=assignee)
    decision.refresh_from_db()
    gate.refresh_from_db()
    assert decision.attempts == 2
    assert gate.status == workflow_models.StepRunStatus.FAILED
    assert "Decision resolution failed validation" in gate.error


def test_nested_decision_schema_validates_objects_and_array_rows_before_round_trip(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Nested object and row schemas validate recursively before resolution persists."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-nested-schema-assignee")
    decision_schema = {
        "type": "object",
        "required": ["review", "rows"],
        "properties": {
            "review": {
                "type": "object",
                "required": ["approved"],
                "properties": {"approved": {"type": "boolean"}},
            },
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["target", "mode"],
                    "properties": {
                        "target": {"type": "integer"},
                        "mode": {"enum": ["append", "replace"]},
                    },
                },
            },
        },
    }
    workflow = workflow_with_steps(
        name="Nested schema gate",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config([assignee], None, [], decision_schema=decision_schema),
            },
        ),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")

    engine.decide(
        decision,
        "complete",
        payload={"review": {"approved": "not-a-boolean"}, "rows": [{"target": "nope", "mode": "merge"}]},
        actor=assignee,
    )
    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 1

    resolution = {"review": {"approved": True}, "rows": [{"target": 7, "mode": "append"}]}
    engine.decide(decision, "complete", payload=resolution, actor=assignee)
    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.COMPLETED
    assert decision.resolution == resolution


def test_escalation_timeout_writes_tuple_and_routes_escalated(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Escalation timers are stale-attempt guarded resolutions."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-escalate-assignee")
    manager = User.objects.create_user(username="wdc-escalate-manager")
    now = timezone.now()
    workflow = _workflow_with_gate_routes(
        policy="one_done",
        assignees=[assignee],
        escalation=[manager],
        escalate_at=now + timedelta(minutes=5),
    )
    run = _open_gate_run(workflow, now=now)
    decision = _decision_for(run, "gate")

    engine.escalate_decision(decision.pk, decision.attempts + 1, now=now + timedelta(minutes=10))
    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.PENDING

    engine.escalate_decision(decision.pk, decision.attempts, now=now + timedelta(minutes=10))
    decision.refresh_from_db()
    gate = _step_run(run, "gate")
    assert decision.verdict == workflow_models.Verdict.ESCALATED
    assert _relationship_subjects(decision, "escalation") == {str(to_subject_ref(manager))}
    assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
    assert gate.outcome == "escalated"


def test_expiry_timeout_routes_expired(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Expiry timers resolve pending slots as expired."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-expire-assignee")
    now = timezone.now()
    workflow = _workflow_with_gate_routes(
        policy="one_done",
        assignees=[assignee],
        expires_at=now + timedelta(minutes=5),
    )
    run = _open_gate_run(workflow, now=now)
    decision = _decision_for(run, "gate")

    engine.expire_decision(decision.pk, decision.attempts, now=now + timedelta(minutes=10))

    decision.refresh_from_db()
    gate = _step_run(run, "gate")
    assert decision.verdict == workflow_models.Verdict.EXPIRED
    assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
    assert gate.outcome == "expired"


def test_decision_sweep_resolves_due_durable_timers(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """The periodic DB sweep resolves decision timers even if ETA tasks are lost."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-sweep-assignee")
    manager = User.objects.create_user(username="wdc-sweep-manager")
    now = timezone.now()
    escalate_workflow = _workflow_with_gate_routes(
        name="Escalate sweep",
        policy="one_done",
        assignees=[assignee],
        escalation=[manager],
        escalate_at=now - timedelta(minutes=5),
    )
    expire_workflow = _workflow_with_gate_routes(
        name="Expire sweep",
        policy="one_done",
        assignees=[assignee],
        expires_at=now - timedelta(minutes=5),
    )
    future_workflow = _workflow_with_gate_routes(
        name="Future sweep",
        policy="one_done",
        assignees=[assignee],
        expires_at=now + timedelta(minutes=5),
    )
    escalate_run = _open_gate_run(escalate_workflow, now=now)
    expire_run = _open_gate_run(expire_workflow, now=now)
    future_run = _open_gate_run(future_workflow, now=now)

    result = engine.sweep_decisions(now=now)

    assert result == {"expired": 1, "escalated": 1}
    assert _decision_for(escalate_run, "gate").verdict == workflow_models.Verdict.ESCALATED
    assert _decision_for(expire_run, "gate").verdict == workflow_models.Verdict.EXPIRED
    assert _decision_for(future_run, "gate").verdict == workflow_models.Verdict.PENDING


def test_override_run_cancels_active_steps_and_injects_synthetic_step_run(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Manual override records the actor-finished journal row and chosen next steps."""

    del workflow_gate_tables, no_workflow_queue
    admin = _platform_admin("wdc-override-admin")
    workflow = workflow_with_steps(
        name="Gate workflow",
        steps=(
            {"key": "active", "config": {"outcome": "done"}},
            {
                "key": "finish",
                "config": {"outcome": "done"},
                "is_entry": False,
            },
        ),
        edges=(("active", "finish", "unreachable"),),
    )
    active = step_for(workflow, "active")
    finish = step_for(workflow, "finish")
    with system_context(reason="test workflows override setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=workflow_models.RunStatus.RUNNING)
        active_row = StepRun.objects.create(run=run, step=active, status=workflow_models.StepRunStatus.STARTED)

    override = engine.override_run(run, [finish], actor=admin)

    active_row.refresh_from_db()
    assert active_row.status == workflow_models.StepRunStatus.CANCELED
    assert override.step_id is None
    assert override.system_kind == "override"
    assert override.status == workflow_models.StepRunStatus.SUCCEEDED
    assert override.created_by == admin
    scheduled = _step_run(run, "finish")
    assert scheduled.status == workflow_models.StepRunStatus.SCHEDULED
    with system_context(reason="test workflows override previous"):
        assert list(scheduled.previous.all()) == [override]


def test_public_schema_exposes_decision_resource_decide_mutation_and_subscription(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Decisions are public REBAC-scoped resources with a public decide mutation."""

    del workflow_gate_tables, no_workflow_queue
    schema = _schema("public")
    sdl = schema.as_str()

    assert "workflow_decisions" in sdl
    assert "decide(" in sdl
    assert "decisionChanged" in sdl
    assert "target_model" in sdl
    assert "target_id" in sdl

    workflows_schema = importlib.import_module("angee.workflows.schema")
    parts = {key: tuple(workflows_schema.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}
    metadata = GraphQLSchemas([SchemaAddon({"public": parts})]).render_metadata()["public"]["angee"]
    decision = next(
        item for item in metadata["resources"] if item["modelLabel"] == "workflows.Decision"
    )
    assert decision["query"]["fields"]["step_run.run"]["filter"]["field"] == "step_run__run"


def test_public_schema_decision_projection_excludes_step_run_journal(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Public decisions expose denormalized labels, not the console StepRun graph."""

    del workflow_gate_tables, no_workflow_queue
    sdl = _schema("public").as_str()

    assert "type DecisionType" in sdl
    decision_section = sdl.split("type DecisionType", 1)[1].split("type ", 1)[0]
    assert "step_run" not in decision_section
    assert "resume_state" not in decision_section
    assert "decision_schema" in decision_section
    assert "decisionSchema" not in decision_section
    assert "workflow_name" in decision_section
    assert "step_name" in decision_section
    assert "StepRunType" not in sdl


def test_decision_schema_is_exposed_narrowly_on_public_and_console_decisions(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Both projections expose the enforced JSON form schema, or null."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-schema-reader")
    admin = _platform_admin("wdc-schema-admin")
    decision_schema = {
        "type": "object",
        "required": ["approved"],
        "properties": {"approved": {"type": "boolean"}},
    }
    workflow = workflow_with_steps(
        name="Schema delivery gate",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config([assignee], None, [], decision_schema=decision_schema),
            },
        ),
        edges=(),
    )
    schema_decision = _decision_for(_open_gate_run(workflow), "gate")
    schema_less_decision = _opened_decision([assignee], None)
    invalid = engine.decide(schema_decision, "complete", payload={}, actor=assignee)
    assert invalid.validation_error is not None
    query = """
        query DecisionSchema($id: String!) {
          workflow_decisions_by_pk(id: $id) {
            decision_schema
          }
        }
    """

    public = _schema("public")
    public_schema = result_data(
        _execute(public, query, {"id": str(schema_decision.sqid)}, user=assignee)
    )
    public_schema_less = result_data(
        _execute(public, query, {"id": str(schema_less_decision.sqid)}, user=assignee)
    )
    console_schema = result_data(
        _execute(_schema("console"), query, {"id": str(schema_decision.sqid)}, user=admin)
    )
    console_schema_less = result_data(
        _execute(_schema("console"), query, {"id": str(schema_less_decision.sqid)}, user=admin)
    )

    assert public_schema["workflow_decisions_by_pk"]["decision_schema"] == decision_schema
    assert public_schema_less["workflow_decisions_by_pk"]["decision_schema"] is None
    assert console_schema["workflow_decisions_by_pk"]["decision_schema"] == decision_schema
    assert console_schema_less["workflow_decisions_by_pk"]["decision_schema"] is None


@pytest.mark.django_db(transaction=True)
def test_retained_decision_transition_requires_complete_owner(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-owner-guard")
    workflow = workflow_with_steps(
        name="Decision owner guard",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config([assignee], None, []),
            },
        ),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")

    with pytest.raises(RuntimeError, match="exact transition owner"):
        type(decision).objects.resolve_retained(
            decision.pk,
            verdict=workflow_models.Verdict.COMPLETED,
            resolution={},
            resolved_by="test",
            at=timezone.now(),
        )

    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.PENDING

    with pytest.raises(TypeError, match="transition owner"):
        decision.resolve(
            workflow_models.Verdict.COMPLETED,
            resolution={},
            resolved_by="bypass",
        )
    decision.refresh_from_db()
    with pytest.raises(TypeError, match="transition owner"):
        decision.record_invalid_resolution()
    decision.refresh_from_db()
    with pytest.raises(TypeError, match="DecisionManager"):
        type(decision).objects.filter(pk=decision.pk).update(
            verdict=workflow_models.Verdict.COMPLETED
        )
    decision.attempts += 1
    with pytest.raises(TypeError, match="DecisionManager"):
        type(decision).objects.bulk_update([decision], ["attempts"])

    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 0


def test_public_decision_schema_query_count_stays_flat_for_three_rows(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Decision form-schema projection carries its relation in the parent query."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-schema-query-reader")
    decision_schema = {
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
    }

    def open_decisions(count: int, name: str) -> None:
        workflow = workflow_with_steps(
            name=name,
            steps=(
                {
                    "key": "gate",
                    "step_class": "gate",
                    "config": _gate_config(
                        [assignee] * count,
                        None,
                        [],
                        decision_schema=decision_schema,
                    ),
                },
            ),
            edges=(),
        )
        _open_gate_run(workflow)

    public = _schema("public")
    query = """
        query DecisionSchemas {
          workflow_decisions(limit: 10, order_by: [{ created_at: asc }]) {
            decision_schema
            workflow_name
            step_name
          }
        }
    """
    open_decisions(1, "One schema query row")
    with CaptureQueriesContext(connection) as one_row:
        one_data = result_data(_execute(public, query, user=assignee))

    open_decisions(2, "Two more schema query rows")
    with CaptureQueriesContext(connection) as three_rows:
        three_data = result_data(_execute(public, query, user=assignee))

    assert len(one_data["workflow_decisions"]) == 1
    assert len(three_data["workflow_decisions"]) == 3
    assert {row["workflow_name"] for row in three_data["workflow_decisions"]} == {
        "One schema query row",
        "Two more schema query rows",
    }
    assert {row["step_name"] for row in three_data["workflow_decisions"]} == {"Gate"}
    assert len(three_rows.captured_queries) == len(one_row.captured_queries)
    assert "rebac_permissionauditevent" not in " ".join(
        query["sql"].lower() for query in three_rows.captured_queries
    )


def test_decision_resources_scope_all_read_shapes_and_guard_journal_links(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Decision reads stay assignee-scoped while journal links require their own read access."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-resource-assignee")
    stranger = User.objects.create_user(username="wdc-resource-stranger")
    admin = _platform_admin("wdc-resource-admin")
    decision = _opened_decision([assignee], None)
    query = """
        query DecisionReads($id: String!, $run: String!) {
          workflow_decisions(where: {step_run__run: {_eq: $run}}, limit: 10) {
            id
            source_run_id
            source_execution_id
            source_attempt_id
          }
          workflow_decisions_by_pk(id: $id) {
            id
            source_run_id
            source_execution_id
            source_attempt_id
          }
          workflow_decisions_aggregate { aggregate { count } }
        }
    """
    public = _schema("public")
    variables = {"id": str(decision.sqid), "run": str(decision.step_run.run.sqid)}

    assigned = result_data(_execute(public, query, variables, user=assignee))
    denied = result_data(_execute(public, query, variables, user=stranger))
    privileged = result_data(_execute(public, query, variables, user=admin))
    console_query = """
        query ConsoleDecision($id: String!) {
          workflow_decisions_by_pk(id: $id) { id step_run { id } }
        }
    """
    console = _schema("console")
    assigned_console = result_data(
        _execute(console, console_query, {"id": str(decision.sqid)}, user=assignee)
    )
    privileged_console = result_data(
        _execute(console, console_query, {"id": str(decision.sqid)}, user=admin)
    )

    assert assigned["workflow_decisions"] == [
        {
            "id": str(decision.sqid),
            "source_run_id": None,
            "source_execution_id": None,
            "source_attempt_id": None,
        }
    ]
    assert assigned["workflow_decisions_by_pk"] == assigned["workflow_decisions"][0]
    assert assigned["workflow_decisions_aggregate"]["aggregate"]["count"] == 1
    assert denied["workflow_decisions"] == []
    assert denied["workflow_decisions_by_pk"] is None
    assert denied["workflow_decisions_aggregate"]["aggregate"]["count"] == 0
    assert privileged["workflow_decisions_by_pk"]["source_run_id"] == str(
        decision.step_run.run.sqid
    )
    assert privileged["workflow_decisions_by_pk"]["source_execution_id"] == str(
        decision.step_run.sqid
    )
    assert privileged["workflow_decisions_by_pk"]["source_attempt_id"] == str(
        decision.suspension_attempt.sqid
    )
    assert assigned_console["workflow_decisions_by_pk"] == {
        "id": str(decision.sqid),
        "step_run": None,
    }
    assert privileged_console["workflow_decisions_by_pk"] == {
        "id": str(decision.sqid),
        "step_run": {"id": str(decision.step_run.sqid)},
    }


def test_public_decide_mutation_uses_actor_scoped_act_permission(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """The public mutation resolves as the session actor, not as system."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-gql-assignee")
    stranger = User.objects.create_user(username="wdc-gql-stranger")
    decision = _opened_decision([assignee], None)
    public = _schema("public")
    mutation = """
        mutation Decide($decision: ID!, $verdict: DecisionVerb!, $payload: JSON) {
          decide(decision: $decision, verdict: $verdict, payload: $payload) {
            decision {
              verdict
              resolution
            }
            validation_errors
          }
        }
    """

    variables = {"decision": str(decision.sqid), "verdict": "COMPLETE", "payload": {"ok": True}}
    denied = _execute(public, mutation, variables, user=stranger)
    assert denied.errors is not None

    data = result_data(_execute(public, mutation, variables, user=assignee))
    assert data["decide"] == {
        "decision": {"verdict": "COMPLETED", "resolution": {"ok": True}},
        "validation_errors": None,
    }


def test_public_decide_returns_dotted_field_errors_and_reopens_the_decision(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Invalid input returns field-keyed validation errors and preserves retry state."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-gql-validation-assignee")
    decision_schema = {
        "type": "object",
        "required": ["review", "rows"],
        "properties": {
            "review": {
                "type": "object",
                "required": ["approved"],
                "properties": {"approved": {"type": "boolean"}},
            },
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["target"],
                    "properties": {"target": {"type": "integer"}},
                },
            },
        },
    }
    workflow = workflow_with_steps(
        name="Validation payload gate",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config([assignee], None, [], decision_schema=decision_schema),
            },
        ),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    mutation = """
        mutation Decide($decision: ID!, $verdict: DecisionVerb!, $payload: JSON) {
          decide(decision: $decision, verdict: $verdict, payload: $payload) {
            decision {
              verdict
              attempts
            }
            validation_errors
          }
        }
    """
    variables = {
        "decision": str(decision.sqid),
        "verdict": "COMPLETE",
        "payload": {"review": {}, "rows": [{"target": "not-an-integer"}]},
    }

    data = result_data(_execute(_schema("public"), mutation, variables, user=assignee))

    assert data["decide"]["decision"] == {"verdict": "PENDING", "attempts": 1}
    assert data["decide"]["validation_errors"].keys() == {
        "review.approved",
        "rows.0.target",
    }
    assert all(messages for messages in data["decide"]["validation_errors"].values())
    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 1


def test_public_decide_checks_act_permission_before_resolution_shape(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """A denied actor cannot exercise schema validation or consume an attempt."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-gql-order-assignee")
    stranger = User.objects.create_user(username="wdc-gql-order-stranger")
    decision_schema = {
        "type": "object",
        "required": ["approved"],
        "properties": {"approved": {"type": "boolean"}},
    }
    workflow = workflow_with_steps(
        name="Permission-first gate",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config([assignee], None, [], decision_schema=decision_schema),
            },
        ),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    mutation = """
        mutation Decide($decision: ID!, $verdict: DecisionVerb!, $payload: JSON) {
          decide(decision: $decision, verdict: $verdict, payload: $payload) {
            decision { verdict }
            validation_errors
          }
        }
    """
    variables = {"decision": str(decision.sqid), "verdict": "COMPLETE", "payload": {}}

    denied = _execute(_schema("public"), mutation, variables, user=stranger)

    assert denied.errors is not None
    assert denied.errors[0].extensions["code"] == "VALIDATION"
    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 0


@pytest.mark.parametrize("surface", ("public", "console"))
def test_decide_hides_unreachable_and_missing_decisions_alike(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    surface: str,
) -> None:
    """An actor outside a decision's read scope cannot learn whether it exists."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-gql-hidden-assignee")
    stranger = User.objects.create_user(username="wdc-gql-hidden-stranger")
    workflow = workflow_with_steps(
        name="Hidden gate",
        steps=(({"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])}),),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    mutation = """
        mutation Decide($decision: ID!) {
          decide(decision: $decision, verdict: COMPLETE, payload: {}) {
            decision { verdict }
          }
        }
    """

    decision_id = str(decision.sqid)
    existing = _execute(_schema(surface), mutation, {"decision": decision_id}, user=stranger)
    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 0
    with system_context(reason="remove decision for public existence-oracle regression"):
        models.QuerySet.delete(Decision.objects.filter(pk=decision.pk))
    missing = _execute(_schema(surface), mutation, {"decision": decision_id}, user=stranger)

    assert existing.errors is not None
    assert missing.errors is not None
    assert existing.data is None
    assert missing.data is None
    assert [error.formatted for error in existing.errors] == [error.formatted for error in missing.errors]


def test_public_decide_accepts_escalate_end_to_end(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """The public enum, resolver, engine, and model accept an escalate verdict."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-gql-escalate-assignee")
    workflow = _workflow_with_gate_routes(policy="one_done", assignees=[assignee])
    run = _open_gate_run(workflow)
    decision = _decision_for(run, "gate")
    mutation = """
        mutation Escalate($decision: ID!) {
          decide(decision: $decision, verdict: ESCALATE) {
            decision { verdict }
            validation_errors
          }
        }
    """

    data = result_data(
        _execute(_schema("public"), mutation, {"decision": str(decision.sqid)}, user=assignee)
    )

    assert data["decide"] == {
        "decision": {"verdict": "ESCALATED"},
        "validation_errors": None,
    }
    decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.ESCALATED
    assert _step_run(run, "gate").outcome == "escalated"


def _workflow_with_gate_routes(
    *,
    name: str = "Gate workflow",
    policy: str,
    assignees: list[Any],
    priorities: list[int] | None = None,
    escalation: list[Any] | None = None,
    escalate_at: Any = None,
    expires_at: Any = None,
) -> Workflow:
    config = _gate_config(
        assignees,
        None,
        escalation or [],
        policy=policy,
        priorities=priorities,
        escalate_at=escalate_at,
        expires_at=expires_at,
    )
    steps = (
        {"key": "gate", "step_class": "gate", "config": config},
        {"key": "completed", "config": {"outcome": "done"}, "is_entry": False},
        {"key": "rejected", "config": {"outcome": "done"}, "is_entry": False},
        {"key": "escalated", "config": {"outcome": "done"}, "is_entry": False},
        {"key": "expired", "config": {"outcome": "done"}, "is_entry": False},
    )
    edges = (
        ("gate", "completed", "completed"),
        ("gate", "rejected", "rejected"),
        ("gate", "escalated", "escalated"),
        ("gate", "expired", "expired"),
    )
    return workflow_with_steps(name=name, steps=steps, edges=edges)


def _gate_config(
    assignees: list[Any],
    requester: Any | None,
    escalation: list[Any],
    *,
    policy: str = "one_done",
    priorities: list[int] | None = None,
    max_attempts: int | None = 3,
    decision_schema: dict[str, Any] | None = None,
    escalate_at: Any = None,
    expires_at: Any = None,
) -> dict[str, Any]:
    slots = []
    for index, assignee in enumerate(assignees):
        slot = {
            "assignee": str(to_subject_ref(assignee)),
            "priority": priorities[index] if priorities else index,
        }
        slots.append(slot)
    return {
        "policy": policy,
        "action": "complete-review",
        "payload": {"title": "Review"},
        "slots": slots,
        "requester": str(to_subject_ref(requester)) if requester is not None else "",
        "escalation": [str(to_subject_ref(user)) for user in escalation],
        "max_attempts": max_attempts,
        "decision_schema": decision_schema or {},
        "escalate_at": escalate_at.isoformat() if escalate_at is not None else "",
        "expires_at": expires_at.isoformat() if expires_at is not None else "",
    }


def _open_gate_run(workflow: Workflow, *, now: Any = None) -> Any:
    run = start_run(workflow)
    advance_once(run, now=now)
    execute_started(run, now=now)
    return run


def _opened_decision(assignees: list[Any], requester: Any | None) -> Any:
    workflow = workflow_with_steps(
        name="Gate workflow",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config(assignees, requester, []),
            },
        ),
        edges=(),
    )
    return _decision_for(_open_gate_run(workflow), "gate")


def _decision_for(run: Any, step_key: str) -> Any:
    with system_context(reason="test workflows decision read"):
        return Decision.objects.get(step_run__run=run, step_run__step__key=step_key)


def _decisions_for(run: Any, step_key: str) -> list[Any]:
    with system_context(reason="test workflows decisions read"):
        queryset = Decision.objects.filter(step_run__run=run, step_run__step__key=step_key)
        return list(queryset.order_by("priority", "pk"))


def _step_run(run: Any, key: str) -> Any:
    with system_context(reason="test workflows gate step read"):
        return StepRun.objects.get(run=run, step__key=key)


def _relationship_subjects(decision: Any, relation: str) -> set[str]:
    Relationship = active_relationship_model()
    with system_context(reason="test workflows relationship read"):
        rows = Relationship.objects.filter(
            resource_type="workflows/decision",
            resource_id=str(decision.sqid),
            relation=relation,
        ).order_by_subject()
    return {
        f"{row.subject_type}:{row.subject_id}"
        + (f"#{row.optional_subject_relation}" if row.optional_subject_relation else "")
        for row in rows
    }


def _user_for_subject(decision: Any, relation: str) -> Any:
    subject = next(iter(_relationship_subjects(decision, relation)))
    subject_id = subject.split(":", 1)[1]
    id_attr = str(getattr(User._meta, "rebac_id_attr", None) or app_settings.REBAC_USER_ID_ATTR)
    return User.objects.sudo(reason="test workflows decision actor lookup").get(**{id_attr: subject_id})


def _platform_admin(username: str) -> Any:
    admin = User.objects.create_superuser(username=username, email=f"{username}@example.com", password="admin")
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin


def _schema(name: str) -> Any:
    workflows_schema = importlib.import_module("angee.workflows.schema")
    parts = {key: tuple(workflows_schema.schemas[name].get(key, ())) for key in SCHEMA_PART_KEYS}
    return GraphQLSchemas([SchemaAddon({name: parts})]).build(name)


def _execute(schema: Any, query: str, variables: dict[str, Any] | None = None, *, user: Any | None = None) -> Any:
    request = RequestFactory().post("/graphql/public/")
    request.user = user
    return execute_schema(schema, query, variables, request=request)
