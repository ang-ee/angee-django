"""Frozen decision form contracts, independent of persistence and execution."""

import json
from datetime import date
from typing import Annotated, Literal

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, WithJsonSchema

from angee.base.jsonschema import validation_issues
from angee.decisions.contracts import DecisionRequest
from angee.decisions.forms import Action, Relation, RelationCandidate, compile_form, relation_candidates, validate_form
from angee.decisions.states import Verdict


class Complete(Action, value="complete", label="Complete", verdict=Verdict.COMPLETED):
    note: str = Field(min_length=3)


class Reject(Action, value="reject", label="Reject", verdict=Verdict.REJECTED):
    reason: str


def test_action_forms_compile_to_closed_tagged_schema():
    schema = compile_form([Complete, Reject])
    Draft202012Validator.check_schema(schema)
    assert schema["discriminator"] == {"propertyName": "action"}
    assert schema["properties"]["action"] == {
        "type": "string", "enum": ["complete", "reject"], "options": [
            {"value": "complete", "label": "Complete", "verdict": "completed"},
            {"value": "reject", "label": "Reject", "verdict": "rejected"},
        ],
    }
    assert all(branch["additionalProperties"] is False for branch in schema["oneOf"])
    verdict, resolution = validate_form(schema, "complete", {"note": "ready"})
    assert verdict == Verdict.COMPLETED
    assert resolution == {"action": "complete", "note": "ready"}
    assert validate_form(schema, "reject", {"reason": "needs editing"})[0] == Verdict.REJECTED


@pytest.mark.parametrize(("action", "values", "field"), [
    ("missing", {}, "action"),
    ("complete", {}, "note"),
    ("complete", {"note": "a"}, "note"),
    ("complete", {"note": 12}, "note"),
    ("complete", {"note": "ready", "other": 1}, "other"),
    ("complete", {"note": "ready", "action": "reject"}, "action"),
    ("complete", [], "__all__"),
])
def test_invalid_submissions_report_fields(action, values, field):
    with pytest.raises(ValidationError) as error:
        validate_form(compile_form([Complete, Reject]), action, values)
    assert field in error.value.message_dict


def test_admitted_defaults_and_annotations_are_copied_and_readonly_is_enforced():
    initial = {"complete": {"note": "frozen note"}}
    refine = {"complete": {"note": {"readOnly": True, "title": "Document note"}}}
    schema = compile_form([Complete], initial=initial, refine=refine)
    initial["complete"]["note"] = "changed"
    refine["complete"]["note"]["readOnly"] = False
    field = schema["oneOf"][0]["properties"]["note"]
    assert field["default"] == field["const"] == "frozen note"
    assert field["title"] == "Document note"
    validate_form(schema, "complete", {"note": "frozen note"})
    assert validate_form(schema, "complete", {})[1]["note"] == "frozen note"
    with pytest.raises(ValidationError) as error:
        validate_form(schema, "complete", {"note": "changed"})
    assert "note" in error.value.message_dict


def test_nested_local_references_and_readonly_row_values():
    class Line(BaseModel):
        key: str = Field(json_schema_extra={"readOnly": True})
        note: str

    class Edit(Action, value="edit", label="Edit", verdict=Verdict.COMPLETED):
        lines: list[Line] = Field(min_length=2, max_length=2, json_schema_extra={"widget": "rows"})

    lines = [{"key": "a", "note": "first"}, {"key": "b", "note": "second"}]
    schema = compile_form([Edit], initial={"edit": {"lines": lines}})
    assert "$defs" not in schema["oneOf"][0]
    changed = [{"key": "a", "note": "edited"}, {"key": "b", "note": "second"}]
    validate_form(schema, "edit", {"lines": changed})
    changed[1]["key"] = "a"
    with pytest.raises(ValidationError) as error:
        validate_form(schema, "edit", {"lines": changed})
    assert "lines.1.key" in error.value.message_dict


def test_declared_readonly_default_is_filled_when_omitted():
    class Document(Action, value="document", label="Document", verdict=Verdict.COMPLETED):
        revision: int = Field(default=3, json_schema_extra={"readOnly": True})

    schema = compile_form([Document])
    validate_form(schema, "document", {"revision": 3})
    assert validate_form(schema, "document", {})[1] == {"action": "document", "revision": 3}


def test_relation_candidates_are_frozen_and_constrain_submissions():
    class Select(Action, value="select", label="Select", verdict=Verdict.COMPLETED):
        document: Annotated[str, Relation("notes.Document")]

    schema = compile_form([Select], refine={"select": {"document": {"options": [
        {"value": "document-a", "label": "Document A"},
        {"value": "document-b", "label": "Document B"},
    ]}}})
    field = schema["oneOf"][0]["properties"]["document"]
    assert field["relation"] == {"resource": "notes.Document", "permission": "read"}
    assert field["enum"] == ["document-a", "document-b"]
    branch = schema["oneOf"][0]
    assert not validation_issues(branch, {"action": "select", "document": "document-a"})
    assert "document" in validation_issues(branch, {"action": "select", "document": "document-c"})
    with pytest.raises(ValidationError) as error:
        validate_form(schema, "select", {"document": "document-a"})
    assert error.value.message_dict == {"document": ["A relation value requires an actor."]}


def test_date_formats_are_asserted_by_the_frozen_schema():
    class Date(Action, value="date", label="Date", verdict=Verdict.COMPLETED):
        written_on: date

    schema = compile_form([Date])
    validate_form(schema, "date", {"written_on": "2026-09-28"})
    with pytest.raises(ValidationError) as error:
        validate_form(schema, "date", {"written_on": "2026-02-30"})
    assert "written_on" in error.value.message_dict


@pytest.mark.parametrize(("initial", "refine"), [
    ({"unknown": {}}, {}),
    ({"complete": {"unknown": "note"}}, {}),
    ({"complete": {"note": "x"}}, {}),
    ({}, {"unknown": {}}),
    ({}, {"complete": {"unknown": {"title": "Unknown"}}}),
    ({}, {"complete": {"note": {"minLength": 10}}}),
    ({}, {"complete": {"note": {"readOnly": True}}}),
    ({}, {"complete": {"note": {"readOnly": "yes"}}}),
    ({}, {"complete": {"note": {"widget": 1}}}),
    ({}, {"complete": {"note": {"options": [{"value": "x", "label": "Short"}]}}}),
])
def test_invalid_runtime_refinements_fail_at_admission(initial, refine):
    with pytest.raises(ValidationError):
        compile_form([Complete], initial=initial, refine=refine)


def test_initial_cannot_replace_a_declared_constant():
    class Fixed(Action, value="fixed", label="Fixed", verdict=Verdict.COMPLETED):
        note: Literal["fixed"] = Field(json_schema_extra={"readOnly": True})

    with pytest.raises(ValidationError):
        compile_form([Fixed], initial={"fixed": {"note": "changed"}})


@pytest.mark.parametrize("extra", [{"unknown": True}, {"format": "unknown-format"}])
def test_unsupported_schema_declarations_fail_at_admission(extra):
    class Unsupported(Action, value="unsupported", label="Unsupported", verdict=Verdict.COMPLETED):
        note: str = Field(json_schema_extra=extra)

    with pytest.raises(ImproperlyConfigured):
        compile_form([Unsupported])


@pytest.mark.parametrize("reference", ["https://example.invalid/schema", "#/$defs/missing"])
def test_unresolvable_references_fail_at_admission(reference):
    class Unsupported(Action, value="unsupported", label="Unsupported", verdict=Verdict.COMPLETED):
        note: Annotated[str, WithJsonSchema({"$ref": reference})]

    with pytest.raises(ImproperlyConfigured):
        compile_form([Unsupported])


def test_recursive_forms_fail_at_admission():
    class Tree(BaseModel):
        children: list["Tree"] = Field(default_factory=list)

    class Recursive(Action, value="recursive", label="Recursive", verdict=Verdict.COMPLETED):
        tree: Tree

    with pytest.raises(ImproperlyConfigured):
        compile_form([Recursive])


def test_duplicate_and_empty_actions_are_rejected():
    for actions in ([], [Complete, Complete]):
        with pytest.raises(ValidationError):
            compile_form(actions)


def test_nonfinite_values_are_not_json_form_values():
    class Number(Action, value="number", label="Number", verdict=Verdict.COMPLETED):
        number: float

    with pytest.raises(ValidationError):
        validate_form(compile_form([Number]), "number", {"number": float("nan")})


def test_nested_declared_fields_are_closed():
    class Note(BaseModel):
        text: str

    class Write(Action, value="write", label="Write", verdict=Verdict.COMPLETED):
        note: Note

    with pytest.raises(ValidationError) as error:
        validate_form(compile_form([Write]), "write", {"note": {"text": "ready", "extra": "unexpected"}})
    assert "note.extra" in error.value.message_dict


def test_pending_verdict_and_reserved_action_field_are_rejected():
    with pytest.raises(ImproperlyConfigured):
        class Pending(Action, value="pending", label="Pending", verdict=Verdict.PENDING):
            pass

    class Reserved(Action, value="reserved", label="Reserved", verdict=Verdict.COMPLETED):
        action: str

    with pytest.raises(ImproperlyConfigured):
        compile_form([Reserved])


def test_nested_discriminated_unions_are_not_a_supported_form_shape():
    class Text(BaseModel):
        kind: Literal["text"]
        text: str

    class Count(BaseModel):
        kind: Literal["count"]
        count: int

    class Nested(Action, value="nested", label="Nested", verdict=Verdict.COMPLETED):
        content: Annotated[Text | Count, Field(discriminator="kind")]

    with pytest.raises(ImproperlyConfigured):
        compile_form([Nested])


def test_omitted_values_use_stored_initial_instead_of_current_class_default():
    class Write(Action, value="write", label="Write", verdict=Verdict.COMPLETED):
        note: str = "class default"

    schema = compile_form([Write], initial={"write": {"note": "shown in the form"}})
    Write.model_fields["note"].default = "new class default"
    Write.model_rebuild(force=True)
    values = {}
    resolution = validate_form(schema, "write", values)[1]
    assert resolution == {"action": "write", "note": "shown in the form"}
    assert values == {}
    assert Write.model_validate({"note": resolution["note"]}).note == "shown in the form"


def test_frozen_defaults_fill_nested_submitted_objects():
    class Note(BaseModel):
        text: str = "declared"

    class Write(Action, value="write", label="Write", verdict=Verdict.COMPLETED):
        notes: list[Note]

    schema = compile_form([Write], initial={"write": {"notes": [{"text": "first"}, {"text": "second"}]}})
    assert validate_form(schema, "write", {"notes": [{}, {}]})[1]["notes"] == [
        {"text": "first"}, {"text": "second"},
    ]


def test_stored_defaults_precede_required_checks_after_jsonb_key_reordering():
    class Note(BaseModel):
        text: str

    class Write(Action, value="write", label="Write", verdict=Verdict.COMPLETED):
        note: str
        notes: list[Note]

    schema = compile_form([Write], initial={"write": {"note": "stored", "notes": [{"text": "nested"}]}})
    stored = json.loads(
        json.dumps(schema),
        object_pairs_hook=lambda pairs: dict(sorted(pairs, key=lambda item: (len(item[0]), item[0]))),
    )
    branch = stored["oneOf"][0]
    assert list(branch).index("required") < list(branch).index("properties")
    expected = {"action": "write", "note": "stored", "notes": [{"text": "nested"}]}
    assert validate_form(stored, "write", {})[1] == expected
    assert validate_form(stored, "write", {"notes": [{}]})[1] == expected


@pytest.mark.parametrize(("initial", "refine"), [
    ([], {}), ({}, []), ({"complete": None}, {}),
    ({}, {"complete": None}), ({}, {"complete": {"note": None}}),
])
def test_malformed_runtime_mapping_shapes_have_defined_errors(initial, refine):
    with pytest.raises(ValidationError):
        compile_form([Complete], initial=initial, refine=refine)


def test_invalid_declared_default_is_a_configuration_fault_even_with_runtime_values():
    class Write(Action, value="write", label="Write", verdict=Verdict.COMPLETED):
        count: int = Field(default="invalid")
        note: str

    with pytest.raises(ImproperlyConfigured):
        compile_form([Write], initial={"write": {"note": "provided"}})


@pytest.mark.parametrize("relation", [
    {"resource": "notes.Document", "permission": "not-a-policy"},
    {"resource": "notes.Document", "filters": [{"operator": "unknown", "field": "name"}]},
    {"resource": "notes.Document", "unexpected": True},
])
def test_relation_metadata_uses_the_base_contract_for_classes_and_runtime_refinements(relation):
    class Select(Action, value="select", label="Select", verdict=Verdict.COMPLETED):
        document: str = Field(json_schema_extra={"relation": relation})

    with pytest.raises(ImproperlyConfigured):
        compile_form([Select])
    with pytest.raises(ValidationError):
        compile_form([Complete], refine={"complete": {"note": {"relation": relation}}})


def test_relation_candidate_projection_preserves_permissions_and_ignores_value_metadata():
    class Select(Action, value="select", label="Select", verdict=Verdict.COMPLETED):
        document: Annotated[str, Relation("notes.Document", permission="write")]
        metadata: dict

    schema = compile_form([Select], initial={"select": {
        "document": "document-a",
        "metadata": {"relation": {"resource": "not.a_model"}, "enum": ["not-a-record"]},
    }}, refine={"select": {"document": {
        "options": [{"value": "document-a", "label": "A"}, {"value": "document-b", "label": "B"}],
        "relation": {"resource": "notes.Document", "permission": "write", "filters": [
            {"operator": "eq", "field": "state", "value": "ready"},
        ]},
    }}})
    assert relation_candidates(schema) == (
        RelationCandidate("notes.Document", "write", ("document-a", "document-b")),
    )
    assert schema["oneOf"][0]["properties"]["document"]["relation"]["filters"] == [
        {"operator": "eq", "field": "state", "value": "ready"},
    ]


@pytest.mark.parametrize("change", [
    {"kind": ""}, {"kind": "  "}, {"assignees": ()}, {"max_attempts": 0},
    {"max_attempts": -1}, {"max_attempts": True}, {"max_attempts": "3"},
    {"supersede": True}, {"context": {}},
    {"assignees": "person"}, {"assignees": 3}, {"actions": ()}, {"actions": (Action,)},
])
def test_request_owns_seat_invariants(change):
    with pytest.raises(ValidationError):
        DecisionRequest(**{"kind": "note", "subject": None, "assignees": (object(),), "actions": (Complete,), **change})


def test_request_owns_configured_attempt_limit(settings):
    settings.ANGEE_DECISION_MAX_ATTEMPTS = 5
    request = DecisionRequest(kind="note", subject=None, assignees=(object(),), actions=(Complete,))
    assert request.attempt_limit == 5
    explicit = DecisionRequest(kind="note", subject=None, assignees=(object(),), actions=(Complete,), max_attempts=2)
    assert explicit.attempt_limit == 2
