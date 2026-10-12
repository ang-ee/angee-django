"""Shared declaration, validation and bounded JSON Schema algebra contracts."""

import copy
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from angee.base.jsonschema import (
    LocalSchemaReferences,
    check_schema,
    embed_schema,
    materialize_schema,
    relation_positions,
    schema_at,
    schemas_match,
    union_schema,
    unmatched_properties,
    validate,
    validation_issues,
    validator,
)


@pytest.mark.parametrize("choice", ["anyOf", "oneOf"])
def test_relation_positions_share_reference_array_and_valid_branch_semantics(choice):
    relation = {"resource": "knowledge.Page"}
    schema = {
        "$defs": {"Reference": {"type": "string", "relation": relation}},
        "type": "object",
        "properties": {
            "page": {"allOf": [{"$ref": "#/$defs/Reference"}]},
            "items": {"type": "array", "prefixItems": [{"$ref": "#/$defs/Reference"}], "items": {"type": "string"}},
            "choice": {choice: [
                {"type": "object", "properties": {
                    "kind": {"const": "reference"}, "value": {"$ref": "#/$defs/Reference"},
                }},
                {"type": "object", "properties": {"kind": {"const": "literal"}, "value": {"type": "string"}}},
            ]},
        },
    }
    value = {"page": "example.page", "items": ["example.first", "example.literal"],
             "choice": {"kind": "literal", "value": "example.ordinary"}}
    assert list(relation_positions(schema, value)) == [
        (("page",), relation, "example.page"), (("items", 0), relation, "example.first"),
    ]
    value["choice"]["kind"] = "reference"
    assert list(relation_positions(schema, value))[-1] == (("choice", "value"), relation, "example.ordinary")


@pytest.mark.parametrize("schema", [True, False, {}, {"type": "string"}])
def test_native_boolean_and_object_declarations(schema):
    """Both native schema forms are checked without changing the declaration."""
    before = copy.deepcopy(schema)
    check_schema(schema)
    assert schema == before


def test_relation_walk_leaves_recursive_literal_schemas_to_native_validation():
    schema = {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#"}}}}
    value = {"children": [{"children": []}]}
    assert validator(schema).is_valid(value)
    assert list(relation_positions(schema, value)) == []


@pytest.mark.parametrize("schema", [{"type": "missing"}, {"required": "name"}, {"properties": {"bad": 3}}])
def test_invalid_declarations_raise_django_validation_errors(schema):
    with pytest.raises(ValidationError, match="Invalid JSON Schema"):
        validator(schema)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
@pytest.mark.parametrize("reference", ["#/$defs/missing", "https://example.invalid/schema", "child.json#value"])
def test_unused_definitions_prove_all_references_before_value_validation(keyword, reference):
    schema = {"type": "object", "$defs": {"Unused": {keyword: reference}}}
    with pytest.raises(ValidationError, match="reference"):
        validator(schema)


@pytest.mark.parametrize("reference", ["#/allOf/not_an_index", "#/type/missing"])
def test_malformed_pointer_traversal_is_a_declaration_error(reference):
    with pytest.raises(ValidationError, match="reference"):
        check_schema({"type": "object", "allOf": [{}], "$ref": reference})


def test_a_reference_into_literal_data_checks_the_target_as_a_schema():
    schema = {"$ref": "#/default", "default": {"$ref": "#/$defs/missing"}}
    with pytest.raises(ValidationError, match="reference"):
        check_schema(schema)


@pytest.mark.parametrize("target", [{"type": "integer"}, {"$ref": "#/$defs/Value"}])
def test_structural_composition_declines_references_into_literal_data(target):
    schema = {"$defs": {"Value": {"type": "integer"}}, "$ref": "#/default", "default": target}
    original = copy.deepcopy(schema)
    definitions: dict[str, Any] = {}
    assert validator(schema).is_valid(1)
    assert not validator(schema).is_valid("bad")
    assert not schemas_match(schema, schema)
    with pytest.raises(ValidationError, match="literal data"):
        embed_schema(schema, definitions)
    assert schema == original
    assert definitions == {}


def test_nested_resource_scope_is_rejected_even_when_unused():
    schema = {"$defs": {"Unused": {"$id": "child", "type": "string"}}}
    with pytest.raises(ValidationError, match="nested resource"):
        check_schema(schema)


@pytest.mark.parametrize("dialect", ["https://json-schema.org/draft-07/schema#", "https://example.invalid/schema"])
def test_other_schema_dialects_are_rejected(dialect):
    with pytest.raises(ValidationError, match="Draft 2020-12|dialect"):
        check_schema({"$schema": dialect})


def test_root_pointers_anchors_and_escaped_definition_names_are_native():
    schema = {
        "$defs": {"Text/~ value": {"$anchor": "text", "type": "string", "minLength": 2}},
        "anyOf": [{"$ref": "#/$defs/Text~1~0%20value"}, {"$ref": "#text"}],
    }
    references = LocalSchemaReferences(schema)
    assert references.resolve("#") is schema
    assert references.resolve("#text") is schema["$defs"]["Text/~ value"]
    assert validator(schema).is_valid("ok")
    assert not validator(schema).is_valid("x")


def test_native_recursive_dynamic_references_are_validated_but_not_structurally_proven():
    schema = {
        "$dynamicAnchor": "node", "type": "object",
        "properties": {"child": {"$dynamicRef": "#node"}}, "additionalProperties": False,
    }
    assert validator(schema).is_valid({"child": {}})
    assert not validator(schema).is_valid({"child": "invalid"})
    assert not schemas_match(schema, schema)
    assert schema_at(schema, ["child"]) is None
    with pytest.raises(ValidationError, match="dynamic scope"):
        embed_schema(schema, {})


@pytest.mark.parametrize(
    "format_,valid,invalid",
    [
        ("date-time", "2026-09-28T12:30:00Z", "yesterday"),
        ("uri", "https://example.test/", "not a uri"),
        ("hostname", "example.test", "bad host"),
        ("duration", "P1D", "one day"),
    ],
)
def test_shared_validator_asserts_installed_formats(format_, valid, invalid):
    schema = {"type": "string", "format": format_}
    validate(schema, valid)
    with pytest.raises(ValidationError, match=format_):
        validate(schema, invalid)


def test_value_validation_preserves_all_native_messages_in_order():
    schema = {"type": "object", "properties": {"number": {"type": "integer"}, "text": {"minLength": 3}}}
    with pytest.raises(ValidationError) as caught:
        validate(schema, {"number": "bad", "text": "x"})
    assert caught.value.messages == ["$.number: 'bad' is not of type 'integer'", "$.text: 'x' is too short"]


def test_declaration_and_embedding_leave_literal_reference_data_untouched():
    literal = {"$ref": "https://example.invalid/literal", "title": "data", "default": 3}
    schema = {"const": literal, "default": literal, "examples": [literal]}
    original = copy.deepcopy(schema)
    definitions: dict[str, Any] = {}
    embedded = embed_schema(schema, definitions)
    assert schema == original
    assert definitions["schema_0"] == original
    assert validator({**embedded, "$defs": definitions}).is_valid(literal)


def test_structural_equality_resolves_refs_without_losing_property_names_or_literal_data():
    source = {"$defs": {"Value": {"type": "integer"}}, "properties": {"title": {"$ref": "#/$defs/Value"}}}
    target = {"properties": {"title": {"type": "integer", "description": "presentation"}}}
    assert schemas_match(source, target)
    assert not schemas_match(source, {"properties": {}})
    assert not schemas_match({"const": {"title": "a"}}, {"const": {"title": "b"}})
    assert not schemas_match({"const": True}, {"const": 1})


def test_structural_equality_keeps_reference_assertion_siblings():
    source = {"$defs": {"Value": {"type": "integer"}}, "$ref": "#/$defs/Value", "minimum": 3}
    assert schemas_match(source, copy.deepcopy(source))
    assert not schemas_match(source, {"type": "integer"})


@pytest.mark.parametrize(("properties", "unmatched"), [
    ({}, ["required"]),
    ({"required": {"type": "integer"}}, []),
    ({"required": {"type": "string"}, "optional": {"type": "integer"}}, ["required", "optional"]),
])
def test_projection_compares_declared_fields_and_permits_absent_optional_fields(properties, unmatched):
    source = {"type": "object", "properties": properties}
    target = {
        "type": "object", "required": ["required"],
        "properties": {"required": {"type": "integer"}, "optional": {"type": "string"}},
    }
    original = copy.deepcopy((source, target))
    assert list(unmatched_properties(source, target)) == unmatched
    assert (source, target) == original


def test_projection_retains_local_reference_scope_and_ignores_only_schema_annotations():
    source = {
        "$defs": {"Value": {"type": "integer", "title": "Source"}},
        "properties": {"value": {"$ref": "#/$defs/Value"}, "literal": {"const": {"title": "source"}}},
    }
    target = {
        "$defs": {"Shape": {
            "properties": {"value": {"type": "integer", "title": "Target"}, "literal": {"const": {"title": "target"}}},
            "required": ["value", "literal"],
        }},
        "$ref": "#/$defs/Shape",
    }
    assert list(unmatched_properties(source, target)) == ["literal"]


def test_recursive_structural_comparison_terminates_and_checks_nonrecursive_fields():
    source = {"$defs": {"Node": {"properties": {"value": {"type": "integer"}, "next": {"$ref": "#/$defs/Node"}}}},
              "$ref": "#/$defs/Node"}
    target = copy.deepcopy(source)
    assert schemas_match(source, target)
    target["$defs"]["Node"]["properties"]["value"] = {"type": "string"}
    assert not schemas_match(source, target)


def test_structural_lookup_keeps_refs_required_parents_and_array_bounds():
    schema = {
        "$defs": {"Value": {"type": "integer"}}, "type": "object", "required": ["items"],
        "properties": {"items": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/Value"}}},
    }
    assert schemas_match(schema_at(schema, ["items", 0], required=True), {"type": "integer"})
    assert schema_at(schema, ["items", 1], required=True) is None
    assert schema_at(schema, ["items", 1]) is not None
    assert schema_at(schema, ["missing"]) is None


def test_structural_lookup_preserves_reference_siblings_at_a_leaf():
    schema = {
        "$defs": {"Value": {"type": "integer", "minimum": 1}},
        "properties": {"value": {"$ref": "#/$defs/Value", "minimum": 3}},
    }
    selected = schema_at(schema, ["value"])
    assert selected is not None
    assert validator(selected).is_valid(3)
    assert not validator(selected).is_valid(2)


def test_composed_allof_proves_required_paths_and_keeps_each_assertion():
    schema = {
        "allOf": [
            {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
            {"type": "object", "properties": {"value": {"minimum": 3}}},
        ],
    }
    selected = schema_at(schema, ["value"], required=True)
    assert selected is not None
    assert validator(selected).is_valid(3)
    assert not validator(selected).is_valid(2)
    assert not validator(selected).is_valid("bad")


def test_projection_and_embedding_retain_root_pointers_outside_definitions():
    schema = {
        "$id": "https://example.invalid/source",
        "type": "object",
        "properties": {
            "value": {"type": "string", "minLength": 2},
            "nested": {"type": "array", "items": {"$ref": "#/properties/value"}},
        },
        "$defs": {"Unused": {"$ref": "#/properties/value"}},
    }
    original = copy.deepcopy(schema)
    selected = schema_at(schema, ["nested"])
    assert selected is not None
    assert validator(selected).is_valid(["ok"])
    assert not validator(selected).is_valid(["x"])
    assert not validator(selected).is_valid([3])
    definitions: dict[str, Any] = {}
    embedded = embed_schema(selected, definitions)
    assert validator({**embedded, "$defs": definitions}).is_valid(["ok"])
    assert not validator({**embedded, "$defs": definitions}).is_valid([3])
    leaf = schema_at(selected, [0])
    assert leaf is not None
    assert schemas_match(leaf, {"type": "string", "minLength": 2})
    assert validator(leaf).is_valid("ok")
    assert schema == original


def test_projection_preserves_recursive_root_scope_and_literal_reference_keys():
    literal = {"$ref": "https://example.invalid/literal", "title": "unchanged"}
    schema = {
        "type": "object", "required": ["value"],
        "properties": {
            "value": {"type": "integer"},
            "children": {"type": "array", "items": {"$ref": "#"}, "default": literal},
        },
    }
    selected = schema_at(schema, ["children"])
    assert selected is not None
    assert selected["default"] == literal
    assert validator(selected).is_valid([{"value": 1, "children": [{"value": 2}]}])
    assert not validator(selected).is_valid([{"children": []}])
    definitions: dict[str, Any] = {}
    embedded = embed_schema(selected, definitions, path=["results"])
    assert validator({**embedded, "$defs": definitions}).is_valid({"results": [{"value": 1}]})
    assert not validator({**embedded, "$defs": definitions}).is_valid({"results": [{"value": "bad"}]})


@pytest.mark.parametrize("reference", [{"$ref": "#value"}, {"$dynamicRef": "#value"}])
def test_projection_declines_unrelocatable_nested_reference_scope(reference):
    schema = {
        "$defs": {"Value": {"$anchor": "value", "type": "string"}},
        "properties": {"nested": {"type": "array", "items": reference}},
    }
    check_schema(schema)
    assert schema_at(schema, ["nested"]) is None


@pytest.mark.parametrize("required", [True, False])
def test_embedding_preserves_nested_refs_and_object_array_path_constraints(required):
    source = {"$defs": {"Value": {"type": "integer"}}, "$ref": "#/$defs/Value"}
    definitions: dict[str, Any] = {}
    constraint = embed_schema(source, definitions, path=["later", 1], required=required)
    compiled = validator({**constraint, "$defs": definitions})
    assert compiled.is_valid({"later": ["ignored", 4]})
    assert not compiled.is_valid({"later": ["ignored", "bad"]})
    assert compiled.is_valid({}) is not required
    assert compiled.is_valid({"later": []}) is not required
    assert source["$ref"] == "#/$defs/Value"


def test_embedding_names_are_deterministic_and_avoid_existing_definitions():
    definitions = {"schema_1": {"type": "string"}}
    assert embed_schema({"type": "integer"}, definitions) == {"$ref": "#/$defs/schema_1_"}
    assert definitions["schema_1"] == {"type": "string"}


def test_embedding_rejects_closed_nested_extensions_before_mutating_either_destination():
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"nested": {"type": "object", "additionalProperties": False}},
    }
    original = copy.deepcopy(schema)
    definitions: dict[str, Any] = {}
    with pytest.raises(ValidationError, match="closed nested field"):
        embed_schema({"type": "integer"}, definitions, path=["nested", "new"], into=schema)
    assert schema == original
    assert definitions == {}


@pytest.mark.parametrize("path", [[-1], [True]])
def test_failed_embedding_does_not_mutate_definitions(path):
    definitions = {"kept": {"type": "string"}}
    with pytest.raises(ValidationError, match="Schema paths"):
        embed_schema({"type": "integer"}, definitions, path=path)
    assert definitions == {"kept": {"type": "string"}}


def test_embedding_rejects_anchor_refs_without_mutating_definitions():
    schema = {"$defs": {"Value": {"$anchor": "value", "type": "integer"}}, "$ref": "#value"}
    definitions: dict[str, Any] = {}
    with pytest.raises(ValidationError, match="root-local JSON Pointer"):
        embed_schema(schema, definitions)
    assert definitions == {}


@pytest.mark.parametrize('schema', [
    {'$id': 'https://example.invalid/schema', 'type': 'string'},
    {'$dynamicAnchor': 'value', '$dynamicRef': '#value'},
])
def test_embedding_rejects_resource_identity_and_dynamic_scope(schema):
    with pytest.raises(ValidationError, match='resource identities or dynamic scope'):
        embed_schema(schema, {})


def test_checked_validation_reports_native_instance_path():
    checked = validator({'properties': {'items': {'type': 'array', 'items': {'type': 'integer'}}}})
    with pytest.raises(ValidationError) as error:
        validate(checked, {'items': ['bad']})
    assert error.value.messages == ["$.items[0]: 'bad' is not of type 'integer'"]


def test_materialization_keeps_reference_sibling_constraints_and_literal_metadata():
    literal = {"$ref": "https://example.invalid/literal"}
    schema = {
        "$defs": {"Value": {"type": "integer", "minimum": 2, "title": "Inherited"}},
        "properties": {"value": {"$ref": "#/$defs/Value", "minimum": 3, "title": "Local"}},
        "default": literal,
    }
    before = copy.deepcopy(schema)
    materialized = materialize_schema(schema)
    assert "$defs" not in materialized
    assert materialized["properties"]["value"]["title"] == "Local"
    assert materialized["default"] == literal
    assert validator(materialized).is_valid({"value": 3})
    assert not validator(materialized).is_valid({"value": 2})
    assert schema == before


@pytest.mark.parametrize("schema", [
    {"$ref": "#"},
    {"$dynamicAnchor": "node", "$dynamicRef": "#node"},
])
def test_materialization_rejects_recursive_or_dynamic_scope(schema):
    with pytest.raises(ValidationError, match="finite|dynamic"):
        materialize_schema(schema)


def test_materialization_keeps_boolean_targets_and_repeated_sibling_references():
    schema = {
        "$defs": {"Value": {"type": "integer"}, "Never": False, "Anything": True},
        "properties": {
            "value": {"$ref": "#/$defs/Value", "allOf": [{"$ref": "#/$defs/Value"}]},
            "never": {"$ref": "#/$defs/Never"}, "anything": {"$ref": "#/$defs/Anything"},
        },
    }
    compiled = validator(materialize_schema(schema))
    assert compiled.is_valid({"value": 1, "anything": {"nested": "value"}})
    assert not compiled.is_valid({"value": "bad"})
    assert not compiled.is_valid({"never": None})


def test_field_validation_applies_nested_defaults_before_required_regardless_of_key_order():
    schema = {
        "required": ["rows"],
        "properties": {"rows": {
            "type": "array", "default": [{}], "items": {
                "required": ["value"], "properties": {"value": {"type": "integer", "default": 3}},
            },
        }},
    }
    value = {}
    assert validation_issues(schema, value, defaults=True) == {}
    assert value == {"rows": [{"value": 3}]}
    assert schema["properties"]["rows"]["default"] == [{}]


def test_field_validation_uses_native_additional_property_rules():
    schema = {"patternProperties": {"^allowed_": {"type": "string"}}, "additionalProperties": False}
    issues = validation_issues(schema, {"allowed_name": "value", "extra": 1})
    assert set(issues) == {"extra"}


def test_union_preserves_distinct_local_reference_roots_without_mutation():
    """The same definition name can mean different things in separate alternatives."""
    number = {"$ref": "#/$defs/Value", "$defs": {"Value": {"type": "integer"}}}
    text = {"$ref": "#/$defs/Value", "$defs": {"Value": {"type": "string"}}}
    before = copy.deepcopy((number, text))
    combined = validator(union_schema(number, text))
    assert combined.is_valid(3) and combined.is_valid("three")
    assert not combined.is_valid({})
    assert (number, text) == before


def test_union_retains_recursive_references_and_distinct_assertions():
    """Native recursive values stay usable; a stricter branch is not dropped."""
    tree = {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#"}}}}
    combined = validator(union_schema(tree, {"type": "integer", "minimum": 2}))
    assert combined.is_valid({"children": [{"children": []}]})
    assert combined.is_valid(2)
    assert not combined.is_valid(1)
    assert not combined.is_valid({"children": [1]})


def test_union_reuses_equal_contracts_and_checks_single_or_empty_alternatives():
    """Annotations do not create duplicate branches; no alternatives admits nothing."""
    original = {"type": "integer", "title": "First"}
    merged = union_schema(original, {"type": "integer", "title": "Second"})
    assert merged == original and merged is not original
    assert not validator(union_schema()).is_valid(None)
    with pytest.raises(ValidationError, match="reference"):
        union_schema({"$ref": "#/$defs/missing"})
