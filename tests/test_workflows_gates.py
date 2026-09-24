"""Tests for workflow decision gates and resolution paths."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from jsonschema import Draft202012Validator
from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter
from rebac import (
    app_settings,
    system_context,
    to_subject_ref,
)
from rebac.models import active_relationship_model

from angee.base.identity import public_subject_ref
from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.dashboards.models import validate_dashboard_queries
from angee.fs import write_atomic
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows import decision_actions, engine
from angee.workflows import models as workflow_models
from angee.workflows.attempts import (
    AttemptResultKind,
    DecisionResolution,
    GateResumeState,
    JsonPresence,
    RecoveryMode,
    validate_json_value,
)
from angee.workflows.configs import GateBinding, GateConfig
from angee.workflows.decision_actions import (
    ReviewAction,
    ReviewFact,
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
    StepEffect,
    StepExecutionMode,
    StepImpl,
    StepOutcome,
    StepResult,
    retry_policy_from_config,
)
from tests.conftest import SchemaAddon, execute_schema, result_data
from tests.conftest import create_platform_admin as _platform_admin
from tests.messaging_models import Party
from tests.workflows import (
    WORKFLOW_RUNTIME_MODELS,
    Decision,
    FixtureStep,
    StepRun,
    Workflow,
    WorkflowDispatch,
    WorkflowRun,
    admit_workflow_actor,
    advance_once,
    execute_started,
    run_to_terminal,
    start_run,
    step_for,
    workflow_table_setup,
    workflow_with_steps,
)

User = get_user_model()


def test_decision_action_builder_owns_tagged_branches_and_typed_context() -> None:
    """Consumers declare actions and models; the builder alone emits tagged branches."""

    record = ReviewRecordReference(model="parties.Party", id="party-1", label="Counterparty")
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
        payload={"document": "document-1"},
        facts=(
            ReviewFact(
                pointer="/total",
                label="Document total",
                value="100.00",
                subject=record,
                authority="source",
                evidence=(record,),
            ),
        ),
        references=record,
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


def test_decision_action_builder_merges_action_field_overrides() -> None:
    """One field keeps shared annotations while each action owns its constraints."""

    frozen_id = "pty_frozen"
    authored = build_decision_action(
        actions=(
            ReviewAction(
                value="bind",
                label="Select candidate",
                verdict="COMPLETE",
                fields=("party_id",),
                required=("party_id",),
            ),
            ReviewAction(
                value="other",
                label="Select another",
                verdict="COMPLETE",
                fields=("party_id",),
                required=("party_id",),
            ),
        ),
        properties={
            "party_id": {
                "type": "string",
                "label": "Party",
                "defaultValue": frozen_id,
                "relation": {
                    "resource": "parties.Party",
                    "permission": "read",
                    "create": {"resource": "parties.Organization"},
                },
            }
        },
        action_properties={
            "bind": {
                "party_id": {
                    "enum": [frozen_id],
                    "relation": {
                        "resource": "parties.Party",
                        "permission": "read",
                        "filters": [
                            {"field": "id", "operator": "in", "value": [frozen_id]}
                        ],
                    },
                }
            },
            "other": {"party_id": {"not": {"enum": [frozen_id]}}},
        },
    )

    schema = authored.decision_schema
    assert "enum" not in schema["properties"]["party_id"]
    bind_field = schema["oneOf"][0]["properties"]["party_id"]
    other_field = schema["oneOf"][1]["properties"]["party_id"]
    assert bind_field["label"] == "Party"
    assert bind_field["defaultValue"] == frozen_id
    assert bind_field["enum"] == [frozen_id]
    assert "create" not in bind_field["relation"]
    assert other_field["relation"]["create"] == {"resource": "parties.Organization"}
    assert other_field["not"] == {"enum": [frozen_id]}
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"action": "bind", "party_id": frozen_id})
    assert not validator.is_valid({"action": "bind", "party_id": "pty_other"})
    assert validator.is_valid({"action": "other", "party_id": "pty_other"})
    assert not validator.is_valid({"action": "other", "party_id": frozen_id})

    with pytest.raises(ValueError, match="overrides unadmitted fields"):
        build_decision_action(
            actions=(
                ReviewAction(
                    value="invalid",
                    label="Invalid",
                    verdict="COMPLETE",
                ),
            ),
            action_properties={"invalid": {"party_id": {"type": "string"}}},
        )


def test_gate_resolves_bound_dynamic_slots_context_and_clean_predicate() -> None:
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
    assert result.decisions[0].record_access[0].id == "party-1"

    admitted.clear()
    admitted["clean"] = True
    clean = GateStep().run(step_run, now=timezone.now())
    assert clean.kind == "done" and clean.outcome == "completed"
    assert clean.output == {"resolutions": [], "outcome": "completed"}


@pytest.mark.parametrize("clean", (False, True))
def test_producer_gate_binding_round_trips_into_native_gate(
    workflow_gate_record_access_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    clean: bool,
) -> None:
    """A declared producer output retains all six values through native gate admission."""

    del workflow_gate_record_access_tables, no_workflow_queue
    actor = _platform_admin("wdc-binding-admin")
    assignee = User.objects.create_user(username="wdc-binding-reviewer")
    with system_context(reason="test gate binding review record"):
        target = Party.objects.create(display_name="Binding review target")
    record = {"model": target._meta.label, "id": str(target.sqid)}
    authored = build_decision_action(
        actions=(ReviewAction(value="approve", label="Approve", verdict="COMPLETE"),),
        payload={"batch": "batch-1"},
    )
    assignee_ref = str(to_subject_ref(assignee))
    binding = (
        GateBinding(clean=True)
        if clean
        else GateBinding.model_validate(
            {
                "slots": [{"assignees": [assignee_ref]}],
                "payload": authored.payload,
                "decision_schema": authored.decision_schema,
                "targets": [{**record, "tab": "details"}],
                "record_access": [record],
                "clean": False,
            }
        )
    )
    output = binding.model_dump(mode="json")

    def produce(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.done(output=output, outcome="done")

    monkeypatch.setattr(FixtureStep, "output_model", GateBinding)
    monkeypatch.setattr(FixtureStep, "run", produce)
    workflow = workflow_with_steps(
        name="Producer gate binding",
        steps=(
            {"key": "producer", "step_class": "fixture"},
            {
                "key": "gate",
                "step_class": "gate",
                "input_binding": {"kind": "step_output", "step_key": "producer", "path": []},
                "config": {
                    "action": "review-binding",
                    **{
                        name: {"kind": "workflow_input", "path": [name]}
                        for name in ("slots", "payload", "decision_schema", "targets", "record_access", "clean")
                    },
                },
            },
        ),
        edges=(("producer", "gate", "done"),),
    )
    run = start_run(workflow, actor=actor)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)

    producer = _step_run(run, "producer")
    gate = _step_run(run, "gate")
    assert producer.output == gate.input == gate.current_attempt.input == output
    assert GateBinding.model_validate(gate.input) == binding
    if clean:
        assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
        assert gate.outcome == "completed"
        assert gate.output == {"resolutions": [], "outcome": "completed"}
        assert _decisions_for(run, "gate") == []
        return

    assert gate.status == workflow_models.StepRunStatus.WAITING
    assert len(_decisions_for(run, "gate")) == 1
    assert len(gate.current_attempt.result_decisions) == 1
    retained = gate.current_attempt.result_decisions[0]
    assert retained["assignees"] == [assignee_ref]
    assert retained["priority"] == 0
    assert retained["payload"] == authored.payload
    assert retained["decision_schema"] == authored.decision_schema
    assert (retained["target_model"], retained["target_id"], retained["target_tab"]) == (
        record["model"],
        record["id"],
        "details",
    )
    assert retained["record_access"] == [record]


@pytest.mark.parametrize("slot_schema", [False, True])
def test_gate_config_rejects_static_action_unions_before_admission(slot_schema: bool) -> None:
    """The typed config owner rejects handwritten unions but admits producer output."""

    schema = build_decision_action(
        actions=(ReviewAction(value="approve", label="Approve", verdict="COMPLETE"),)
    ).decision_schema
    config = {
        "action": "approve",
        "slots": [{"assignees": ["auth/user:1"]}],
    }
    if slot_schema:
        config["slots"][0]["decision_schema"] = schema
    else:
        config["decision_schema"] = schema

    with pytest.raises(ValueError, match="oneOf"):
        GateConfig.model_validate(config)
    with pytest.raises(ValidationError, match="oneOf"):
        GateStep.normalize_config(config)
    with pytest.raises(ValidationError, match="oneOf"):
        GateStep.gate_config(SimpleNamespace(step=SimpleNamespace(config=config)))
    assert GateConfig.model_validate(config, context={"resolved_bindings": True})


@pytest.mark.parametrize("config", [None, False, 1, "invalid", []])
def test_step_config_validation_rejects_non_objects_consistently(config: Any) -> None:
    step_run = SimpleNamespace(step=SimpleNamespace(config=config))
    validators = (
        lambda: StepImpl.validate_config(config),
        lambda: retry_policy_from_config(config),
        lambda: GateStep.gate_config(step_run),
    )

    for validate in validators:
        with pytest.raises(ValidationError) as error:
            validate()
        assert error.value.message_dict == {"config": ["Step config must be a JSON object."]}


@pytest.mark.parametrize(
    "state",
    [
        {"_resume_after_decisions": "true"},
        {"_decision_ids": ["11"]},
        {"_decision_outcome": "completed"},
        {"_decision_resolutions": {}},
        {"_decision_outcome": "completed", "_decision_resolutions": {}},
    ],
)
def test_gate_resume_state_rejects_coercion_and_partial_settlement(state: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GateResumeState.from_checkpoint(state)


def test_gate_resume_state_preserves_custom_checkpoint_fields() -> None:
    checkpoint = {
        "_resume_after_decisions": True,
        "gate": {"policy": "all_done"},
        "cursor": "page-2",
        "state": ["page-2"],
        "decision_ids": ["operation-owned"],
        "decision_outcome": {"operation": "pending"},
        "resume_after_decisions": "operation-owned",
    }
    state = GateResumeState.from_checkpoint(checkpoint)
    assert state.resume_after_decisions is True
    assert state.decision_ids == []
    assert state.model_dump(mode="json", exclude_defaults=True) == checkpoint
    assert state.model_copy(update={"decision_ids": [11]}).model_dump(mode="json", exclude_defaults=True) == {
        **checkpoint,
        "_decision_ids": [11],
    }


def test_decision_form_schema_reads_retained_reserved_checkpoint_keys() -> None:
    decision = Decision(pk=11)
    schema = {"type": "object", "properties": {"reason": {"type": "string"}}}
    checkpoint = {"_decision_schemas": {"11": schema}, "state": ["operation-owned"]}
    setattr(decision, Decision._form_schema_state_attribute, checkpoint)

    assert decision.form_schema == schema
    checkpoint["_decision_schemas"] = {"11": []}
    with pytest.raises(ValidationError):
        _ = decision.form_schema


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
    step_run.resume_state["state"] = []
    with pytest.raises(ValidationError, match="retained state must be an object"):
        GateStep.resumption(step_run)


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


class _StrictTupleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    values: tuple[str, ...]
    resolved_at: AwareDatetime


@pytest.mark.parametrize(
    "validator",
    [_StrictTupleOutput.model_validate_json, TypeAdapter(_StrictTupleOutput).validate_json],
    ids=["model", "adapter"],
)
@pytest.mark.parametrize("values", [["retained"], ("retained",)])
def test_json_validation_preserves_strict_types_after_transport(
    validator: Callable[[str], _StrictTupleOutput],
    values: Any,
) -> None:
    resolved_at = timezone.now()
    value = {"values": values, "resolved_at": resolved_at.isoformat()}

    parsed = validate_json_value(validator, value)

    assert parsed.values == ("retained",)
    assert parsed.resolved_at == resolved_at
    with pytest.raises(ValueError, match="valid string"):
        validate_json_value(validator, {**value, "values": [1]})


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_json_validation_rejects_nonfinite_values_before_schema_validation(number: float) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        validate_json_value(TypeAdapter(Any).validate_json, {"nested": [number]})


@pytest.mark.parametrize("has_resolver", [True, False])
def test_decision_apply_dispatches_identity_and_preserves_wait(
    monkeypatch: pytest.MonkeyPatch,
    has_resolver: bool,
) -> None:
    """The dispatcher accepts human and timer outcomes without an authority token."""

    actor = object() if has_resolver else None
    predecessor = SimpleNamespace(pk=42, resolution_actor_subject=lambda: actor)
    calls: list[dict[str, Any]] = []

    class Apply(DecisionApplyStep):
        input_model = _ApplyInput
        output_model = _ApplyOutput
        outcomes = (StepOutcome("applied", "Applied"),)
        effect = StepEffect.WRITE
        execution_mode = StepExecutionMode.DATABASE_COMMAND
        idempotent = True

        def invoke_command(self, step_run: Any, **kwargs: Any) -> StepResult:
            calls.append(kwargs)
            return StepResult.wait(
                until=timezone.now() + timedelta(minutes=1),
                resume_state={"manager": "retained"},
                waiting_kind="external",
            )

    monkeypatch.setattr(type(Decision.objects), "predecessor_decision", lambda *args: predecessor)
    now = timezone.now()
    result = Apply().run(StepRun(), now=now)
    assert result.kind == "wait"
    assert result.resume_state == {"manager": "retained"}
    assert calls == [{"decision_id": 42, "actor": actor, "now": now}]


def test_decision_apply_validates_strict_output_through_json_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict tuple and datetime fields round-trip through retained JSON."""

    predecessor = SimpleNamespace(pk=42, resolution_actor_subject=lambda: object())
    resolved_at = timezone.now()

    class Apply(DecisionApplyStep):
        input_model = _ApplyInput
        output_model = _StrictTupleOutput
        outcomes = (StepOutcome("applied", "Applied"),)
        effect = StepEffect.WRITE
        execution_mode = StepExecutionMode.DATABASE_COMMAND
        idempotent = True

        def invoke_command(self, step_run: Any, **kwargs: Any) -> StepResult:
            del self, step_run, kwargs
            return StepResult.done(
                {
                    "values": ["retained"],
                    "resolved_at": resolved_at.isoformat(),
                },
                outcome="applied",
            )

    monkeypatch.setattr(
        type(Decision.objects),
        "predecessor_decision",
        lambda *args: predecessor,
    )
    result = Apply().run(StepRun(), now=timezone.now())
    assert result.output == {
        "values": ["retained"],
        "resolved_at": resolved_at.isoformat(),
    }

    class InvalidApply(Apply):
        def invoke_command(self, step_run: Any, **kwargs: Any) -> StepResult:
            del self, step_run, kwargs
            return StepResult.done(
                {
                    "values": "retained",
                    "resolved_at": resolved_at.isoformat(),
                },
                outcome="applied",
            )

    with pytest.raises(ValueError, match="valid array"):
        InvalidApply().run(StepRun(), now=timezone.now())


@pytest.mark.parametrize("through_prepare", (False, True))
def test_predecessor_lookup_loads_the_declared_settled_gate_decision(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    through_prepare: bool,
) -> None:
    """The apply dispatcher selects and validates direct and prepared gate routes."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="predecessor-gate-assignee")
    applied: list[int] = []

    class Apply(DecisionApplyStep):
        input_model = _ApplyInput
        output_model = _ApplyOutput
        outcomes = (StepOutcome("applied", "Applied"),)
        effect = StepEffect.WRITE
        execution_mode = StepExecutionMode.DATABASE_COMMAND
        idempotent = True

        def invoke_command(self, step_run: Any, *, decision_id: int, actor: Any, now: Any) -> StepResult:
            del self, now
            with Decision.objects.locked_resolution(
                decision_id,
                actor=actor,
                consumer_step_run_id=step_run.pk,
            ) as retained:
                applied.append(retained.pk)
            return StepResult.done({"applied": True}, outcome="applied")

    fixture_run = FixtureStep.run

    def dispatch(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        if step_run.step.key == "apply":
            return Apply().run(step_run, now=now)
        return fixture_run(self, step_run, now=now)

    monkeypatch.setattr(FixtureStep, "run", dispatch)
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
            *(({"key": "prepare", "step_class": "fixture", "config": {}},) if through_prepare else ()),
            {"key": "apply", "step_class": "fixture", "config": {}},
        ),
        edges=(
            (("gate", "prepare", "completed"), ("prepare", "apply", "done"))
            if through_prepare
            else (("gate", "apply", "completed"),)
        ),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "gate")
    assert engine.decide(decision, "complete", actor=assignee).validation_error is None
    run_to_terminal(run)

    assert applied == [decision.pk]
    assert _step_run(run, "apply").output == {"applied": True}


@pytest.mark.parametrize("first_route", ("farther", "same_depth", "unselected"))
def test_predecessor_lookup_uses_nearest_gate_on_retained_routes(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    first_route: str,
) -> None:
    """Distance and ambiguity use retained engine routes, not definition edges."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="predecessor-nearest-assignee")
    workflow = workflow_with_steps(
        name="Nearest retained predecessor",
        steps=(
            {"key": "entry", "config": {}},
            {"key": "first_gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},
            {
                "key": "second_gate",
                "step_class": "gate",
                "config": _gate_config([assignee], None, []),
            },
            {"key": "first_prepare", "config": {}},
            *(({"key": "first_route", "config": {}},) if first_route == "farther" else ()),
            {"key": "second_prepare", "config": {}},
            {"key": "apply", "config": {}},
        ),
        edges=(
            ("entry", "first_gate", "done"),
            ("entry", "second_gate", "done"),
            ("first_gate", "first_prepare", "rejected" if first_route == "unselected" else "completed"),
            *(
                (("first_prepare", "first_route", "done"), ("first_route", "apply", "done"))
                if first_route == "farther"
                else (("first_prepare", "apply", "done"),)
            ),
            ("second_gate", "second_prepare", "completed"),
            ("second_prepare", "apply", "done"),
        ),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)
    for key in ("first_gate", "second_gate"):
        assert engine.decide(
            _decision_for(run, key), "complete", payload={"action": "complete"}, actor=assignee,
        ).validation_error is None
    run_to_terminal(run, stop_key="apply")
    consumer = _step_run(run, "apply")

    if first_route == "same_depth":
        with pytest.raises(ValidationError, match="one declared predecessor gate"):
            Decision.objects.predecessor_decision(consumer, GateStep)
    else:
        selected = Decision.objects.predecessor_decision(consumer, GateStep)
        assert selected.pk == _decision_for(run, "second_gate").pk
    if first_route == "unselected":
        assert _step_run(run, "first_prepare").status == workflow_models.StepRunStatus.SKIPPED
        with system_context(reason="test retained predecessor route"):
            assert list(consumer.previous.values_list("step__key", flat=True)) == ["second_prepare"]


def test_predecessor_lookup_ignores_skipped_gate_retained_by_unconditional_join(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """An all_done join retains skipped rows without making them gate candidates."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="predecessor-skipped-assignee")
    workflow = workflow_with_steps(
        name="Skipped gate in retained join ancestry",
        steps=(
            {"key": "entry", "config": {"outcome": "active"}},
            {"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},
            {"key": "skipped_gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},
            {"key": "prepare", "config": {}, "join_rule": workflow_models.JoinRule.ALL_DONE},
            {"key": "apply", "config": {}},
        ),
        edges=(
            ("entry", "gate", "active"),
            ("entry", "skipped_gate", "inactive"),
            ("gate", "prepare", ""),
            ("skipped_gate", "prepare", ""),
            ("prepare", "apply", "done"),
        ),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "gate")
    assert engine.decide(decision, "complete", payload={"action": "complete"}, actor=assignee).validation_error is None
    run_to_terminal(run, stop_key="apply")
    gate = _step_run(run, "gate")
    skipped_gate = _step_run(run, "skipped_gate")
    prepare = _step_run(run, "prepare")
    consumer = _step_run(run, "apply")

    assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
    assert skipped_gate.status == workflow_models.StepRunStatus.SKIPPED
    assert skipped_gate.current_attempt_id is None
    with system_context(reason="test unconditional join retains skipped gate"):
        assert set(prepare.previous.values_list("pk", flat=True)) == {gate.pk, skipped_gate.pk}
        assert list(consumer.previous.values_list("pk", flat=True)) == [prepare.pk]
    assert Decision.objects.predecessor_decision(consumer, GateStep).pk == decision.pk


def test_predecessor_lookup_rejects_route_without_a_gate(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Exhausted retained ancestry preserves the missing-gate validation error."""

    del workflow_gate_tables, no_workflow_queue
    workflow = workflow_with_steps(
        name="No retained gate",
        steps=({"key": "prepare", "config": {}}, {"key": "apply", "config": {}}),
        edges=(("prepare", "apply", "done"),),
    )
    run = start_run(workflow)
    run_to_terminal(run, stop_key="apply")

    with pytest.raises(ValidationError, match="one declared predecessor gate"):
        Decision.objects.predecessor_decision(_step_run(run, "apply"), GateStep)


def test_predecessor_lookup_does_not_fall_back_past_a_clean_nearest_gate(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """A nearer clean gate cannot borrow an older gate's settled approval."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="predecessor-clean-assignee")
    workflow = workflow_with_steps(
        name="Nearest gate requires its own settlement",
        steps=(
            {"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},
            {
                "key": "clean_gate",
                "step_class": "gate",
                "config": {**_gate_config([assignee], None, []), "clean": True},
            },
            {"key": "prepare", "config": {}},
            {"key": "apply", "config": {}},
        ),
        edges=(
            ("gate", "clean_gate", "completed"),
            ("clean_gate", "prepare", "completed"),
            ("prepare", "apply", "done"),
        ),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    assert engine.decide(
        _decision_for(run, "gate"), "complete", payload={"action": "complete"}, actor=assignee,
    ).validation_error is None
    run_to_terminal(run, stop_key="apply")

    with pytest.raises(ValidationError, match="retained predecessor settlement"):
        Decision.objects.predecessor_decision(_step_run(run, "apply"), GateStep)


@pytest.mark.parametrize("ancestry", ("map", "child"))
def test_predecessor_lookup_crosses_map_and_child_ancestry(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    ancestry: str,
) -> None:
    """Engine-created Map members and owned children share retained gate authority."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="predecessor-nested-assignee")
    if ancestry == "child":
        child_workflow = workflow_with_steps(
            name="Child gate consumer",
            steps=({"key": "apply", "config": {}},),
            edges=(),
        )
        admit_workflow_actor(child_workflow, assignee)
        nested = {
            "key": "nested",
            "step_class": "call_workflow",
            "config": {"publication": str(child_workflow.sqid)},
            "input_binding": {"kind": "constant", "value": {}},
        }
    else:
        nested = {"key": "nested", "step_class": "map", "config": {"target_step": "apply", "items": [1]}}
    workflow = workflow_with_steps(
        name="Nested retained predecessor",
        steps=(
            {"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},
            {"key": "prepare", "config": {}},
            nested,
            *(({"key": "apply", "config": {}},) if ancestry == "map" else ()),
        ),
        edges=(("gate", "prepare", "completed"), ("prepare", "nested", "done")),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "gate")
    assert engine.decide(decision, "complete", payload={"action": "complete"}, actor=assignee).validation_error is None
    applied: list[int] = []
    fixture_run = FixtureStep.run

    def apply(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        if step_run.step.key != "apply":
            return fixture_run(self, step_run, now=now)
        selected = Decision.objects.predecessor_decision(step_run, GateStep)
        with Decision.objects.locked_resolution(
            selected.pk,
            actor=assignee,
            consumer_step_run_id=step_run.pk,
        ) as retained:
            applied.append(retained.pk)
        return StepResult.done(outcome="done")

    monkeypatch.setattr(FixtureStep, "run", apply)
    run_to_terminal(run)

    assert applied == [decision.pk]


def test_locked_resolution_allows_active_child_after_parent_run_succeeds(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed asynchronous starter remains valid retained child ancestry."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="completed-parent-child-assignee")
    parent_workflow = workflow_with_steps(
        name="Completed child starter",
        steps=({"key": "start", "config": {}},),
        edges=(),
    )
    child_workflow = workflow_with_steps(
        name="Decision child after completed starter",
        steps=(
            {"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},
            {"key": "apply", "config": {}},
        ),
        edges=(("gate", "apply", "completed"),),
    )
    admit_workflow_actor(child_workflow, assignee)
    parent = start_run(parent_workflow, actor=assignee)
    run_to_terminal(parent)
    with system_context(reason="load completed child starter"):
        parent_step = StepRun.objects.get(run=parent, step__key="start")
    child = engine.start(
        child_workflow,
        subject=None,
        actor=assignee,
        parent_step_run=parent_step,
        parent_relation="continuation",
        origin=workflow_models.RunOrigin.WORKFLOW,
    )
    advance_once(child)
    execute_started(child)
    decision = _decision_for(child, "gate")
    assert engine.decide(
        decision,
        "complete",
        payload={"action": "complete"},
        actor=assignee,
    ).validation_error is None
    applied: list[int] = []
    fixture_run = FixtureStep.run

    def apply(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        if step_run.step.key != "apply":
            return fixture_run(self, step_run, now=now)
        with Decision.objects.locked_resolution(
            decision.pk,
            actor=assignee,
            consumer_step_run_id=step_run.pk,
        ) as retained:
            applied.append(retained.pk)
        return StepResult.done(outcome="done")

    monkeypatch.setattr(FixtureStep, "run", apply)
    run_to_terminal(child)

    assert parent.status == workflow_models.RunStatus.SUCCEEDED
    assert applied == [decision.pk]


@pytest.mark.parametrize("recovery_source", ("apply", "map", "prepare"))
def test_predecessor_lookup_recovers_the_exact_transitive_gate_source(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    recovery_source: str,
) -> None:
    """A FRESH redirect retains prepared and mapped source ancestry for application."""

    del workflow_gate_tables, no_workflow_queue
    assignee = _platform_admin("predecessor-recovery-assignee")
    mapped = recovery_source == "map"
    failed_key = "prepare" if recovery_source == "prepare" else "apply"
    workflow = workflow_with_steps(
        name="Recover transitive gate consumer",
        steps=(
            {"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},
            {"key": "prepare", "config": {}},
            *(
                ({"key": "map", "step_class": "map", "config": {"target_step": "apply", "items": [1]}},)
                if mapped
                else ()
            ),
            {"key": "apply", "config": {}},
        ),
        edges=(("gate", "prepare", "completed"), ("prepare", "map" if mapped else "apply", "done")),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "gate")
    assert engine.decide(decision, "complete", payload={"action": "complete"}, actor=assignee).validation_error is None
    applied: list[int] = []
    fixture_run = FixtureStep.run

    def apply(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        if step_run.step.key == failed_key and step_run.run.origin != workflow_models.RunOrigin.RECOVERY:
            raise RuntimeError("Recover this gate application from its retained source.")
        if step_run.step.key != "apply":
            return fixture_run(self, step_run, now=now)
        selected = Decision.objects.predecessor_decision(step_run, GateStep)
        with Decision.objects.locked_resolution(
            selected.pk,
            actor=assignee,
            consumer_step_run_id=step_run.pk,
        ) as retained:
            applied.append(retained.pk)
        return StepResult.done(outcome="done")

    monkeypatch.setattr(FixtureStep, "run", apply)
    monkeypatch.setattr(FixtureStep, "execution_mode", StepExecutionMode.DATABASE_COMMAND)
    monkeypatch.setattr(FixtureStep, "replay_mode", RecoveryMode.FRESH)
    run_to_terminal(run, allow_failed={run.pk})
    source_step = _step_run(run, failed_key)
    with system_context(reason="test predecessor recovery source"):
        source = source_step.current_attempt
    recovery = WorkflowRun.objects.start_recovery(source, request_key="transitive-gate-retry", actor=assignee)
    run_to_terminal(recovery)

    assert applied == [decision.pk]
    recovered = _step_run(recovery, failed_key)
    assert (recovered.step_id, recovered.map_index) == (source_step.step_id, source_step.map_index)


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
                    "pointer": "/counterparty",
                    "label": "Counterparty",
                    "value": "A",
                    "authority": "source",
                }
            ]
        }
    )
    with pytest.raises(ValidationError, match="does not satisfy"):
        contract.validate_context({"facts": [{"pointer": "/counterparty"}]})


@pytest.fixture(autouse=True)
def executable_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the concrete fixture operation executable while tests replace behavior."""

    def run(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        return StepResult.done(outcome=str(step_run.step.config.get("outcome", "done")))

    monkeypatch.setattr(FixtureStep, "run", run)


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
    checkpoint = {"phase": "awaiting-review", "state": ["operation-owned"], "decision_ids": ["external-id"]}

    def suspend_from_fixture(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(
            resume_state=checkpoint,
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

    monkeypatch.setattr(FixtureStep, "run", suspend_from_fixture)
    workflow = workflow_with_steps(
        name="Gate workflow",
        steps=({"key": "fixture", "step_class": "fixture", "config": {}},),
        edges=(),
    )

    run = start_run(workflow, actor=requester)
    advance_once(run)
    execute_started(run)

    decision = _decision_for(run, "fixture")
    assert decision.priority == 0
    assert decision.action == "complete-review"
    assert decision.payload == {"title": "Review"}
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.max_attempts == 3
    row = _step_run(run, "fixture")
    assert row.resume_state == {**checkpoint, "_decision_ids": [decision.pk]}
    assert decision.form_schema is None

    assert _relationship_subjects(decision, "assignee") == {str(to_subject_ref(assignee))}
    assert _relationship_subjects(decision, "requester") == {str(to_subject_ref(requester))}
    assert _relationship_subjects(decision, "escalation") == {str(to_subject_ref(escalated))}
    engine.decide(decision, "complete", actor=assignee)
    row.refresh_from_db()
    assert row.status == workflow_models.StepRunStatus.SUCCEEDED


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
        steps=({"key": "target", "step_class": "fixture", "config": {}},),
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

    def suspend_from_fixture(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(decisions=(declaration,))

    monkeypatch.setattr(FixtureStep, "run", suspend_from_fixture)
    gate = workflow_with_steps(
        name="Targeted decision",
        steps=({"key": "fixture", "step_class": "fixture", "config": {}},),
        edges=(),
    )
    run = engine.start(gate, None, actor=admit_workflow_actor(gate, admin))
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "fixture")

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

    assert GateResumeState.from_checkpoint(gate.resume_state).decision_ids == [decision.pk]
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

    def gate_then_consume(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
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

    monkeypatch.setattr(FixtureStep, "run", gate_then_consume)
    workflow = workflow_with_steps(
        name="Bound retained decision output",
        steps=(
            {"key": "gate", "step_class": "fixture", "config": {}},
            {
                "key": "consumer",
                "step_class": "fixture",
                "config": {},
                "input_binding": {"kind": "step_output", "step_key": "gate", "path": []},
            },
        ),
        edges=(("gate", "consumer", "completed"),),
    )
    run = engine.start(workflow, subject=None, actor=admit_workflow_actor(workflow, requester))
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

    def suspend(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
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

    monkeypatch.setattr(FixtureStep, "run", suspend)
    workflow = workflow_with_steps(
        name="Force expiry continuation",
        steps=({"key": "fixture", "step_class": "fixture", "config": {}},),
        edges=(),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    row = _step_run(run, "fixture")
    prior_attempt_id = row.current_attempt_id

    assert engine.expire_pending_decisions(run, resolved_by="test/session-close") == 1

    row.refresh_from_db()
    decision = _decision_for(run, "fixture")
    assert decision.verdict == workflow_models.Verdict.EXPIRED
    assert row.current_attempt_id == prior_attempt_id
    assert GateResumeState.from_checkpoint(row.resume_state).decision_outcome == "completed"
    assert row.wait_until is not None


@pytest.mark.parametrize(
    ("policy", "outcome", "retained_count", "legacy_settlement"),
    (("one_done", "expired", 1, False), ("one_done", "expired", 2, True), ("all_done", "completed", 2, False)),
)
def test_force_expiry_retains_the_policy_owned_predecessor_evidence(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
    outcome: str,
    retained_count: int,
    legacy_settlement: bool,
) -> None:
    """Bulk expiry preserves all rows and the exact policy-owned settlement."""

    del workflow_gate_tables, no_workflow_queue
    requester = User.objects.create_user(username="wdc-expiry-requester")
    assignee = User.objects.create_user(username="wdc-expiry-verifier")

    def consume(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        return StepResult.done(output=step_run.input)

    monkeypatch.setattr(FixtureStep, "run", consume)
    workflow = workflow_with_steps(
        name="Force expiry one_done predecessor",
        steps=(
            {
                "key": "gate",
                "step_class": "gate",
                "config": {
                    "action": "verify-expiring-review",
                    "policy": policy,
                    "slots": [
                        {"assignees": [str(to_subject_ref(assignee))], "priority": 0},
                        {"assignees": [str(to_subject_ref(requester))], "priority": 1},
                    ],
                },
            },
            {
                "key": "consumer",
                "step_class": "fixture",
                "config": {},
                "input_binding": {"kind": "step_output", "step_key": "gate", "path": []},
            },
        ),
        edges=(("gate", "consumer", outcome),),
    )
    run = engine.start(workflow, None, actor=admit_workflow_actor(workflow, requester))
    advance_once(run)
    execute_started(run)
    decisions = _decisions_for(run, "gate")
    assert len(decisions) == 2
    with monkeypatch.context() as legacy_writer:
        if legacy_settlement:
            legacy_writer.setattr(
                type(_step_run(run, "gate").decision_gate),
                "settled_decisions",
                lambda self, rows: tuple(row for row in rows if row.verdict in workflow_models.Verdict.TERMINAL),
            )
        assert engine.expire_pending_decisions(run, resolved_by="test/expiry") == 2
    advance_once(run)
    execute_started(run)
    gate = _step_run(run, "gate")
    consumer = _step_run(run, "consumer")
    assert gate.current_attempt.decision_settlement == {
        "decision_ids": [row.pk for row in decisions[:retained_count]],
        "outcome": outcome,
    }
    assert [item["decision_id"] for item in consumer.output["resolutions"]] == [
        row.sqid for row in decisions[:retained_count]
    ]
    if policy == "one_done":
        assert Decision.objects.predecessor_decision(consumer, GateStep).pk == decisions[0].pk
    else:
        with pytest.raises(ValidationError, match="one settled predecessor slot"):
            Decision.objects.predecessor_decision(consumer, GateStep)
    assert all(row.verdict == workflow_models.Verdict.EXPIRED for row in _decisions_for(run, "gate"))


def test_delivery_expires_departed_suspension_before_failed_rerun(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broad event cannot leave the prior approval actionable after rerunning its step."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-delivery-retirement")

    def suspend_then_fail(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
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

    monkeypatch.setattr(FixtureStep, "run", suspend_then_fail)
    workflow = workflow_with_steps(
        name="Retire delivered suspension",
        steps=({"key": "fixture", "step_class": "fixture", "config": {}},),
        edges=(),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "fixture")

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

    def suspend(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(
            decisions=(
                DecisionSpec(
                    assignees=(str(to_subject_ref(assignee)),),
                    action="approve-tool",
                ),
            )
        )

    monkeypatch.setattr(FixtureStep, "run", suspend)
    workflow = workflow_with_steps(
        name="Repair orphaned suspension",
        steps=({"key": "fixture", "step_class": "fixture", "config": {}},),
        edges=(),
    )
    run = start_run(workflow, actor=assignee)
    advance_once(run)
    execute_started(run)
    decision = _decision_for(run, "fixture")

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

    def suspend_each_attempt(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        assignees = [first] if step_run.attempt == 1 else [second, third]
        return StepResult.suspend(
            resume_state=GateResumeState(resume_after_decisions=True, gate={"policy": "all_done"}).model_dump(
                mode="json", exclude_defaults=True
            ),
            decisions=tuple(
                DecisionSpec(
                    assignees=(str(to_subject_ref(assignee)),),
                    action="approve-tool",
                    priority=index,
                )
                for index, assignee in enumerate(assignees)
            ),
        )

    monkeypatch.setattr(FixtureStep, "run", suspend_each_attempt)
    workflow = workflow_with_steps(
        name="Resumable decision workflow",
        steps=({"key": "fixture", "step_class": "fixture", "config": {}},),
        edges=(),
    )
    run = start_run(workflow, actor=first)
    advance_once(run)
    execute_started(run)

    row = _step_run(run, "fixture")
    first_decision = _decisions_for(run, "fixture")[0]
    assert GateResumeState.from_checkpoint(row.resume_state).decision_ids == [first_decision.pk]

    engine.decide(first_decision, "complete", actor=first)
    row.refresh_from_db()
    assert row.status == workflow_models.StepRunStatus.WAITING
    assert GateResumeState.from_checkpoint(row.resume_state).decision_outcome == "completed"

    advance_once(run)
    execute_started(run)
    row.refresh_from_db()
    all_decisions = _decisions_for(run, "fixture")
    current = all_decisions[1:]
    assert len(current) == 2
    assert GateResumeState.from_checkpoint(row.resume_state).decision_ids == [decision.pk for decision in current]
    assert first_decision.pk not in GateResumeState.from_checkpoint(row.resume_state).decision_ids

    engine.decide(current[0], "complete", actor=second)
    row.refresh_from_db()
    assert row.status == workflow_models.StepRunStatus.WAITING
    assert GateResumeState.from_checkpoint(row.resume_state).decision_outcome is None

    engine.decide(current[1], "complete", actor=third)
    row.refresh_from_db()
    assert row.status == workflow_models.StepRunStatus.WAITING
    assert GateResumeState.from_checkpoint(row.resume_state).decision_outcome == "completed"


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
                "input_binding": {"kind": "workflow_input", "path": []},
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
                "input_binding": {"kind": "workflow_input", "path": []},
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
                "input_binding": {"kind": "workflow_input", "path": []},
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


def test_decision_json_schema_preserves_types_and_authored_constraints(
    monkeypatch: Any,
) -> None:
    """JSON Schema rejects coercion and validates all authored constraints before relations."""

    relation_checks: list[dict[str, Any]] = []
    monkeypatch.setattr(
        decision_actions,
        "_validate_relation_fields",
        lambda _schema, resolution, _actor: relation_checks.append(resolution),
    )
    schema = _action_schema(
        actions=("apply",),
        verdicts={"apply": "COMPLETE"},
        required=("amount",),
        properties={
            "amount": {"type": "integer"},
            "review_schedule_id": {"type": "string"},
            "due_date": {"type": "string"},
            "note": {"type": "string"},
        },
        all_of=[
            {
                "oneOf": [
                    {"required": ["review_schedule_id"]},
                    {"required": ["due_date"]},
                ]
            }
        ],
    )

    with pytest.raises(ValidationError):
        _validate_json_resolution(
            schema,
            {
                "action": "apply",
                "amount": "7",
                "review_schedule_id": "monthly",
                "due_date": "2030-01-01",
            },
        )
    assert relation_checks == []

    with pytest.raises(ValidationError):
        _validate_json_resolution(schema, {"action": "apply", "amount": "7", "review_schedule_id": "monthly"})
    assert relation_checks == []

    validated = _validate_json_resolution(
        schema,
        {"action": "apply", "amount": 7, "review_schedule_id": "monthly"},
    )
    assert validated == {
        "action": "apply",
        "amount": 7,
        "review_schedule_id": "monthly",
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
                "required": ["kind", "document_id"],
                "properties": {
                    "kind": {"const": "document_review"},
                    "document_id": {"type": "string", "minLength": 1},
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
            "source_evidence": {"kind": "document_review", "document_id": "doc_exact"},
        }
    )
    with pytest.raises(ValidationError, match="does not satisfy"):
        contract.validate_context(
            {
                "source_evidence": {"kind": "document_review", "document_id": ""},
            }
        )

    assert _validate_json_resolution(schema, {"action": "accept"}) == {
        "action": "accept",
    }
    with pytest.raises(ValidationError, match="cannot be submitted"):
        _validate_json_resolution(
            schema,
            {"source_evidence": "forged", "action": "accept"},
        )


def test_decision_relation_permission_defaults_to_write_and_allows_declared_read(monkeypatch: Any) -> None:
    """Relation selection changes scope only through a valid explicit permission."""

    model = object()
    actions: list[str] = []
    monkeypatch.setattr(decision_actions.apps, "get_model", lambda _app, _model: model)
    monkeypatch.setattr(
        decision_actions,
        "read_scoped_queryset",
        lambda _model, _actor, *, action: actions.append(action) or StepRun.objects.none(),
    )
    monkeypatch.setattr(decision_actions, "instance_from_public_id", lambda _model, _value, *, queryset: object())

    assert (
        decision_actions._relation_error({"resource": "demo.Company"}, "company-1", object()) is None
    )
    assert (
        decision_actions._relation_error(
            {"resource": "demo.Company", "permission": "read"}, "company-1", object()
        )
        is None
    )
    assert actions == ["write", "read"]

    assert (
        decision_actions._relation_error(
            {"resource": "demo.Company", "permission": "read;delete"}, "company-1", object()
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
        WorkflowDispatch.objects.deliver(
            dispatch.pk,
            expected_target_id=decision.pk,
            expected_generation=decision.attempts + 1,
            now=now + timedelta(minutes=10),
        )
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING

    WorkflowDispatch.objects.deliver(
        dispatch.pk,
        expected_target_id=decision.pk,
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
    WorkflowDispatch.objects.deliver(
        dispatch.pk,
        expected_target_id=decision.pk,
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


def test_console_dashboard_resources_expose_lineage_and_pending_filters(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Keep workflow keys filterable and names as the relation label axis.

    Declaring both ``workflow__key`` and ``workflow__name`` as group leaves
    raised ``ImproperlyConfigured`` in ``_relation_label_axes``: a grouped
    relation has one label axis. Pending Decisions retain their native filter.
    """

    del workflow_gate_tables, no_workflow_queue
    workflows_schema = importlib.import_module("angee.workflows.schema")
    parts = {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}
    metadata = GraphQLSchemas([SchemaAddon({"console": parts})]).render_metadata()["console"]["angee"]
    resources = {item["modelLabel"]: item for item in metadata["resources"]}
    workflow_run = resources["workflows.WorkflowRun"]
    decision = resources["workflows.Decision"]

    assert workflow_run["query"]["fields"]["workflow.key"]["filter"]["field"] == "workflow__key"
    assert "workflow.key" not in workflow_run["query"]["axes"]
    assert decision["query"]["fields"]["step_run.run.workflow.key"]["filter"]["field"] == "step_run__run__workflow__key"
    assert decision["query"]["fields"]["step_run.step.key"]["sort"] is not None
    for field in ("workflow_key", "workflow_name", "step_key", "step_name"):
        assert decision["query"]["fields"][field]["row"] is not None
    assert decision["query"]["fields"]["workflow_key"].get("filter") is None
    assert {"from": "PENDING", "to": "pending"} in decision["query"]["fields"]["verdict"]["filter"]["valueMap"]


@pytest.mark.parametrize(
    "columns",
    (
        [{"path": "action", "label": "Decision"}, {"path": "created_at"}],
        [{"path": "step_name"}, {"path": "workflow_name", "label": "Workflow"}],
    ),
)
def test_dashboard_decision_columns_validate_against_selected_context(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    columns: list[dict[str, str]],
) -> None:
    """Installed rows widgets may declare ordered labels over flat Decision context."""

    del workflow_gate_tables, no_workflow_queue
    workflows_schema = importlib.import_module("angee.workflows.schema")
    parts = {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}
    schemas = GraphQLSchemas([SchemaAddon({"console": parts})])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    snapshot = {
        "widgets": [
            {
                "isArchived": False,
                "data": {
                    "shape": "rows",
                    "source": {
                        "resource": "workflows.Decision",
                        "fields": ["action", "workflow_name", "step_name", "created_at"],
                        "filter": {
                            "step_run.run.workflow.key": {"inList": ["review", "verification"]},
                            "verdict": {"exact": "pending"},
                        },
                    },
                },
                "options": {"columns": columns},
            },
            {
                "isArchived": False,
                "data": {
                    "shape": "rows",
                    "source": {
                        "resource": "workflows.WorkflowRun",
                        "fields": ["id", "workflow", "created_at", "status"],
                        "filter": {"workflow.key": {"inList": ["review", "verification"]}},
                    },
                },
                "options": {"columns": [{"path": "workflow", "label": "Workflow"}, {"path": "id", "label": "Run"}]},
            },
        ]
    }

    validate_dashboard_queries(snapshot)


@pytest.mark.parametrize(
    "columns",
    (
        [{"path": "workflow_name"}],
        [{"path": "action", "label": 7}],
        [{"path": "action", "label": ""}],
        [{"path": "action", "unknown": True}],
        [{"path": "action"}, {"path": "action"}],
        [{"path": ""}],
        [{}],
        ["action"],
        [],
        None,
        "action",
    ),
)
def test_dashboard_decision_columns_reject_unselected_or_malformed_columns(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    columns: Any,
) -> None:
    """A declared column must be a unique selected path with an optional string label."""

    del workflow_gate_tables, no_workflow_queue
    workflows_schema = importlib.import_module("angee.workflows.schema")
    parts = {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}
    schemas = GraphQLSchemas([SchemaAddon({"console": parts})])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    snapshot = {
        "widgets": [
            {
                "isArchived": False,
                "data": {"shape": "rows", "source": {"resource": "workflows.Decision", "fields": ["action"]}},
                "options": {"columns": columns},
            }
        ]
    }

    with pytest.raises(ValidationError, match=r"widgets\[0\]\.options\.columns"):
        validate_dashboard_queries(snapshot)


@pytest.mark.parametrize("shape", ("value", "series", "none"))
@pytest.mark.parametrize("archived", (False, True))
def test_dashboard_columns_require_active_rows_widgets(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    archived: bool,
) -> None:
    """Column declarations fail on active non-row widgets, including source-less actions."""

    del workflow_gate_tables, no_workflow_queue
    workflows_schema = importlib.import_module("angee.workflows.schema")
    parts = {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}
    schemas = GraphQLSchemas([SchemaAddon({"console": parts})])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    snapshot = {
        "widgets": [{"isArchived": archived, "data": {"shape": shape}, "options": {"columns": [{"path": "action"}]}}]
    }

    if archived:
        validate_dashboard_queries(snapshot)
    else:
        with pytest.raises(ValidationError, match="columns are only valid for row widgets"):
            validate_dashboard_queries(snapshot)


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
    assert "workflow_key" in decision_section
    assert "step_key" in decision_section
    assert "source_run_id: ID" in decision_section
    assert "\n  run_id:" not in decision_section
    assert "run_created_at" not in decision_section
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
                "input_binding": {"kind": "workflow_input", "path": []},
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
                "input_binding": {"kind": "workflow_input", "path": []},
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

    decision.verdict = workflow_models.Verdict.COMPLETED
    with system_context(reason="test generic Decision writes stay closed"):
        with pytest.raises(TypeError):
            decision.save(update_fields=["verdict"])
    _refresh_decision(decision)
    with pytest.raises(TypeError, match="DecisionManager"):
        type(decision).objects.filter(pk=decision.pk).update(verdict=workflow_models.Verdict.COMPLETED)
    decision.attempts += 1
    with pytest.raises(TypeError, match="DecisionManager"):
        type(decision).objects.bulk_update([decision], ["attempts"])

    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 0


@pytest.mark.parametrize("system_kind", ("override", ""))
def test_system_decision_context_uses_only_the_system_kind_for_its_step_label(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    system_kind: str,
) -> None:
    """System events have no step key and never substitute a journal primary key."""

    del workflow_gate_tables, no_workflow_queue
    with system_context(reason="test system Decision context projection"):
        workflow = Workflow.objects.create(name="System decision context")
        run = WorkflowRun.objects.create(workflow=workflow)
        step_run = StepRun.objects.create(run=run, system_kind=system_kind)
        decision = Decision.objects.create(step_run=step_run, action="review")
        projected = Decision.objects.with_context_projection().get(pk=decision.pk)

    assert projected._decision_workflow_name == workflow.name
    assert projected._decision_step_key is None
    assert projected._decision_step_name == system_kind


@pytest.mark.parametrize("surface, journal_reader", (("public", False), ("console", False), ("console", True)))
def test_decision_context_and_schema_query_count_stays_flat_for_three_rows(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    surface: str,
    journal_reader: bool,
) -> None:
    """Narrow context projections and independently readable journals batch without row fanout."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-schema-query-reader")
    viewer = _platform_admin("wdc-schema-query-admin") if journal_reader else assignee
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
                    "input_binding": {"kind": "workflow_input", "path": []},
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

    schema = _schema(surface)
    journal_selection = "step_run { id }" if surface == "console" else ""
    query = f"""
        query DecisionSchemas {{
          workflow_decisions(limit: 10, order_by: [{{ created_at: asc }}]) {{
            decision_schema
            workflow_key
            workflow_name
            step_key
            step_name
            created_at
            {journal_selection}
          }}
        }}
    """
    open_decisions(1, "One schema query row")
    with CaptureQueriesContext(connection) as one_row:
        one_data = result_data(_execute(schema, query, user=viewer))

    open_decisions(2, "Two more schema query rows")
    with CaptureQueriesContext(connection) as three_rows:
        three_data = result_data(_execute(schema, query, user=viewer))

    assert len(one_data["workflow_decisions"]) == 1
    assert len(three_data["workflow_decisions"]) == 3
    assert {row["workflow_name"] for row in three_data["workflow_decisions"]} == {
        "One schema query row",
        "Two more schema query rows",
    }
    assert {row["step_name"] for row in three_data["workflow_decisions"]} == {"Gate"}
    assert {row["step_key"] for row in three_data["workflow_decisions"]} == {"gate"}
    if surface == "console":
        assert all((row["step_run"] is not None) == journal_reader for row in three_data["workflow_decisions"])
    assert len(three_rows.captured_queries) == len(one_row.captured_queries)
    assert sum(f'FROM "{Decision._meta.db_table}"' in query["sql"] for query in three_rows.captured_queries) == 1
    assert "rebac_permissionauditevent" not in " ".join(query["sql"].lower() for query in three_rows.captured_queries)


def test_workflow_subject_history_batches_context_and_guarded_journals(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One and three history groups use the same query count for selected related rows."""

    del workflow_gate_tables, no_workflow_queue
    viewer = _platform_admin("wdc-history-batch-admin")
    with system_context(reason="test history subject"):
        subject = Workflow.objects.create(name="History subject")
    gate_workflow = workflow_with_steps(
        name="History decisions",
        steps=({"key": "gate", "step_class": "gate", "config": _gate_config([viewer], None, [])},),
        edges=(),
    )
    failed_workflow = workflow_with_steps(
        name="History failures",
        steps=({"key": "failed", "step_class": "fixture"},),
        edges=(),
    )

    def fail(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        raise RuntimeError("retained history failure")

    monkeypatch.setattr(FixtureStep, "run", fail)

    def add_history_group() -> dict[str, str]:
        gate_run = engine.start(gate_workflow, subject=subject, actor=admit_workflow_actor(gate_workflow, viewer))
        advance_once(gate_run)
        execute_started(gate_run)
        gate_row = _step_run(gate_run, "gate")
        decision = _decision_for(gate_run, "gate")
        failed_run = engine.start(failed_workflow, subject=subject, actor=admit_workflow_actor(failed_workflow, viewer))
        advance_once(failed_run)
        execute_started(failed_run)
        failed_row = _step_run(failed_run, "failed")
        assert failed_row.status == workflow_models.StepRunStatus.FAILED
        with system_context(reason="test history child and retained attempt"):
            child = WorkflowRun.objects.create(
                workflow=gate_workflow,
                parent_step_run=gate_row,
                parent_relation="owned_call",
            )
            return {
                "gate_run": str(gate_run.sqid),
                "failed_run": str(failed_run.sqid),
                "gate_step": str(gate_row.sqid),
                "decision": str(decision.sqid),
                "failure": str(failed_row.sqid),
                "attempt": str(failed_row.current_attempt.sqid),
                "child": str(child.sqid),
            }

    schema = _schema("console")
    query = """
        query SubjectHistory($subject: WorkflowObjectRefInput!) {
          workflow_subject_history(subject: $subject) {
            runs { id }
            decisions { id workflow_name step_name step_run { id } }
            pending_decisions { id workflow_key step_key step_run { id } }
            failures { id run { id } step { key } current_attempt { id } }
            child_runs { parent_run_id run { id workflow { name } } }
          }
        }
    """
    variables = {"subject": {"subject_declaration": subject._meta.label, "id": str(subject.sqid)}}
    groups = [add_history_group()]
    result_data(_execute(schema, query, variables, user=viewer))
    with CaptureQueriesContext(connection) as one_group:
        first = result_data(_execute(schema, query, variables, user=viewer))["workflow_subject_history"]
    groups.extend((add_history_group(), add_history_group()))
    with CaptureQueriesContext(connection) as three_groups:
        all_rows = result_data(_execute(schema, query, variables, user=viewer))["workflow_subject_history"]

    assert len(first["runs"]) == 2
    assert len(all_rows["runs"]) == 6
    for field in ("decisions", "pending_decisions", "failures", "child_runs"):
        assert len(first[field]) == 1
        assert len(all_rows[field]) == 3
    assert {row["id"] for row in all_rows["runs"]} == {
        group[key] for group in groups for key in ("gate_run", "failed_run")
    }
    assert {row["step_run"]["id"] for row in all_rows["decisions"]} == {group["gate_step"] for group in groups}
    assert {row["workflow_name"] for row in all_rows["decisions"]} == {gate_workflow.name}
    assert {row["step_name"] for row in all_rows["decisions"]} == {"Gate"}
    assert {row["id"] for row in all_rows["pending_decisions"]} == {group["decision"] for group in groups}
    assert {row["step_key"] for row in all_rows["pending_decisions"]} == {"gate"}
    assert {row["current_attempt"]["id"] for row in all_rows["failures"]} == {group["attempt"] for group in groups}
    assert {row["run"]["id"] for row in all_rows["failures"]} == {group["failed_run"] for group in groups}
    assert {row["step"]["key"] for row in all_rows["failures"]} == {"failed"}
    assert {row["parent_run_id"] for row in all_rows["child_runs"]} == {group["gate_run"] for group in groups}
    assert {row["run"]["id"] for row in all_rows["child_runs"]} == {group["child"] for group in groups}
    assert {row["run"]["workflow"]["name"] for row in all_rows["child_runs"]} == {gate_workflow.name}
    assert len(three_groups.captured_queries) == len(one_group.captured_queries)


@pytest.mark.parametrize("related_history", ("child", "failure"))
def test_workflow_subject_history_guards_independent_definition_reads(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    related_history: str,
) -> None:
    """Reading a run does not authorize joined workflow or step definitions."""

    del workflow_gate_tables, no_workflow_queue
    viewer = User.objects.create_user(username="wdc-history-run-reader")
    private_workflow = workflow_with_steps(name="Private definition", steps=({"key": "private"},), edges=())
    with system_context(reason="test independently scoped history definitions"):
        subject = Workflow.objects.create(name="Readable subject", created_by=viewer)
        run = WorkflowRun.objects.create(workflow=private_workflow, subject=subject, created_by=viewer)
        row = StepRun.objects.create(
            run=run,
            step=step_for(private_workflow, "private"),
            status=workflow_models.StepRunStatus.FAILED if related_history == "failure" else "scheduled",
        )
        if related_history == "child":
            WorkflowRun.objects.create(
                workflow=private_workflow,
                parent_step_run=row,
                parent_relation="owned_call",
                created_by=viewer,
            )
    query = """
        query SubjectHistory($subject: WorkflowObjectRefInput!) {
          workflow_subject_history(subject: $subject) {
            child_runs { run { id workflow { name } } }
            failures { id step { key } }
          }
        }
    """
    result = _execute(
        _schema("console"),
        query,
        {"subject": {"subject_declaration": subject._meta.label, "id": str(subject.sqid)}},
        user=viewer,
    )

    assert result.errors
    assert any(isinstance(error.original_error, PermissionDenied) for error in result.errors)


def test_decision_resources_scope_all_read_shapes_and_guard_journal_links(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Console decisions use act seats while public reads retain their broader scope."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-resource-assignee")
    requester = User.objects.create_user(username="wdc-resource-requester")
    stranger = User.objects.create_user(username="wdc-resource-stranger")
    admin = _platform_admin("wdc-resource-admin")
    decision = _opened_decision([assignee], requester)
    context_selection = """
        fragment DecisionContext on DecisionType {
          action
          workflow_key
          workflow_name
          step_key
          step_name
          created_at
        }
    """
    query = (
        """
        query DecisionReads($id: String!, $run: String!) {
          workflow_decisions(where: {step_run__run: {_eq: $run}}, limit: 10) {
            id
            source_run_id
            source_execution_id
            source_attempt_id
            ...DecisionContext
          }
          workflow_decisions_by_pk(id: $id) {
            id
            source_run_id
            source_execution_id
            source_attempt_id
            ...DecisionContext
          }
          workflow_decisions_aggregate { aggregate { count } }
        }
    """
        + context_selection
    )
    public = _schema("public")
    variables = {"id": str(decision.sqid), "run": str(decision.step_run.run.sqid)}

    assigned = result_data(_execute(public, query, variables, user=assignee))
    requester_read = result_data(_execute(public, query, variables, user=requester))
    denied = result_data(_execute(public, query, variables, user=stranger))
    privileged = result_data(_execute(public, query, variables, user=admin))
    console_query = (
        """
        query ConsoleDecision($id: String!, $workflowKey: String!) {
          workflow_decisions(
            where: {step_run__run__workflow__key: {_eq: $workflowKey}}
            limit: 10
          ) { id ...DecisionContext step_run { id } }
          workflow_decisions_by_pk(id: $id) { id ...DecisionContext step_run { id } }
          workflow_decisions_aggregate { aggregate { count } }
        }
    """
        + context_selection
    )
    console = _schema("console")
    console_variables = {
        "id": str(decision.sqid),
        "workflowKey": decision.step_run.run.workflow.key,
    }
    assigned_console = result_data(_execute(console, console_query, console_variables, user=assignee))
    requester_console = result_data(_execute(console, console_query, console_variables, user=requester))
    denied_console = result_data(_execute(console, console_query, console_variables, user=stranger))
    privileged_console = result_data(_execute(console, console_query, console_variables, user=admin))

    expected_context = {
        "action": decision.action,
        "workflow_key": decision.step_run.run.workflow.key,
        "workflow_name": decision.step_run.run.workflow.name,
        "step_key": decision.step_run.step.key,
        "step_name": decision.step_run.step.name,
        "created_at": decision.created_at.isoformat(),
    }
    assert assigned["workflow_decisions"] == [
        {
            **expected_context,
            "id": str(decision.sqid),
            "source_run_id": None,
            "source_execution_id": None,
            "source_attempt_id": None,
        }
    ]
    assert assigned["workflow_decisions_by_pk"] == assigned["workflow_decisions"][0]
    assert assigned["workflow_decisions_aggregate"]["aggregate"]["count"] == 1
    assert requester_read["workflow_decisions_by_pk"] == assigned["workflow_decisions_by_pk"]
    assert denied["workflow_decisions"] == []
    assert denied["workflow_decisions_by_pk"] is None
    assert denied["workflow_decisions_aggregate"]["aggregate"]["count"] == 0
    assert privileged["workflow_decisions_by_pk"]["source_run_id"] == str(decision.step_run.run.sqid)
    assert privileged["workflow_decisions_by_pk"]["source_execution_id"] == str(decision.step_run.sqid)
    assert privileged["workflow_decisions_by_pk"]["source_attempt_id"] == str(decision.suspension_attempt.sqid)
    expected_assigned_console = {
        **expected_context,
        "id": str(decision.sqid),
        "step_run": None,
    }
    assert assigned_console["workflow_decisions"] == [expected_assigned_console]
    assert assigned_console["workflow_decisions_by_pk"] == expected_assigned_console
    assert assigned_console["workflow_decisions_aggregate"]["aggregate"]["count"] == 1
    assert requester_console["workflow_decisions"] == []
    assert requester_console["workflow_decisions_by_pk"] is None
    assert requester_console["workflow_decisions_aggregate"]["aggregate"]["count"] == 0
    assert denied_console["workflow_decisions"] == []
    assert denied_console["workflow_decisions_by_pk"] is None
    assert denied_console["workflow_decisions_aggregate"]["aggregate"]["count"] == 0
    expected_privileged_console = {
        **expected_context,
        "id": str(decision.sqid),
        "step_run": {"id": str(decision.step_run.sqid)},
    }
    assert privileged_console["workflow_decisions"] == [expected_privileged_console]
    assert privileged_console["workflow_decisions_by_pk"] == expected_privileged_console
    assert privileged_console["workflow_decisions_aggregate"]["aggregate"]["count"] == 1


@pytest.mark.parametrize("surface", ("public", "console"))
def test_decide_mutation_uses_actor_scoped_act_permission_and_projects_context(
    workflow_gate_tables: None,
    no_workflow_queue: None,
    surface: str,
) -> None:
    """Authorized mutation results expose context without relying on queryset optimization."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-gql-assignee")
    stranger = User.objects.create_user(username="wdc-gql-stranger")
    decision = _opened_decision([assignee], None)
    schema = _schema(surface)
    mutation = """
        mutation Decide($decision: ID!, $verdict: DecisionVerb!, $payload: JSON) {
          decide(decision: $decision, verdict: $verdict, payload: $payload) {
            decision {
              verdict
              resolution
              workflow_key
              workflow_name
              step_key
              step_name
              created_at
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
    denied = _execute(schema, mutation, variables, user=stranger)
    assert denied.errors is not None

    data = result_data(_execute(schema, mutation, variables, user=assignee))
    assert data["decide"] == {
        "decision": {
            "verdict": "COMPLETED",
            "resolution": {"action": "complete"},
            "workflow_key": decision.step_run.run.workflow.key,
            "workflow_name": decision.step_run.run.workflow.name,
            "step_key": decision.step_run.step.key,
            "step_name": decision.step_run.step.name,
            "created_at": decision.created_at.isoformat(),
        },
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
                "input_binding": {"kind": "workflow_input", "path": []},
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
                "input_binding": {"kind": "workflow_input", "path": []},
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
    with system_context(reason="create deletable legacy decision for missing oracle"):
        legacy = Decision.objects.create(step_run=decision.step_run, action="legacy-oracle")
        missing_id = str(legacy.sqid)
        legacy.delete()
    existing = _execute(_schema(surface), mutation, {"decision": decision_id}, user=stranger)
    _refresh_decision(decision)
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 0
    missing = _execute(_schema(surface), mutation, {"decision": missing_id}, user=stranger)

    assert existing.errors is not None
    assert missing.errors is not None
    assert existing.data is None
    assert missing.data is None
    for result, supplied_id in ((existing, decision_id), (missing, missing_id)):
        message = f"Decision '{supplied_id}' was not found."
        assert [error.formatted for error in result.errors] == [{
            "message": str({"__all__": [message]}),
            "locations": [{"line": 3, "column": 11}],
            "path": ["decide"],
            "extensions": {
                "code": "VALIDATION", "validationErrors": {}, "formErrors": [message],
            },
        }]


def test_raw_delete_rejects_actor_hidden_retained_decision(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Raw SQL cannot evade retention through an actor scope that hides the row."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="raw-delete-assignee")
    stranger = User.objects.create_user(username="raw-delete-hidden-stranger")
    workflow = workflow_with_steps(
        steps=({"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])},),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    hidden = Decision.objects.with_actor(stranger).filter(pk=decision.pk).scoped()
    assert not hidden.exists()
    with pytest.raises(TypeError, match="Retained workflow Decisions"):
        hidden._raw_delete(using=hidden.db)

    with system_context(reason="actor-hidden raw-delete retention assertion"):
        retained = Decision.objects.get(pk=decision.pk)
    assert retained.suspension_attempt_id == decision.suspension_attempt_id
    assert retained.suspension_attempt_id is not None
    assert retained.declaration_index == decision.declaration_index
    assert retained.declaration_index is not None


def test_retained_decision_rejects_every_public_and_collector_delete(
    workflow_gate_tables: None,
    no_workflow_queue: None,
) -> None:
    """Suspension Decisions remain retained through instance, queryset, raw, and parent collectors."""

    del workflow_gate_tables, no_workflow_queue
    assignee = User.objects.create_user(username="wdc-delete-retained")
    workflow = workflow_with_steps(
        name="Retained decision deletion",
        steps=(({"key": "gate", "step_class": "gate", "config": _gate_config([assignee], None, [])}),),
        edges=(),
    )
    decision = _decision_for(_open_gate_run(workflow), "gate")
    with system_context(reason="retained Decision deletion regression"):
        legacy = Decision.objects.create(step_run=decision.step_run, action="legacy")
        for delete in (
            decision.delete,
            lambda: Decision.objects.filter(pk=decision.pk).delete(),
            lambda: Decision.objects.filter(pk__in=(legacy.pk, decision.pk)).delete(),
            lambda: Decision.objects.filter(pk=decision.pk)._raw_delete(using="default"),
        ):
            with pytest.raises(TypeError, match="Retained workflow Decisions"):
                delete()
        assert Decision.objects.filter(pk=decision.pk).exists()
        with pytest.raises((TypeError, ProtectedError)):
            decision.suspension_attempt.delete()
        legacy.delete()


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
    payload: dict[str, Any] = {"title": "Review"}
    config: dict[str, Any] = {
        "policy": policy,
        "action": "complete-review",
        "payload": payload,
        "slots": slots,
        "requester": str(to_subject_ref(requester)) if requester is not None else "",
        "escalation": [str(to_subject_ref(user)) for user in escalation],
        "max_attempts": max_attempts,
        "escalate_at": escalate_at.isoformat() if escalate_at is not None else "",
        "expires_at": expires_at.isoformat() if expires_at is not None else "",
    }
    if decision_schema is None:
        config["actions"] = [
            {
                "value": action,
                "label": action.title(),
                "verdict": verdict,
            }
            for action, verdict in (
                ("complete", "COMPLETE"),
                ("reject", "REJECT"),
                ("escalate", "ESCALATE"),
            )
        ]
    else:
        payload["__test_bound_decision_schema"] = decision_schema
        config["decision_schema"] = {"kind": "workflow_input", "path": ["decision_schema"]}
    return config


def _open_gate_run(workflow: Workflow, *, now: Any = None) -> Any:
    actor = User.objects.create_user(username=f"wdc-run-actor-{workflow.pk}")
    gate = step_for(workflow, "gate")
    payload = gate.config.get("payload", {})
    bound_schema_present = isinstance(payload, dict) and "__test_bound_decision_schema" in payload
    bound_schema = payload.get("__test_bound_decision_schema") if bound_schema_present else None
    run = engine.start(
        workflow,
        subject=None,
        actor=admit_workflow_actor(workflow, actor),
        input=JsonPresence(True, {"decision_schema": bound_schema}) if bound_schema_present else JsonPresence(),
    )
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


def _validate_json_resolution(schema: dict[str, Any], payload: Any) -> dict[str, Any]:
    decision = SimpleNamespace(form_schema=schema)
    return decision_actions.validate_decision_resolution(
        decision, payload, actor=None, verdict="completed"
    )


def test_tagged_action_reports_each_missing_nested_field_once() -> None:
    """Selected action constraints preserve precise paths without union summaries."""

    schema = _action_schema(
        actions=("reject", "complete"),
        required=("review",),
        properties={
            "review": {
                "type": "object",
                "required": ["approved", "note"],
                "properties": {"approved": {"type": "boolean"}, "note": {"type": "string"}},
            },
        },
    )
    with pytest.raises(ValidationError) as error:
        _validate_json_resolution(schema, {"action": "complete", "review": {}})
    assert error.value.message_dict == {
        "review.approved": ["This field is required."],
        "review.note": ["This field is required."],
    }


def test_tagged_action_preserves_local_references_into_its_branch() -> None:
    """Diagnostic projection keeps the retained document's reference targets intact."""

    schema = _action_schema(properties={"note": {"$ref": "#/oneOf/0/properties/note"}})
    schema["oneOf"][0]["properties"]["note"] = {"type": "string"}
    submitted = {"action": "complete", "note": "Reviewed"}
    assert _validate_json_resolution(schema, submitted) == submitted
    with pytest.raises(ValidationError) as error:
        _validate_json_resolution(schema, {"action": "complete", "note": 1})
    assert error.value.message_dict == {"note": ["1 is not of type 'string'"]}


def test_tagged_action_retains_referenced_union_constraints() -> None:
    """A referenced oneOf belongs to its own schema, independent of action indexes."""

    schema = _action_schema(
        actions=("reject", "escalate", "complete"), properties={"note": {"type": "string"}},
    )
    schema["$defs"] = {"extra": {"oneOf": [
        {"properties": {"note": {"const": "yes"}}},
        {"properties": {"note": {"const": "approved"}}},
    ]}}
    schema["$ref"] = "#/$defs/extra"
    submitted = {"action": "complete", "note": "yes"}
    assert _validate_json_resolution(schema, submitted) == submitted
    invalid = {"action": "complete", "note": "no"}
    with pytest.raises(ValidationError) as error:
        _validate_json_resolution(schema, invalid)
    assert error.value.message_dict == {
        "payload": [f"{invalid!r} is not valid under any of the given schemas"],
    }


@pytest.mark.parametrize("value", ["7", True, None])
def test_json_authored_integer_is_not_coerced(value: Any) -> None:
    schema = _action_schema(
        actions=("apply",),
        verdicts={"apply": "COMPLETE"},
        properties={"amount": {"type": "integer"}},
        required=("amount",),
    )
    with pytest.raises(ValidationError):
        _validate_json_resolution(schema, {"action": "apply", "amount": value})


def test_json_schema_keeps_nested_constraints_and_does_not_insert_defaults() -> None:
    schema = _action_schema(
        actions=("apply",),
        verdicts={"apply": "COMPLETE"},
        properties={
            "rows": {"type": "array", "items": {"$ref": "#/$defs/row"}, "minItems": 1},
            "optional": {"type": "string", "default": "suggestion"},
        },
        required=("rows",),
    )
    schema["$defs"] = {"row": {"type": "integer", "minimum": 2}}
    assert _validate_json_resolution(schema, {"action": "apply", "rows": [2]}) == {"action": "apply", "rows": [2]}
    for payload in ({"rows": []}, {"rows": [1]}, {"rows": ["2"]}, {"rows": [2], "unknown": True}):
        with pytest.raises(ValidationError):
            _validate_json_resolution(schema, {"action": "apply", **payload})
