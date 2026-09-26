"""Native JSON Schema applicators retain Decision relation authorization."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from pydantic import BaseModel, Field

from angee.workflows import decision_actions


@pytest.mark.parametrize("applicator", ["allOf", "anyOf", "oneOf"])
@pytest.mark.parametrize("permitted", [True, False])
def test_relation_permission_follows_references_applicators_and_nested_arrays(
    monkeypatch: pytest.MonkeyPatch,
    applicator: str,
    permitted: bool,
) -> None:
    actor = object()
    checked: list[tuple[Any, Any, Any]] = []
    relation = {"resource": "parties.Party", "permission": "write"}
    schema: dict[str, Any] = {
        "$defs": {"record": {"type": "string", "relation": relation}},
        "type": "object",
        "properties": {
            "selection": {
                applicator: [
                    {
                        "type": "array",
                        "items": {"type": "object", "properties": {"record": {"$ref": "#/$defs/record"}}},
                    },
                ],
            },
        },
    }
    if applicator != "allOf":
        schema["properties"]["selection"][applicator].append({"type": "null"})

    def authorize(spec: Any, value: Any, supplied_actor: Any) -> str | None:
        checked.append((spec, value, supplied_actor))
        return None if permitted else "Record is outside the actor's permission scope."

    monkeypatch.setattr(decision_actions, "_relation_error", authorize)
    payload = {"selection": [{"record": "pty_123"}]}
    if permitted:
        decision_actions._validate_relation_fields(schema, payload, actor)
    else:
        with pytest.raises(ValidationError) as error:
            decision_actions._validate_relation_fields(schema, payload, actor)
        assert "selection.0.record" in error.value.message_dict
    assert checked == [(relation, "pty_123", actor)]


def test_nonmatching_branch_does_not_deny_permitted_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def authorize(relation: dict[str, str], value: str, actor: Any) -> str | None:
        del value, actor
        return "Denied wrong branch" if relation["permission"] == "write" else None

    monkeypatch.setattr(decision_actions, "_relation_error", authorize)
    schema = {
        "type": "object",
        "properties": {"mode": {"type": "string"}, "record": {"type": "string"}},
        "oneOf": [
            {"properties": {"mode": {"const": "edit"}, "record": {"relation": {"permission": "write"}}}},
            {"properties": {"mode": {"const": "view"}, "record": {"relation": {"permission": "read"}}}},
        ],
    }
    payload = {"mode": "view", "record": "pty_123"}
    decision_actions._validate_relation_fields(schema, payload, object())


def test_structural_validation_never_coerces_relation_values(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = decision_actions.build_decision_action(
        actions=(
            decision_actions.ReviewAction(value="approve", label="Approve", verdict="COMPLETE", fields=("record",)),
        ),
        properties={"record": {"type": "string", "relation": {"resource": "parties.Party"}}},
    ).decision_schema
    monkeypatch.setattr(
        decision_actions,
        "_relation_error",
        lambda *args, **kwargs: pytest.fail("Malformed relation reached authorization before structural validation."),
    )
    with pytest.raises(ValidationError, match="not of type"):
        decision_actions.validate_decision_resolution(
            SimpleNamespace(form_schema=schema),
            {"action": "approve", "record": 123},
            actor=object(),
            verdict="completed",
        )


def test_rebac_relation_without_actor_scope_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    model = object()
    monkeypatch.setattr(decision_actions.apps, "get_model", lambda *args, **kwargs: model)
    monkeypatch.setattr(decision_actions, "read_scoped_queryset", lambda *args, **kwargs: None)
    monkeypatch.setattr(decision_actions, "model_resource_type", lambda *args, **kwargs: "parties/party")
    monkeypatch.setattr(
        decision_actions,
        "instance_from_public_id",
        lambda *args, **kwargs: pytest.fail("An unscoped model lookup must not occur."),
    )
    assert (
        decision_actions._relation_error({"resource": "parties.Party"}, "pty_123", None)
        == "Relation value must reference a permitted record."
    )


def test_explicit_nested_dialect_keeps_relation_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = {
        "$defs": {
            "record": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "string",
                "relation": {"resource": "parties.Party"},
            }
        },
        "type": "object",
        "properties": {"record": {"$ref": "#/$defs/record"}},
    }
    monkeypatch.setattr(decision_actions, "_relation_error", lambda *args, **kwargs: "Denied retained record")
    with pytest.raises(ValidationError, match="Denied retained record"):
        decision_actions._validate_relation_fields(schema, {"record": "pty_123"}, object())
    assert "$schema" in schema["$defs"]["record"]


def test_schema_annotations_are_not_mistaken_for_subschemas() -> None:
    schema = {
        "type": "object",
        "default": {"$schema": "ordinary domain data"},
        "properties": {"name": {"type": "string", "example": {"$schema": "ordinary domain data"}}},
    }
    assert decision_actions._decision_validation_schema(schema) == schema


def test_nested_dialect_changes_and_remote_references_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Draft 2020-12 throughout"):
        decision_actions._validate_relation_fields(
            {
                "type": "object",
                "properties": {
                    "record": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "string"},
                },
            },
            {"record": "pty_123"},
            object(),
        )
    with pytest.raises(ValidationError, match="inside the retained schema"):
        decision_actions._validate_relation_fields(
            {
                "type": "object",
                "properties": {"record": {"$ref": "https://example.invalid/review-schema"}},
            },
            {"record": "pty_123"},
            object(),
        )


@pytest.mark.parametrize("permitted", [True, False])
def test_python_authored_contract_retains_native_coercion_and_relation_authorization(
    monkeypatch: pytest.MonkeyPatch,
    permitted: bool,
) -> None:
    class Selection(BaseModel):
        record: str = Field(alias="selected_record", json_schema_extra={"relation": {"resource": "parties.Party"}})
        count: int

    actor = object()
    seen: list[tuple[Any, Any]] = []

    def authorize(relation: Any, value: Any, supplied_actor: Any) -> str | None:
        del relation
        seen.append((value, supplied_actor))
        return None if permitted else "Record is outside the actor's permission scope."

    monkeypatch.setattr(decision_actions, "_relation_error", authorize)
    step = SimpleNamespace(resolve_impl=lambda name: SimpleNamespace(decision_schema=Selection))
    decision = SimpleNamespace(form_schema=None, step_run=SimpleNamespace(step_id=1, step=step))
    payload = {"selected_record": "pty_123", "count": "7"}
    if permitted:
        assert decision_actions.validate_decision_resolution(
            decision, payload, actor=actor, verdict="completed"
        ) == {"record": "pty_123", "count": 7}
    else:
        with pytest.raises(ValidationError, match="outside the actor"):
            decision_actions.validate_decision_resolution(
                decision, payload, actor=actor, verdict="completed"
            )
    assert seen == [("pty_123", actor)]
