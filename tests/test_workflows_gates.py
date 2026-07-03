"""Tests for workflow decision gates and resolution paths."""

from __future__ import annotations

import importlib
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils import timezone
from rebac import PermissionDenied, app_settings, system_context, to_subject_ref
from rebac.models import active_relationship_model
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows import engine
from angee.workflows import models as workflow_models
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
pytest_plugins = ("tests.workflows",)


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
            {"key": "finish", "config": {"outcome": "done"}, "is_entry": False},
        ),
        edges=(),
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
    assert "workflow_name" in decision_section
    assert "step_name" in decision_section
    assert "StepRunType" not in sdl


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
            verdict
            resolution
          }
        }
    """

    variables = {"decision": str(decision.sqid), "verdict": "COMPLETE", "payload": {"ok": True}}
    denied = _execute(public, mutation, variables, user=stranger)
    assert denied.errors is not None

    data = result_data(_execute(public, mutation, variables, user=assignee))
    assert data["decide"] == {"verdict": "COMPLETED", "resolution": {"ok": True}}


def _workflow_with_gate_routes(
    *,
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
    return workflow_with_steps(name="Gate workflow", steps=steps, edges=edges)


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
