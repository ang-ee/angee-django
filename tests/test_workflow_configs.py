"""Tests for canonical built-in workflow operation configuration."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from rebac import system_context

from angee.workflows.attempts import RecoveryMode
from angee.workflows.configs import EmitConfig, GateConfig, JoinContinuationConfig, MapConfig, WaitConfig
from angee.workflows.models import check_database_command_replay_declarations
from angee.workflows.steps import (
    CallWorkflow,
    EmitStep,
    GateStep,
    JoinContinuation,
    MapStep,
    StepExecutionMode,
    StepImpl,
    WaitStep,
    _decision_specs_from_config,
    retry_policy_from_config,
)
from tests.workflows import Step, Workflow


def normalized_twice(step: type[StepImpl], config: dict[str, object]) -> dict[str, object]:
    """Assert typed normalization reaches a stable persisted representation."""

    first = step.normalize_config(config)
    assert step.normalize_config(first) == first
    return first


def test_wait_config_preserves_timer_and_retry_backoff() -> None:
    normalized = normalized_twice(
        WaitStep,
        {"until": "2030-01-02T03:04:05Z", "retry": {"max_attempts": 3, "backoff": {"wait": 7}}},
    )

    assert normalized["until"] == "2030-01-02T03:04:05Z"
    assert normalized["retry"] == {
        "max_attempts": 3,
        "backoff": {"wait": 7, "linear_wait": 0, "exponential_wait": 0},
    }
    assert retry_policy_from_config(normalized).wait == 7


def test_gate_config_preserves_dynamic_payload_and_defaults_seat_priorities() -> None:
    config = {
        "policy": "all_success",
        "action": "approve-note",
        "slots": [{"assignees": ["auth/user:1"]}, {"assignees": ["auth/group:2#member"]}],
        "payload": {"nested": [1, {"kept": True}]},
        "requester": "auth/user:3",
        "escalation": ["auth/user:4"],
        "max_attempts": "2",
        "expires_at": "2030-01-02T03:04:05Z",
        "decision_schema": {"type": "object", "properties": {"reason": {"type": "string"}}},
        "retry": {"backoff": {"linear_wait": 4}},
    }
    normalized = normalized_twice(GateStep, config)

    assert [slot["assignees"] for slot in normalized["slots"]] == [
        ["auth/user:1"],
        ["auth/group:2#member"],
    ]
    assert [slot["priority"] for slot in normalized["slots"]] == [0, 1]
    assert normalized["payload"] == config["payload"]
    assert normalized["decision_schema"] == config["decision_schema"]
    assert normalized["max_attempts"] == 2


def test_static_gate_actions_are_stored_as_declarations_and_derived_on_admission() -> None:
    normalized = normalized_twice(
        GateStep,
        {
            "action": "approve-note",
            "slots": [{"assignees": ["auth/user:1"]}],
            "actions": [
                {"value": "approve", "label": "Publish", "verdict": "COMPLETE", "variant": "primary"},
                {
                    "value": "reject",
                    "label": "Reject",
                    "verdict": "REJECT",
                    "variant": "destructive",
                    "confirm": "Reject this note?",
                },
            ],
            "properties": {"reason": {"type": "string", "title": "Reason"}},
        },
    )

    assert normalized["decision_schema"] == {}
    schema = GateConfig.model_validate(normalized).admission_decision_schema()
    assert schema["properties"]["action"]["enum"] == ["approve", "reject"]
    assert schema["properties"]["action"]["options"][1]["confirm"] == "Reject this note?"
    assert [branch["properties"]["action"]["const"] for branch in schema["oneOf"]] == [
        "approve",
        "reject",
    ]
    assert all(branch["required"] == ["action"] for branch in schema["oneOf"])
    assert all("reason" in branch["properties"] for branch in schema["oneOf"])


@pytest.mark.parametrize(
    "config",
    [
        {
            "action": "approve-note",
            "slots": [{"assignees": ["auth/user:1"]}],
            "decision_schema": {"oneOf": [{"type": "object"}]},
        },
        {
            "action": "approve-note",
            "slots": [
                {
                    "assignees": ["auth/user:1"],
                    "decision_schema": {"oneOf": [{"type": "object"}]},
                }
            ],
        },
    ],
)
def test_static_gate_rejects_hand_written_one_of(config: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="hand-written oneOf"):
        GateStep.validate_config(config)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object"},
        {"kind": "workflow_input", "path": ["decision_schema"]},
    ],
)
def test_static_gate_actions_cannot_mix_with_another_schema_owner(schema: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        GateStep.validate_config(
            {
                "action": "approve-note",
                "slots": [{"assignees": ["auth/user:1"]}],
                "decision_schema": schema,
                "actions": [
                    {"value": "approve", "label": "Approve", "verdict": "COMPLETE"},
                ],
            }
        )


def test_join_and_emit_configs_retain_declared_contracts() -> None:
    join = normalized_twice(
        JoinContinuation,
        {
            "child_id_path": ["continuation_id"],
            "expected_starter_class": "start_continuation",
            "expected_output_schema": {
                "type": "object",
                "required": ["invoice_id"],
                "properties": {"invoice_id": {"type": "string"}},
            },
            "expected_subject": "accounting.invoice",
            "expected_outcomes": ["completed"],
            "reconcile_after": 45,
        },
    )
    emitted = normalized_twice(
        EmitStep,
        {
            "output_schema": {
                "type": "object",
                "required": ["workflow_id"],
                "properties": {"workflow_id": {"type": "string"}},
            },
            "outcome": "accepted",
            "artifacts": [],
        },
    )

    assert join["child_id_path"] == ["continuation_id"]
    assert join["reconcile_after"] == 45
    assert emitted["outcome"] == "accepted"


def test_emit_artifact_paths_must_be_guaranteed_public_id_strings() -> None:
    valid = {
        "output_schema": {
            "type": "object",
            "required": ["user_id"],
            "properties": {"user_id": {"type": "string"}},
        },
        "artifacts": [{"model": "auth.User", "id_path": ["user_id"], "label": "Accepted user"}],
    }
    EmitStep.validate_config(valid)

    invalid = {
        **valid,
        "output_schema": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
        },
    }
    with pytest.raises(ValidationError, match="guaranteed string"):
        EmitStep.validate_config(invalid)


def test_emit_artifact_decimal_indices_retain_schema_presence_checks() -> None:
    config = {
        "output_schema": {
            "type": "object",
            "required": ["0"],
            "properties": {"0": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
        },
        "artifacts": [{"model": "auth.User", "id_path": ["0", "0"], "label": "Accepted user"}],
    }
    assert normalized_twice(EmitStep, config)["artifacts"][0]["id_path"] == ["0", "0"]
    config["output_schema"]["properties"]["0"]["minItems"] = 0
    with pytest.raises(ValidationError, match="guaranteed string"):
        EmitStep.validate_config(config)


@pytest.mark.parametrize("path", [[], [""], ["children", 0], ["children", True]])
def test_join_config_requires_nonempty_string_path_segments(path: list[object]) -> None:
    with pytest.raises(ValidationError, match="child_id_path"):
        JoinContinuation.validate_config({
            "child_id_path": path,
            "expected_starter_class": "start_continuation",
            "expected_output_schema": {"type": "object"},
            "expected_outcomes": ["completed"],
        })


def test_database_command_replay_requires_an_explicit_operation_declaration() -> None:
    class UndeclaredDatabaseCommand(StepImpl):
        execution_mode = StepExecutionMode.DATABASE_COMMAND

    assert UndeclaredDatabaseCommand.recovery_capability(attempt=object()).mode is None
    assert CallWorkflow.recovery_capability(attempt=object()).mode is RecoveryMode.FRESH
    assert JoinContinuation.recovery_capability(attempt=object()).mode is RecoveryMode.FRESH
    assert EmitStep.recovery_capability(attempt=object()).mode is RecoveryMode.FRESH


def test_database_command_system_check_warns_only_for_an_implicit_recovery_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UndeclaredDatabaseCommand(StepImpl):
        key = "undeclared_command"
        execution_mode = StepExecutionMode.DATABASE_COMMAND

    monkeypatch.setattr(
        "angee.workflows.models.resolve_all_impl_classes",
        lambda *args, **kwargs: (UndeclaredDatabaseCommand,),
    )

    warnings = check_database_command_replay_declarations()

    assert [warning.id for warning in warnings] == ["angee.workflows.W001"]
    assert warnings[0].obj is UndeclaredDatabaseCommand


def test_gate_slot_escalation_preserves_inherited_and_explicit_empty_meanings() -> None:
    normalized = GateStep.normalize_config(
        {
            "action": "approve-note",
            "escalation": ["auth/user:2"],
            "slots": [
                {"assignees": ["auth/user:1"]},
                {"assignees": ["auth/user:3"], "escalation": []},
            ],
        }
    )

    decisions = _decision_specs_from_config(normalized)
    assert decisions[0].escalation == ("auth/user:2",)
    assert decisions[1].escalation == ()


def test_gate_config_preserves_binding_nodes_for_runtime_admission() -> None:
    slots = {"kind": "workflow_input", "path": ["review", "slots"]}
    payload = {"kind": "workflow_input", "path": ["review", "payload"]}
    clean = {"kind": "workflow_input", "path": ["review", "clean"]}

    normalized = normalized_twice(
        GateStep,
        {
            "policy": "all_done",
            "action": "review_invoice",
            "slots": slots,
            "payload": payload,
            "clean": clean,
            "resume": True,
        },
    )

    assert normalized["slots"] == slots
    assert normalized["payload"] == payload
    assert normalized["clean"] == clean
    assert normalized["policy"] == "all_done"
    assert normalized["resume"] is True


def test_gate_config_reserves_kind_only_for_closed_binding_discriminators() -> None:
    """A domain ``kind`` fails clearly instead of entering the binding parser."""

    with pytest.raises(ValidationError, match="top-level key 'kind' is reserved"):
        GateStep.normalize_config(
            {
                "action": "review_invoice",
                "slots": [{"assignees": ["auth/user:1"]}],
                "payload": {"kind": "invoice", "id": "invoice-1"},
            }
        )


@pytest.mark.django_db(transaction=True)
def test_step_canonical_config_projects_legacy_gate_without_rewriting_row(workflow_tables: None) -> None:
    """The editor reads canonical slots while an existing definition stays untouched."""

    del workflow_tables
    legacy = {"action": "approve", "slots": [{"assignee": "auth/user:1"}]}
    with system_context(reason="test legacy gate read projection"):
        workflow = Workflow.objects.create(name="Legacy gate")
        step = Step.objects.create(
            workflow=workflow,
            key="gate",
            name="Gate",
            step_class="gate",
            config=legacy,
            is_entry=True,
        )
        models.QuerySet.update(Step._base_manager.filter(pk=step.pk), config=legacy)
        step.refresh_from_db()

    projection = step.config_projection()
    assert projection.value["slots"][0]["assignees"] == ["auth/user:1"]
    assert projection.value["slots"][0]["priority"] == 0
    assert projection.errors == {}
    assert step.config == legacy

    invalid = {"action": "approve", "slots": [{"assignee": ""}]}
    with system_context(reason="test invalid legacy gate read projection"):
        models.QuerySet.update(Step._base_manager.filter(pk=step.pk), config=invalid)
        step.refresh_from_db()
    projection = step.config_projection()
    assert projection.value == invalid
    assert "config.slots.0.assignees.0" in projection.errors


@pytest.mark.parametrize("items", ["subject.tags", [{"id": 1}, "literal"]])
def test_map_config_preserves_expression_and_literal_item_variants(items: object) -> None:
    normalized = normalized_twice(
        MapStep,
        {"target_step": "publish", "items": items, "min_success_ratio": "0.5", "all_must_succeed": False},
    )

    assert normalized["items"] == items
    assert normalized["min_success_ratio"] == 0.5
    assert "min_success" not in normalized


@pytest.mark.parametrize(
    ("step", "config"),
    [
        (WaitStep, {"until": "not-a-date"}),
        (WaitStep, {"until": "2030-01-02T03:04:05Z", "retry": {"backoff": 7}}),
        (WaitStep, {"until": "2030-01-02T03:04:05Z", "retry": ""}),
        (WaitStep, {"until": "2030-01-02T03:04:05Z", "retry": False}),
        (GateStep, {"action": "approve", "assignees": ["auth/user:1"]}),
        (MapStep, {"target_step": "publish", "items": [1], "min_success": 0.5}),
        (MapStep, {"target_step": "publish", "items": [1], "min_success_ratio": 0.75, "min_success": 0.25}),
        (GateStep, {"action": "approve", "slots": []}),
        (MapStep, {"target_step": "publish", "items": []}),
        (WaitStep, {"until": "2030-01-02T03:04:05Z", "unknown": True}),
    ],
)
def test_typed_builtin_configs_reject_invalid_or_unknown_values(
    step: type[WaitStep | GateStep | MapStep], config: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        step.validate_config(config)


def test_builtin_operations_own_their_typed_models() -> None:
    assert WaitStep.config_model is WaitConfig
    assert GateStep.config_model is GateConfig
    assert MapStep.config_model is MapConfig
    assert JoinContinuation.config_model is JoinContinuationConfig
    assert EmitStep.config_model is EmitConfig

    wait = WaitStep.config_form_spec()
    gate = GateStep.config_form_spec()
    mapped = MapStep.config_form_spec()
    assert wait is not None and wait["required"] == ["until"]
    assert wait["properties"]["retry"]["nullable"] is True
    assert gate is not None and gate["properties"]["slots"]["widget"] == "json"
    assert gate["properties"]["payload"]["widget"] == "json"
    assert gate["properties"]["decision_schema"]["widget"] == "json"
    assert mapped is not None and mapped["properties"]["items"] == {
        "type": "any",
        "widget": "json",
        "label": "Items",
        "presenceRequired": True,
    }


@pytest.mark.django_db(transaction=True)
def test_reapplying_canonical_config_does_not_publish_a_new_version(workflow_tables: None) -> None:
    """Stable normalization keeps a no-op resource-style reload from versioning again."""

    del workflow_tables
    config = {"until": "2030-01-02T03:04:05Z", "retry": {"max_attempts": 2, "backoff": {"wait": 3}}}
    with system_context(reason="test stable workflow config normalization"):
        workflow = Workflow.objects.create(name="Stable typed config")
        step = Step.objects.create(
            workflow=workflow,
            key="wait",
            name="Wait",
            step_class="wait",
            config=config,
            is_entry=True,
        )
        first = workflow.publish_if_changed()
        assert first is not None

        step.config = config
        step.save(update_fields={"config", "updated_at"})

        assert workflow.publish_if_changed() is None
    assert step.config == WaitStep.normalize_config(config)
