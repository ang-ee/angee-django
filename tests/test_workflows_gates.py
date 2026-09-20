"""Tests for workflow decision gates and resolution paths."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pydantic import BaseModel, ConfigDict
from rebac import (
    PermissionDenied,
    app_settings,
    system_context,
    to_subject_ref,
)
from rebac.models import active_relationship_model

from angee.base.identity import public_subject_ref
from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.attempts import AttemptResultKind, DecisionRecordAccess, DecisionResolution
from angee.workflows.decision_actions import (
    ReviewAction,
    ReviewDifference,
    ReviewFact,
    ReviewReason,
    ReviewRecordReference,
    build_decision_action,
    compile_decision_action_schema,
    retained_decision_form_schema,
)
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.managers import _retained_record_access_refs
from angee.workflows.steps import (
    DecisionApplyStep,
    DecisionSpec,
    GateStep,
    HandlerStep,
    StepEffect,
    StepExecutionMode,
    StepOutcome,
    StepResult,
)
from tests.conftest import SchemaAddon, execute_schema, result_data
from tests.conftest import create_platform_admin as _platform_admin
from tests.messaging_models import Party
from tests.workflows import (
    WORKFLOW_RUNTIME_MODELS,
    Decision,
    StepRun,
    Workflow,
    WorkflowDispatch,
    WorkflowRun,
    advance_once,
    execute_started,
    start_run,
    step_for,
    workflow_table_setup,
    workflow_with_steps,
)

User = get_user_model()


def test_decision_action_builder_owns_tagged_branches_and_typed_context() -> None:
    """Consumers declare actions and models; the builder alone emits tagged branches."""

    record = ReviewRecordReference(model="parties.Party", id="party-1", label="Supplier")
    authored = build_decision_action(
        actions=(
            ReviewAction(
                value="accept",
                label="Accept",
                verdict="COMPLETE",
                fields=("note",),
            ),
            ReviewAction(
                value="reject",
                label="Reject",
                verdict="REJECT",
                fields=("note",),
                required=("note",),
                variant="destructive",
            ),
        ),
        properties={"note": {"type": "string", "minLength": 1}},
        payload={"invoice": "invoice-1"},
        facts=(
            ReviewFact(
                pointer="/total",
                label="Invoice total",
                value="100.00",
                subject=record,
                authority="source",
                evidence=(record,),
            ),
        ),
        references=record,
        differences=(
            ReviewDifference(
                field="total",
                label="Total",
                left="100.00",
                right="101.00",
                changed=True,
                leftRecord=record,
            ),
        ),
        reasons=(ReviewReason(code="total_changed", parameters={"pages": 1}),),
    )

    assert authored.decision_schema["properties"]["action"]["enum"] == ["accept", "reject"]
    assert [branch["properties"]["action"]["const"] for branch in authored.decision_schema["oneOf"]] == [
        "accept",
        "reject",
    ]
    assert authored.payload["facts"][0]["subject"]["id"] == "party-1"
    contract = compile_decision_action_schema(authored.decision_schema)
    assert contract is not None
    contract.validate_context(authored.payload)


def test_gate_resolves_bound_dynamic_slots_context_authority_and_clean_predicate() -> None:
    """Every dynamic gate field evaluates through the admitted-input binding grammar."""

    authored = build_decision_action(
        actions=(ReviewAction(value="approve", label="Approve", verdict="COMPLETE"),),
        payload={"batch": "batch-1"},
    )
    admitted = {
        "slots": [
            {"assignees": ["auth/user:1"]},
            {"assignees": ["auth/user:2"], "requester": "auth/user:3"},
        ],
        "payload": authored.payload,
        "decision_schema": authored.decision_schema,
        "targets": [
            {
                "model": "parties.Party",
                "id": "party-1",
                "authority_path": ["party_id"],
                "authority_gate_path": ["resolutions", 0, "decision_id"],
            }
        ],
        "record_access": [{"model": "parties.Party", "id": "party-1"}],
        "clean": False,
    }

    def binding(path: str) -> dict[str, Any]:
        return {"kind": "workflow_input", "path": [path]}

    step_run = SimpleNamespace(
        pk=7,
        resume_state={},
        input=admitted,
        current_attempt=SimpleNamespace(input_present=True, input=admitted),
        step=SimpleNamespace(
            config={
                "policy": "all_done",
                "action": "approve_batch",
                "slots": binding("slots"),
                "payload": binding("payload"),
                "decision_schema": binding("decision_schema"),
                "targets": binding("targets"),
                "record_access": binding("record_access"),
                "clean": binding("clean"),
            }
        ),
    )

    result = GateStep().run(step_run, now=timezone.now())

    assert result.kind == "suspend"
    assert result.resume_state == {"gate": {"policy": "all_done"}}
    assert [decision.priority for decision in result.decisions] == [0, 1]
    assert result.decisions[0].payload == {"batch": "batch-1"}
    assert result.decisions[0].target_authority_path == ("party_id",)
    assert result.decisions[1].target_authority_gate_path == ("resolutions", 0, "decision_id")
    assert result.decisions[0].record_access[0].id == "party-1"

    admitted.clear()
    admitted["clean"] = True
    clean = GateStep().run(step_run, now=timezone.now())
    assert clean.kind == "done" and clean.outcome == "completed"
    assert clean.output == {"resolutions": [], "outcome": "completed"}


def test_gate_resumption_returns_retained_state_and_runtime_slot_results() -> None:
    """A same-step gate resumes with its exact retained state and terminal slots."""

    decisions = _DecisionRows(
        [
            SimpleNamespace(
                pk=11,
                payload={"tool_call_id": "call-1", "facts": [{"label": "hidden"}]},
                verdict="completed",
                resolution={"action": "approve"},
            ),
            SimpleNamespace(
                pk=12,
                payload={"tool_call_id": "call-2"},
                verdict="rejected",
                resolution={"action": "reject", "reason": "unsafe"},
            ),
        ]
    )
    projected = {"resolutions": [{"decision_id": "decision-1"}], "outcome": "completed"}
    step_run = SimpleNamespace(
        resume_state={
            "_resume_after_decisions": True,
            "_decision_ids": [11, 12],
            "_decision_outcome": "completed",
            "_decision_resolutions": projected,
            "state": {"turn": "turn-1"},
        },
        decisions=decisions,
    )

    resumed = GateStep.resumption(step_run)

    assert resumed is not None
    assert resumed.resolutions == projected
    assert resumed.state == {"turn": "turn-1"}
    assert resumed.slots == (
        {
            "tool_call_id": "call-1",
            "approved": True,
            "verdict": "completed",
            "resolution": {"action": "approve"},
        },
        {
            "tool_call_id": "call-2",
            "approved": False,
            "verdict": "rejected",
            "resolution": {"action": "reject", "reason": "unsafe"},
        },
    )


class _DecisionRows(list[Any]):
    """Small QuerySet-shaped decision collection for resumption projection."""

    def filter(self, **kwargs: Any) -> _DecisionRows:
        assert set(kwargs) == {"pk__in"}
        return _DecisionRows(row for row in self if row.pk in kwargs["pk__in"])

    def order_by(self, *fields: str) -> _DecisionRows:
        assert fields == ("declaration_index", "pk")
        return self


class _ApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolutions: list[DecisionResolution]
    outcome: str


class _ApplyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applied: bool


def test_decision_apply_base_consumes_provenance_and_passes_durable_wait_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared apply base owns admission while adapters retain native StepResult waits."""

    retained = {
        "decision_id": "decision-1",
        "action": "approve_batch",
        "verdict": "completed",
        "resolution": {"action": "approve"},
        "resolved_by": "auth/user:1",
        "resolved_at": timezone.now().isoformat(),
        "declaration_index": 0,
    }
    predecessor = SimpleNamespace(
        action="approve_batch",
        target_model="parties.Party",
        target_id="party-1",
        resolved_by="auth/user:1",
    )
    actor = object()
    locked = object()
    calls: list[dict[str, Any]] = []
    lock_order: list[str] = []

    class Apply(DecisionApplyStep):
        input_model = _ApplyInput
        output_model = _ApplyOutput
        outcomes = (StepOutcome("applied", "Applied"),)
        effect = StepEffect.WRITE
        execution_mode = StepExecutionMode.DATABASE_COMMAND
        idempotent = True

        def locked_record_basis(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
            lock_order.append("record")
            return (locked,)

        def apply_resolution(self, *args: Any, **kwargs: Any) -> StepResult:
            assert kwargs["record_basis"] == (locked,)
            lock_order.append("apply")
            return StepResult.wait(
                until=timezone.now() + timedelta(minutes=1),
                resume_state={"manager": "retained"},
                waiting_kind="external",
            )

    monkeypatch.setattr(engine, "load_predecessor_gate_decision", lambda *args: predecessor)
    monkeypatch.setattr(
        engine,
        "resolve_workflow_actor",
        lambda *args, **kwargs: SimpleNamespace(actor=actor),
    )

    def consume(*args: Any, **kwargs: Any) -> tuple[Any, DecisionResolution]:
        lock_order.append("ancestry")
        basis_loader = kwargs.pop("required_record_access")
        assert basis_loader() == (locked,)
        calls.append({**kwargs, "required_record_access": "loaded-after-ancestry"})
        return predecessor, DecisionResolution.model_validate_json(json.dumps(retained))

    monkeypatch.setattr(engine, "consume_decision_resolution", consume)
    result = Apply().run(
        SimpleNamespace(input={"resolutions": [retained]}),
        now=timezone.now(),
    )

    assert result.kind == "wait"
    assert result.resume_state == {"manager": "retained"}
    assert lock_order == ["ancestry", "record", "apply"]
    assert calls == [
        {
            "expected_action": "approve_batch",
            "expected_target": ("parties.Party", "party-1"),
            "expected_verdict": "completed",
            "actor": actor,
            "required_record_access": "loaded-after-ancestry",
        }
    ]


def test_predecessor_lookup_loads_the_declared_settled_gate_decision(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Apply adapters find one direct gate predecessor without reimplementing graph queries."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="predecessor-gate-assignee")
    workflow = workflow_with_steps(
        name="Predecessor lookup",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": {
                    "action": "approve_batch",
                    "slots": [{"assignees": [str(to_subject_ref(assignee))]}],
                },
            },
            {"key": "apply", "step_class": "handler", "config": {}},
        ),
        edges=(("gate", "apply", "completed"),),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "gate")
    assert engine.decide(decision, "complete", actor=assignee).validation_error is None
    advance_once(run)

    loaded = engine.load_predecessor_gate_decision(_step_run(run, "apply"), GateStep)

    assert loaded.pk == decision.pk


@pytest.fixture()
def workflow_gate_record_access_tables(
    transactional_db: Any,
    tmp_path: Path,
) -> Iterator[None]:
    """Compose the native pending-Decision Party owner before the fixture's sole sync."""

    del transactional_db
    app_configs = list(apps.get_app_configs())
    runtime_dir = tmp_path / "permissions"
    source_map = extension_source_map(app_configs)
    for relpath, text in source_map.items():
        write_atomic(runtime_dir / relpath, text)
    apply_schema_paths(app_configs, runtime_dir, sources=source_map)
    with workflow_table_setup((*WORKFLOW_RUNTIME_MODELS, Party)):
        yield


def _action_schema(
    *,
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
    actions: tuple[str, ...] = ("complete",),
    verdicts: dict[str, str] | None = None,
    admitted: dict[str, tuple[str, ...]] | None = None,
    all_of: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Author the same closed tagged-action contract required in production."""

    fields = dict(properties or {})
    mappings = verdicts or {
        "complete": "COMPLETE",
        "reject": "REJECT",
        "escalate": "ESCALATE",
    }
    schema: dict[str, Any] = {
        "type": "object",
        "required": ["action", *required],
        "properties": {
            "action": {
                "type": "string",
                "enum": list(actions),
                "options": [
                    {
                        "value": action,
                        "label": action.replace("_", " ").title(),
                        "verdict": mappings[action],
                    }
                    for action in actions
                ],
            },
            **fields,
        },
        "oneOf": [
            {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"const": action},
                    **{name: fields[name] for name in (admitted or {}).get(action, tuple(fields))},
                },
                "additionalProperties": False,
            }
            for action in actions
        ],
    }
    if all_of:
        schema["allOf"] = all_of
    return schema


def _refresh_decision(decision: Any) -> None:
    """Refresh an actor-scoped Decision only through an explicit test owner."""

    with system_context(reason="test workflows decision refresh"):
        decision.refresh_from_db()


def test_decision_context_local_defs_are_validated_with_root_scope() -> None:
    """A published typed context $ref keeps its root $defs at resolution."""

    schema = {
        "type": "object",
        "required": ["action"],
        "$defs": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["pointer", "label", "value", "authority"],
                    "properties": {
                        "pointer": {"type": "string"},
                        "label": {"type": "string"},
                        "value": {},
                        "authority": {"enum": ["source", "correction", "unverified"]},
                    },
                },
            }
        },
        "properties": {
            "action": {
                "type": "string",
                "enum": ["approve"],
                "options": [
                    {"value": "approve", "label": "Approve", "verdict": "COMPLETE"},
                ],
            },
            "facts": {"$ref": "#/$defs/facts", "layout": "context", "widget": "facts"},
        },
        "oneOf": [
            {
                "type": "object",
                "required": ["action"],
                "properties": {"action": {"const": "approve"}},
                "additionalProperties": False,
            }
        ],
    }
    contract = compile_decision_action_schema(schema)
    assert contract is not None
    contract.validate_context(
        {
            "facts": [
                {
                    "pointer": "/supplier",
                    "label": "Supplier",
                    "value": "A",
                    "authority": "source",
                }
            ]
        }
    )
    with pytest.raises(ValidationError, match="does not satisfy"):
        contract.validate_context({"facts": [{"pointer": "/supplier"}]})


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
                    assignees=(str(public_subject_ref(to_subject_ref(assignee))),),
                    requester=str(public_subject_ref(to_subject_ref(requester))),
                    escalation=(str(public_subject_ref(to_subject_ref(escalated))),),
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

    run = start_run(workflow, actor=requester)
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
        target._meta.label,
        str(target.sqid),
        "details",
    )
    with system_context(reason="test retained decision target attempt"):
        retained = decision.suspension_attempt.result_decisions[0]
    assert (retained["target_model"], retained["target_id"], retained["target_tab"]) == (
        target._meta.label,
        str(target.sqid),
        "details",
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
        engine.decide(decision, "complete", payload={"action": "complete"}, actor=requester)
    with pytest.raises(PermissionDenied):
        engine.decide(decision, "complete", payload={"action": "complete"}, actor=stranger)

    engine.decide(decision, "complete", payload={"action": "complete"}, actor=admin)

    _refresh_decision(decision)
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
        engine.decide(
            decision,
            verb,
            payload={"action": verb},
            actor=_user_for_subject(decision, "assignee"),
        )

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
    engine.decide(decision, "complete", payload={"action": "complete"}, actor=assignee)

    gate.refresh_from_db()
    assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
    assert gate.outcome == "completed"


def test_settled_retained_decision_output_feeds_downstream_binding(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One winning slot retains the original gate value after sibling expiry."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-bound-decision")
    pending_assignee = User.objects.create_user(username="wdc-bound-decision-pending")
    requester = User.objects.create_user(username="wdc-bound-decision-requester")

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
    run = engine.start(workflow, subject=None, actor=requester)
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
    with system_context(reason="test settled sibling assertion"):
        pending.refresh_from_db()
    assert suspension.result_kind == str(AttemptResultKind.SUSPEND)
    assert pending.verdict == workflow_models.Verdict.EXPIRED
    assert suspension.decision_settlement == {
        "decision_ids": [decision.pk],
        "outcome": "completed",
    }
    assert consumer.status == workflow_models.StepRunStatus.SUCCEEDED
    assert consumer.output == gate.output
    assert consumer.output["outcome"] == "completed"
    assert [item["decision_id"] for item in consumer.output["resolutions"]] == [decision.sqid]
    assert consumer.current_attempt.input_provenance["settled_decision_ids"] == [
        decision.pk,
    ]


def test_owned_call_consumes_exact_terminal_record_delegation_or_current_reads(
    workflow_gate_record_access_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child consumes its exact gate and no broader terminal record delegation."""

    del workflow_gate_record_access_tables, no_workflow_queue
    requester = User.objects.create_user(username="owned-call-consume-requester")
    resolver = User.objects.create_user(username="owned-call-consume-resolver")
    consumed: list[int] = []
    with system_context(reason="owned-call target fixture"):
        target = Workflow.objects.create(name="Owned call unread target", created_by=requester)
        protected = Party.objects.create(
            display_name="Protected delegated Party",
            created_by=requester,
        )
        undeclared = Party.objects.create(
            display_name="Undeclared Party",
            created_by=requester,
        )
        declared_extra = Party.objects.create(
            display_name="Second protected delegated Party",
            created_by=requester,
        )
        directly_readable = Party.objects.create(
            display_name="Directly readable Party",
            created_by=resolver,
        )
    assert not target.with_actor(resolver).has_access("read")
    assert not protected.with_actor(resolver).has_access("read")
    assert protected.with_actor(requester).has_access("write")
    assert directly_readable.with_actor(resolver).has_access("read")

    def gate_then_consume(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        if step_run.step.key == "gate":
            return StepResult.suspend(
                resume_state={"gate": {"policy": "one_done"}},
                decisions=(
                    DecisionSpec(
                        assignees=(str(to_subject_ref(resolver)),),
                        action="approve-owned-call-input",
                        target_model=target._meta.label,
                        target_id=str(target.sqid),
                        record_access=(
                            DecisionRecordAccess(
                                model=protected._meta.label,
                                id=str(protected.sqid),
                            ),
                            DecisionRecordAccess(
                                model=declared_extra._meta.label,
                                id=str(declared_extra.sqid),
                            ),
                        ),
                    ),
                ),
            )
        with pytest.raises(ValidationError, match="retained Decision gate"):
            engine.consume_decision_resolution(
                step_run,
                ("resolutions", 0),
                expected_action="approve-owned-call-input",
                expected_target=(target._meta.label, str(target.sqid)),
                expected_verdict="completed",
                actor=resolver,
            )
        assert not protected.with_actor(resolver).has_access("read")
        with pytest.raises(DjangoPermissionDenied, match="complete required record basis"):
            engine.consume_decision_resolution(
                step_run,
                ("resolutions", 0),
                input_source="owned_call_input",
                expected_action="approve-owned-call-input",
                expected_target=(target._meta.label, str(target.sqid)),
                expected_verdict="completed",
                actor=resolver,
                required_record_access=(protected,),
            )
        with pytest.raises(DjangoPermissionDenied, match="complete required record basis"):
            engine.consume_decision_resolution(
                step_run,
                ("resolutions", 0),
                input_source="owned_call_input",
                expected_action="approve-owned-call-input",
                expected_target=(target._meta.label, str(target.sqid)),
                expected_verdict="completed",
                actor=resolver,
                required_record_access=(protected, declared_extra, undeclared),
            )
        direct_decision, _ = engine.consume_decision_resolution(
            step_run,
            ("resolutions", 0),
            input_source="owned_call_input",
            expected_action="approve-owned-call-input",
            expected_target=(target._meta.label, str(target.sqid)),
            expected_verdict="completed",
            actor=resolver,
            required_record_access=(directly_readable,),
        )
        decision, resolution = engine.consume_decision_resolution(
            step_run,
            ("resolutions", 0),
            input_source="owned_call_input",
            expected_action="approve-owned-call-input",
            expected_target=(target._meta.label, str(target.sqid)),
            expected_verdict="completed",
            actor=resolver,
            required_record_access=(protected, declared_extra),
        )
        assert direct_decision.pk == decision.pk
        consumed.append(decision.pk)
        return StepResult.done(output={"decision_id": resolution.decision_id})

    monkeypatch.setattr(HandlerStep, "run", gate_then_consume)
    child_workflow = workflow_with_steps(
        name="Owned call gate consumer",
        steps=(
            {
                "key": "consume",
                "step_class": "handler",
                "config": {},
                "input_binding": {"kind": "workflow_input", "path": []},
            },
        ),
        edges=(),
    )
    parent_workflow = workflow_with_steps(
        name="Owned call gate producer",
        steps=(
            {"key": "gate", "step_class": "handler", "config": {}},
            {
                "key": "call",
                "step_class": "call_workflow",
                "config": {"publication": str(child_workflow.sqid)},
                "input_binding": {
                    "kind": "object",
                    "fields": {"input": {"kind": "step_output", "step_key": "gate", "path": []}},
                },
            },
        ),
        edges=(("gate", "call", "completed"),),
    )
    parent = engine.start(parent_workflow, subject=None, actor=requester)
    advance_once(parent)
    execute_started(parent)
    gate = _decision_for(parent, "gate")
    assert protected.with_actor(resolver).has_access("read")
    assert declared_extra.with_actor(resolver).has_access("read")
    assert engine.decide(gate, "complete", actor=resolver).validation_error is None
    assert not protected.with_actor(resolver).has_access("read")
    assert not declared_extra.with_actor(resolver).has_access("read")
    advance_once(parent)
    execute_started(parent)
    with system_context(reason="owned-call consume child fixture"):
        child = WorkflowRun.objects.get(parent_step_run__run=parent)
    advance_once(child)
    execute_started(child)

    consume = _step_run(child, "consume")
    attempt = consume.current_attempt
    assert attempt is not None
    retained_failure = f"error={attempt.error!r}\nstacktrace={attempt.stacktrace or ''}"
    assert consume.status == workflow_models.StepRunStatus.SUCCEEDED, retained_failure
    assert attempt.result_kind == str(AttemptResultKind.DONE), retained_failure
    assert consumed == [gate.pk]
    assert consume.output == {"decision_id": str(gate.sqid)}


def test_retained_decision_record_access_rejects_duplicate_refs() -> None:
    retained = {
        "resource_type": "storage/file",
        "resource_id": "1",
    }
    with pytest.raises(ValidationError, match="must be unique"):
        _retained_record_access_refs(SimpleNamespace(record_access=[retained, dict(retained)]))


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
    run = start_run(workflow, actor=assignee)
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


def test_delivery_expires_departed_suspension_before_failed_rerun(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broad event cannot leave the prior approval actionable after rerunning its step."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-delivery-retirement")

    def suspend_then_fail(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        if step_run.attempt == 1:
            return StepResult.suspend(
                decisions=(
                    DecisionSpec(
                        assignees=(str(to_subject_ref(assignee)),),
                        action="approve-tool",
                    ),
                ),
            )
        raise RuntimeError("rerun failed after delivery")

    monkeypatch.setattr(HandlerStep, "run", suspend_then_fail)
    workflow = workflow_with_steps(
        name="Retire delivered suspension",
        steps=({"key": "handler", "step_class": "handler", "config": {}},),
        edges=(),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "handler")

    assert engine.deliver(run.pk) == {"woken": 1}
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.EXPIRED
    advance_once(run)
    execute_started(run)
    advance_once(run)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.FAILED
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.EXPIRED


def test_orphan_repair_refuses_current_approval_and_expires_terminal_orphan(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repair owner leaves an active approval alone and retires it after terminal failure."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-orphan-retirement")

    def suspend(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(
            decisions=(
                DecisionSpec(
                    assignees=(str(to_subject_ref(assignee)),),
                    action="approve-tool",
                ),
            )
        )

    monkeypatch.setattr(HandlerStep, "run", suspend)
    workflow = workflow_with_steps(
        name="Repair orphaned suspension",
        steps=({"key": "handler", "step_class": "handler", "config": {}},),
        edges=(),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "handler")

    assert engine.expire_orphaned_decisions(run, resolved_by="test/orphan-repair") == 0
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING
    with system_context(reason="test terminal orphan fixture"):
        run.refresh_from_db()
        run.mark_failed("Controlled failure after suspension")
    assert engine.expire_orphaned_decisions(run, resolved_by="test/orphan-repair") == 1
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.EXPIRED


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
    run = start_run(workflow, actor=first)
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
        engine.decide(
            second_decision,
            "complete",
            payload={"action": "complete"},
            actor=second,
        )

    engine.decide(first_decision, "complete", payload={"action": "complete"}, actor=first)
    _refresh_decision(first_decision)
    _refresh_decision(second_decision)
    assert first_decision.verdict == workflow_models.Verdict.COMPLETED
    assert second_decision.verdict == workflow_models.Verdict.PENDING
    assert _step_run(run, "gate").status == workflow_models.StepRunStatus.WAITING

    engine.decide(second_decision, "complete", payload={"action": "complete"}, actor=second)
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
                    decision_schema=_action_schema(
                        properties={
                            "password": {"type": "string", "const": "open-sesame"},
                        },
                        required=("password",),
                    ),
                ),
            },
        ),
        edges=(),
    )
    run = _open_gate_run(workflow)
    decision = _decision_for(run, "gate")

    engine.decide(
        decision,
        "complete",
        payload={"action": "complete", "password": "wrong"},
        actor=assignee,
    )
    _refresh_decision(decision)
    gate = _step_run(run, "gate")
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 1
    assert gate.status == workflow_models.StepRunStatus.WAITING

    engine.decide(
        decision,
        "complete",
        payload={"action": "complete", "password": "wrong-again"},
        actor=assignee,
    )
    _refresh_decision(decision)
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
    decision_schema = _action_schema(
        required=("review", "rows"),
        properties={
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
    )
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
        payload={
            "action": "complete",
            "review": {"approved": "not-a-boolean"},
            "rows": [{"target": "nope", "mode": "merge"}],
        },
        actor=assignee,
    )
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 1

    resolution = {
        "action": "complete",
        "review": {"approved": True},
        "rows": [{"target": 7, "mode": "append"}],
    }
    engine.decide(decision, "complete", payload=resolution, actor=assignee)
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.COMPLETED
    assert decision.resolution == resolution


def test_decision_schema_enforces_resolution_conditional_requirements(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """The native Decision owner gates inputs selected by the submitted action."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-conditional-schema-assignee")
    schema = _action_schema(
        actions=("approve", "reject"),
        verdicts={"approve": "COMPLETE", "reject": "COMPLETE"},
        properties={
            "party_id": {"type": "string", "minLength": 1, "pattern": r".*\S.*"},
        },
        all_of=[
            {
                "if": {"properties": {"action": {"const": "approve"}}, "required": ["action"]},
                "then": {"required": ["party_id"]},
            }
        ],
    )
    workflow = workflow_with_steps(
        name="Conditional schema gate",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config([assignee], None, [], decision_schema=schema),
            },
        ),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")

    engine.decide(decision, "complete", payload={"action": "approve"}, actor=assignee)
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING

    engine.decide(decision, "complete", payload={"action": "approve", "party_id": ""}, actor=assignee)
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING

    engine.decide(decision, "complete", payload={"action": "reject"}, actor=assignee)
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.COMPLETED
    assert decision.resolution["action"] == "reject"


def test_decision_mapping_schema_enforces_authored_constraints_after_normalization(
    monkeypatch: Any,
) -> None:
    """Full JSON Schema sees coerced submitted fields, before relation authorization."""

    relation_checks: list[dict[str, Any]] = []
    monkeypatch.setattr(
        engine,
        "_validate_relation_fields",
        lambda _schema, resolution, _actor: relation_checks.append(resolution),
    )
    schema = _action_schema(
        actions=("apply",),
        verdicts={"apply": "COMPLETE"},
        required=("amount",),
        properties={
            "amount": {"type": "integer"},
            "payment_term_id": {"type": "string"},
            "due_date": {"type": "string"},
            "note": {"type": "string"},
        },
        all_of=[
            {
                "oneOf": [
                    {"required": ["payment_term_id"]},
                    {"required": ["due_date"]},
                ]
            }
        ],
    )

    with pytest.raises(ValidationError):
        engine._validate_mapping_schema(
            schema,
            {
                "action": "apply",
                "amount": "7",
                "payment_term_id": "net-30",
                "due_date": "2030-01-01",
            },
        )
    assert relation_checks == []

    validated = engine._validate_mapping_schema(
        schema,
        {"action": "apply", "amount": "7", "payment_term_id": "net-30"},
    )
    assert validated == {
        "action": "apply",
        "amount": 7,
        "payment_term_id": "net-30",
    }
    assert relation_checks == [validated]


def test_decision_mapping_schema_excludes_layout_context_from_resolution() -> None:
    """Frozen display context is neither accepted nor materialized as a decision answer."""

    schema = _action_schema(
        actions=("accept", "reject"),
        verdicts={"accept": "COMPLETE", "reject": "REJECT"},
        properties={
            "source_evidence": {
                "type": "object",
                "layout": "context",
                "readOnly": True,
                "widget": "object",
                "required": ["kind", "invoice_id"],
                "properties": {
                    "kind": {"const": "invoice_review"},
                    "invoice_id": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
            "note": {"type": "string"},
        },
        admitted={"accept": ("note",), "reject": ("note",)},
    )
    contract = compile_decision_action_schema(schema)
    assert contract is not None
    contract.validate_context(
        {
            "source_evidence": {"kind": "invoice_review", "invoice_id": "inv_exact"},
        }
    )
    with pytest.raises(ValidationError, match="does not satisfy"):
        contract.validate_context(
            {
                "source_evidence": {"kind": "invoice_review", "invoice_id": ""},
            }
        )

    assert engine._validate_mapping_schema(schema, {"action": "accept"}) == {
        "action": "accept",
    }
    with pytest.raises(ValidationError, match="cannot be submitted"):
        engine._validate_mapping_schema(
            schema,
            {"source_evidence": "forged", "action": "accept"},
        )


def test_decision_relation_permission_defaults_to_write_and_allows_declared_read(monkeypatch: Any) -> None:
    """Relation selection changes scope only through a valid explicit permission."""

    model = object()
    actions: list[str] = []
    monkeypatch.setattr(engine.apps, "get_model", lambda _app, _model: model)
    monkeypatch.setattr(
        engine,
        "read_scoped_queryset",
        lambda _model, _actor, *, action: actions.append(action) or object(),
    )
    monkeypatch.setattr(engine, "instance_from_public_id", lambda _model, _value, *, queryset: object())

    assert engine._relation_error({"resource": "demo.Company"}, "company-1", object()) is None
    assert (
        engine._relation_error(
            {"resource": "demo.Company", "permission": "read"},
            "company-1",
            object(),
        )
        is None
    )
    assert actions == ["write", "read"]

    assert (
        engine._relation_error(
            {"resource": "demo.Company", "permission": "read;delete"},
            "company-1",
            object(),
        )
        == "Relation value must reference a permitted record."
    )
    assert actions == ["write", "read"]


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

    with system_context(reason="test workflows read escalation dispatch"):
        dispatch = WorkflowDispatch.objects.get(
            decision=decision,
            kind=WorkflowDispatchKind.DECISION_ESCALATE,
        )
    with pytest.raises(ValidationError, match="durable intent"):
        engine.escalate_decision_dispatch(
            dispatch.pk,
            expected_decision_id=decision.pk,
            expected_generation=decision.attempts + 1,
            now=now + timedelta(minutes=10),
        )
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING

    engine.escalate_decision_dispatch(
        dispatch.pk,
        expected_decision_id=decision.pk,
        expected_generation=decision.attempts,
        now=now + timedelta(minutes=10),
    )
    _refresh_decision(decision)
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

    with system_context(reason="test workflows read expiry dispatch"):
        dispatch = WorkflowDispatch.objects.get(
            decision=decision,
            kind=WorkflowDispatchKind.DECISION_EXPIRE,
        )
    engine.expire_decision_dispatch(
        dispatch.pk,
        expected_decision_id=decision.pk,
        expected_generation=decision.attempts,
        now=now + timedelta(minutes=10),
    )

    _refresh_decision(decision)
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
            {
                "key": "unused",
                "config": {"outcome": "done"},
                "is_entry": False,
            },
        ),
        edges=(
            ("active", "finish", "unreachable"),
            ("active", "unused", "also_unreachable"),
        ),
    )
    active = step_for(workflow, "active")
    finish = step_for(workflow, "finish")
    unused = step_for(workflow, "unused")
    with system_context(reason="test workflows override setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=workflow_models.RunStatus.RUNNING)
        active_row = StepRun.objects.create(run=run, step=active, status=workflow_models.StepRunStatus.STARTED)
        unused_row = StepRun.objects.create(run=run, step=unused, status=workflow_models.StepRunStatus.SCHEDULED)

    override = engine.override_run(run, [finish], actor=admin)

    active_row.refresh_from_db()
    unused_row.refresh_from_db()
    assert active_row.status == workflow_models.StepRunStatus.CANCELED
    assert unused_row.status == workflow_models.StepRunStatus.CANCELED
    assert unused_row.resume_state == {"cancel_requested": True}
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
    target_section = sdl.split("type WorkflowArtifactTarget", 1)[1].split("type ", 1)[0]
    assert "label: String" in target_section

    workflows_schema = importlib.import_module("angee.workflows.schema")
    parts = {key: tuple(workflows_schema.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}
    metadata = GraphQLSchemas([SchemaAddon({"public": parts})]).render_metadata()["public"]["angee"]
    decision = next(item for item in metadata["resources"] if item["modelLabel"] == "workflows.Decision")
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


@pytest.mark.django_db(transaction=True)
def test_decision_subject_egress_uses_public_ids(workflow_gate_tables: None) -> None:
    """Decision actor fields hide canonical PKs while retaining audit sentinels."""

    del workflow_gate_tables
    workflows_schema = importlib.import_module("angee.workflows.schema")
    assignee = User.objects.create_user(username="wdc-subject-egress")
    canonical = to_subject_ref(assignee)
    public = public_subject_ref(canonical)

    assert workflows_schema._public_subject_value(str(canonical)) == str(public)
    assert workflows_schema._public_subject_value(str(public)) == str(public)
    assert workflows_schema._public_subject_value("") == ""
    assert workflows_schema._public_subject_value("workflows/timer:expire") == "workflows/timer:expire"


def test_decision_schema_is_exposed_narrowly_on_public_and_console_decisions(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Both projections expose the enforced JSON form schema, or null."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-schema-reader")
    admin = _platform_admin("wdc-schema-admin")
    decision_schema = _action_schema(
        properties={"approved": {"type": "boolean"}},
        required=("approved",),
    )
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
    schema_less_workflow = workflow_with_steps(
        name="Schema-less delivery gate",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": _gate_config([assignee], None, [], decision_schema={}),
            },
        ),
        edges=(),
    )
    schema_less_decision = _decision_for(_open_gate_run(schema_less_workflow), "gate")
    invalid = engine.decide(
        schema_decision,
        "complete",
        payload={"action": "complete"},
        actor=assignee,
    )
    assert invalid.validation_error is not None
    query = """
        query DecisionSchema($id: String!) {
          workflow_decisions_by_pk(id: $id) {
            decision_schema
          }
        }
    """

    public = _schema("public")
    public_schema = result_data(_execute(public, query, {"id": str(schema_decision.sqid)}, user=assignee))
    public_schema_less = result_data(_execute(public, query, {"id": str(schema_less_decision.sqid)}, user=assignee))
    console_schema = result_data(_execute(_schema("console"), query, {"id": str(schema_decision.sqid)}, user=admin))
    console_schema_less = result_data(
        _execute(_schema("console"), query, {"id": str(schema_less_decision.sqid)}, user=admin)
    )

    expected_schema = retained_decision_form_schema(decision_schema)
    assert public_schema["workflow_decisions_by_pk"]["decision_schema"] == expected_schema
    assert public_schema_less["workflow_decisions_by_pk"]["decision_schema"] is None
    assert console_schema["workflow_decisions_by_pk"]["decision_schema"] == expected_schema
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

    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING

    with pytest.raises(TypeError, match="transition owner"):
        decision.resolve(
            workflow_models.Verdict.COMPLETED,
            resolution={},
            resolved_by="bypass",
        )
    _refresh_decision(decision)
    with pytest.raises(TypeError, match="transition owner"):
        decision.record_invalid_resolution()
    _refresh_decision(decision)
    with pytest.raises(TypeError, match="DecisionManager"):
        type(decision).objects.filter(pk=decision.pk).update(verdict=workflow_models.Verdict.COMPLETED)
    decision.attempts += 1
    with pytest.raises(TypeError, match="DecisionManager"):
        type(decision).objects.bulk_update([decision], ["attempts"])

    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 0


def test_public_decision_schema_query_count_stays_flat_for_three_rows(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Decision form-schema projection carries its relation in the parent query."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-schema-query-reader")
    decision_schema = _action_schema(
        properties={"approved": {"type": "boolean"}},
    )

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
    assert "rebac_permissionauditevent" not in " ".join(query["sql"].lower() for query in three_rows.captured_queries)


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
    assigned_console = result_data(_execute(console, console_query, {"id": str(decision.sqid)}, user=assignee))
    privileged_console = result_data(_execute(console, console_query, {"id": str(decision.sqid)}, user=admin))

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
    assert privileged["workflow_decisions_by_pk"]["source_run_id"] == str(decision.step_run.run.sqid)
    assert privileged["workflow_decisions_by_pk"]["source_execution_id"] == str(decision.step_run.sqid)
    assert privileged["workflow_decisions_by_pk"]["source_attempt_id"] == str(decision.suspension_attempt.sqid)
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

    variables = {
        "decision": str(decision.sqid),
        "verdict": "COMPLETE",
        "payload": {"action": "complete"},
    }
    denied = _execute(public, mutation, variables, user=stranger)
    assert denied.errors is not None

    data = result_data(_execute(public, mutation, variables, user=assignee))
    assert data["decide"] == {
        "decision": {"verdict": "COMPLETED", "resolution": {"action": "complete"}},
        "validation_errors": None,
    }


def test_public_decide_returns_dotted_field_errors_and_reopens_the_decision(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Invalid input returns field-keyed validation errors and preserves retry state."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-gql-validation-assignee")
    decision_schema = _action_schema(
        required=("review", "rows"),
        properties={
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
    )
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
        "payload": {
            "action": "complete",
            "review": {},
            "rows": [{"target": "not-an-integer"}],
        },
    }

    data = result_data(_execute(_schema("public"), mutation, variables, user=assignee))

    assert data["decide"]["decision"] == {"verdict": "PENDING", "attempts": 1}
    assert data["decide"]["validation_errors"].keys() == {
        "review.approved",
        "rows.0.target",
    }
    assert all(messages for messages in data["decide"]["validation_errors"].values())
    _refresh_decision(decision)
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
    decision_schema = _action_schema(
        properties={"approved": {"type": "boolean"}},
        required=("approved",),
    )
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
    _refresh_decision(decision)
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
          decide(decision: $decision, verdict: COMPLETE, payload: {action: "complete"}) {
            decision { verdict }
          }
        }
    """

    decision_id = str(decision.sqid)
    existing = _execute(_schema(surface), mutation, {"decision": decision_id}, user=stranger)
    _refresh_decision(decision)
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
          decide(decision: $decision, verdict: ESCALATE, payload: {action: "escalate"}) {
            decision { verdict }
            validation_errors
          }
        }
    """

    data = result_data(_execute(_schema("public"), mutation, {"decision": str(decision.sqid)}, user=assignee))

    assert data["decide"] == {
        "decision": {"verdict": "ESCALATED"},
        "validation_errors": None,
    }
    _refresh_decision(decision)
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
        "decision_schema": (
            _action_schema(actions=("complete", "reject", "escalate")) if decision_schema is None else decision_schema
        ),
        "escalate_at": escalate_at.isoformat() if escalate_at is not None else "",
        "expires_at": expires_at.isoformat() if expires_at is not None else "",
    }


def _open_gate_run(workflow: Workflow, *, now: Any = None) -> Any:
    actor = User.objects.create_user(username=f"wdc-run-actor-{workflow.pk}")
    run = engine.start(workflow, subject=None, actor=actor)
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
            resource_id=str(decision.pk),
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


def _schema(name: str) -> Any:
    workflows_schema = importlib.import_module("angee.workflows.schema")
    parts = {key: tuple(workflows_schema.schemas[name].get(key, ())) for key in SCHEMA_PART_KEYS}
    return GraphQLSchemas([SchemaAddon({name: parts})]).build(name)


def _execute(schema: Any, query: str, variables: dict[str, Any] | None = None, *, user: Any | None = None) -> Any:
    request = RequestFactory().post("/graphql/public/")
    request.user = user
    return execute_schema(schema, query, variables, request=request)
