"""Compatibility tests for built-in workflow operation configuration."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from rebac import system_context

from angee.workflows.configs import GateConfig, MapConfig, WaitConfig
from angee.workflows.steps import (
    GateStep,
    MapStep,
    WaitStep,
    _decision_specs_from_config,
    retry_policy_from_config,
)
from tests.workflows import Step, Workflow


def normalized_twice(step: type[WaitStep | GateStep | MapStep], config: dict[str, object]) -> dict[str, object]:
    """Assert typed normalization reaches a stable persisted representation."""

    first = step.normalize_config(config)
    assert step.normalize_config(first) == first
    return first


def test_wait_config_preserves_timer_and_legacy_scalar_retry() -> None:
    normalized = normalized_twice(
        WaitStep,
        {"until": "2030-01-02T03:04:05Z", "retry": {"max_attempts": 3, "backoff": 7}},
    )

    assert normalized["until"] == "2030-01-02T03:04:05Z"
    assert normalized["retry"] == {
        "max_attempts": 3,
        "backoff": {"wait": 7, "linear_wait": 0, "exponential_wait": 0},
    }
    assert retry_policy_from_config(normalized).wait == 7


def test_gate_config_preserves_dynamic_payload_and_normalizes_legacy_seats() -> None:
    legacy = {
        "policy": "all_success",
        "action": "approve-note",
        "assignees": ["auth/user:1", "auth/group:2#member"],
        "payload": {"nested": [1, {"kept": True}]},
        "requester": "auth/user:3",
        "escalation": ["auth/user:4"],
        "max_attempts": "2",
        "expires_at": "2030-01-02T03:04:05Z",
        "decision_schema": {"type": "object", "properties": {"reason": {"type": "string"}}},
        "retry": {"backoff": {"linear_wait": 4}},
    }
    normalized = normalized_twice(GateStep, legacy)

    assert normalized["slots"] == [
        {"assignees": ["auth/user:1"], "priority": 0, "requester": "", "escalation": None},
        {"assignees": ["auth/group:2#member"], "priority": 1, "requester": "", "escalation": None},
    ]
    assert normalized["payload"] == legacy["payload"]
    assert normalized["decision_schema"] == legacy["decision_schema"]
    assert normalized["max_attempts"] == 2


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
    assert projection.value["slots"] == [
        {"assignees": ["auth/user:1"], "priority": 0, "requester": "", "escalation": None}
    ]
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
        {"target_step": "publish", "items": items, "min_success": "0.5", "all_must_succeed": False},
    )

    assert normalized["items"] == items
    assert normalized["min_success_ratio"] == 0.5
    assert "min_success" not in normalized

    both = normalized_twice(
        MapStep,
        {"target_step": "publish", "items": items, "min_success_ratio": 0.75, "min_success": 0.25},
    )
    assert both["min_success_ratio"] == 0.75


@pytest.mark.parametrize(
    ("step", "config"),
    [
        (WaitStep, {"until": "not-a-date"}),
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

    wait = WaitStep.config_form_spec()
    gate = GateStep.config_form_spec()
    mapped = MapStep.config_form_spec()
    assert wait is not None and wait["required"] == ["until"]
    assert wait["properties"]["retry"]["nullable"] is True
    assert gate is not None and gate["properties"]["slots"]["widget"] == "list"
    assert gate["properties"]["slots"]["minItems"] == 1
    assert gate["properties"]["payload"]["widget"] == "json"
    assert gate["properties"]["decision_schema"]["widget"] == "json"
    assert mapped is not None and mapped["properties"]["items"] == {
        "type": "any",
        "widget": "json",
        "label": "Items",
        "presenceRequired": True,
    }


@pytest.mark.django_db(transaction=True)
def test_reapplying_legacy_config_does_not_publish_a_new_version(workflow_tables: None) -> None:
    """Stable normalization keeps a no-op resource-style reload from versioning again."""

    del workflow_tables
    legacy = {"until": "2030-01-02T03:04:05Z", "retry": {"max_attempts": 2, "backoff": 3}}
    with system_context(reason="test stable workflow config normalization"):
        workflow = Workflow.objects.create(name="Stable typed config")
        step = Step.objects.create(
            workflow=workflow,
            key="wait",
            name="Wait",
            step_class="wait",
            config=legacy,
            is_entry=True,
        )
        first = workflow.publish_if_changed()
        assert first is not None

        step.config = legacy
        step.save(update_fields={"config", "updated_at"})

        assert workflow.publish_if_changed() is None
    assert step.config == WaitStep.normalize_config(legacy)
