from __future__ import annotations

from typing import Annotated
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_serializer, model_serializer
from referencing.exceptions import Unresolvable

from angee.workflows.data_contracts import (
    check_json_schema,
    json_schema_validator,
    json_value_at_path,
    model_data_contract,
    schema_data_contract,
)
from angee.workflows.decision_actions import ReviewAction, build_decision_action


@pytest.mark.parametrize("reference", ["https://example.invalid/schema", "other.json", "//example.invalid/schema"])
@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_schema_declarations_reject_nonlocal_references(reference: str, keyword: str) -> None:
    schema = {"type": "object", "properties": {"value": {"allOf": [{keyword: reference}]}}}
    with patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(ValueError, match="references must be local"):
            check_json_schema(schema)
        urlopen.assert_not_called()


def test_schema_annotations_are_not_reference_declarations() -> None:
    schema = {"type": "object", "default": {"$ref": "ordinary data"}, "const": {"$ref": "ordinary data"}}
    assert check_json_schema(schema) == schema


def test_runtime_schema_validation_never_fetches_remote_references() -> None:
    with patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(Unresolvable):
            list(json_schema_validator({"$ref": "https://example.invalid/schema"}).iter_errors({}))
        urlopen.assert_not_called()


def test_runtime_schema_validation_resolves_root_local_definitions() -> None:
    schema = {
        "$id": "https://example.invalid/schema",
        "$defs": {"value": {"type": "integer", "minimum": 1}},
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/value"}},
    }
    assert check_json_schema(schema) == schema
    with patch("urllib.request.urlopen") as urlopen:
        validator = json_schema_validator(schema)
        assert not list(validator.iter_errors({"value": 1}))
        assert list(validator.iter_errors({"value": 0}))
        urlopen.assert_not_called()


class Address(BaseModel):
    """A postal address."""

    city: str


class Payload(BaseModel):
    address: Address | None = Field(title="Destination", description="Where to send it.")
    values: list[int]
    aliased: str = Field(serialization_alias="wire.name", validation_alias="input.name")
    literal_brackets: str = Field(alias="[]")


def test_config_paths_resolve_decimal_indices_without_changing_numeric_object_keys() -> None:
    schema = {
        "type": "object",
        "required": ["0"],
        "properties": {
            "0": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["01"],
                    "properties": {"01": {"type": "string"}},
                },
            },
        },
    }
    contract = schema_data_contract(schema)
    path = ("0", "0", "01")
    resolved = contract.catalogue.resolve_path(path)
    assert resolved == ("0", 0, "01")
    assert contract.guarantees_path(resolved)
    assert not contract.matches_path(path)  # Binding paths still require typed indices.
    value = {"0": [{"01": "public-id"}]}
    assert json_value_at_path(value, path, field="id_path") == "public-id"
    assert json_value_at_path(value, resolved, field="id_path") == "public-id"
    assert contract.catalogue.resolve_path(("missing",)) is None
    assert contract.catalogue.resolve_path(("0", "0", "01", "missing")) is None
    with pytest.raises(ValidationError, match="does not exist"):
        json_value_at_path(value, ("0", "1", "01"), field="id_path")


@pytest.mark.parametrize("segment", ["-1", "01", "+0", " 0", "0.0", "²", "٠", "", "missing"])
def test_config_paths_reject_noncanonical_array_indices(segment: str) -> None:
    contract = schema_data_contract({"type": "array", "items": {"type": "string"}})
    assert contract.catalogue.resolve_path((segment,)) is None
    with pytest.raises(ValidationError, match="does not exist"):
        json_value_at_path(["public-id"], (segment,), field="id_path")


@pytest.mark.parametrize("segment", [-1, True, False, 0.0])
def test_runtime_paths_reject_negative_boolean_and_float_indices(segment: int | float) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        json_value_at_path(["public-id"], (segment,), field="id_path")


def test_modes_preserve_pydantic_aliases_defs_nullable_arrays_and_literal_keys() -> None:
    validation = model_data_contract(Payload, mode="validation")
    serialization = model_data_contract(Payload, mode="serialization")

    assert validation.matches_path(["input.name"])
    assert not validation.matches_path(["wire.name"])
    assert serialization.matches_path(["wire.name"])
    assert not serialization.matches_path(["input.name"])
    assert serialization.matches_path(["address", "city"])
    address = serialization.catalogue.fields[0].contract
    assert address.nullable
    assert address.title == "Destination"
    assert address.description == "Where to send it."
    assert serialization.matches_path(["values", 0])
    assert serialization.matches_path(["values", 999])
    assert not serialization.matches_path(["values", True])
    assert not serialization.matches_path(["values", -1])
    assert not serialization.matches_path(["values", "0"])
    assert serialization.matches_path(["[]"])
    assert not serialization.matches_path(["literal_brackets"])
    assert serialization.raw_schema is not None
    assert "$defs" in serialization.raw_schema

    flat = serialization.flat_catalogue()
    assert flat.root_node_id == 0
    assert [node.id for node in flat.nodes] == list(range(len(flat.nodes)))
    literal = next(edge for edge in flat.edges if edge.kind == "field" and edge.key == "[]")
    assert flat.nodes[literal.child_node_id].kind == "scalar"
    assert any(edge.kind == "item" and edge.key is None for edge in flat.edges)


class SerializedValue(BaseModel):
    value: int

    @field_serializer("value", return_type=str)
    def serialize_value(self, value: int) -> str:
        return str(value)


class SerializedRoot(BaseModel):
    value: int

    @model_serializer(return_type=list[str])
    def serialize_model(self) -> list[str]:
        return [str(self.value)]


def test_serialization_mode_follows_custom_serializer_shapes() -> None:
    field_contract = model_data_contract(SerializedValue, mode="serialization")
    root_contract = model_data_contract(SerializedRoot, mode="serialization")

    value = next(edge.contract for edge in field_contract.catalogue.fields if edge.key == "value")
    assert value.kind == "scalar"
    assert value.json_type == "string"
    assert field_contract.raw_schema is not None
    assert field_contract.raw_schema["properties"]["value"]["type"] == "string"
    assert root_contract.catalogue.kind == "array"
    assert root_contract.matches_path([0])


class StringRoot(RootModel[str]):
    pass


class ArrayRoot(RootModel[list[Address]]):
    pass


def test_root_models_use_their_emitted_root_shape() -> None:
    scalar = model_data_contract(StringRoot, mode="serialization")
    array = model_data_contract(ArrayRoot, mode="serialization")

    assert scalar.matches_path([])
    assert not scalar.matches_path(["anything"])
    assert array.matches_path([0, "city"])


class CommonA(BaseModel):
    shared: Address
    only_a: str


class CommonB(BaseModel):
    shared: Address
    only_b: str


class UnionPayload(BaseModel):
    choice: CommonA | CommonB
    incompatible: str | list[str]


def test_union_catalogue_exposes_only_common_compatible_paths() -> None:
    contract = model_data_contract(UnionPayload, mode="serialization")

    assert contract.matches_path(["choice", "shared", "city"])
    assert not contract.matches_path(["choice", "only_a"])
    assert contract.matches_path(["incompatible"])
    assert not contract.matches_path(["incompatible", 0])


class SameMetadataA(BaseModel):
    shared: str = Field(title="Shared", description="Agreed description")


class SameMetadataB(BaseModel):
    shared: str = Field(title="Shared", description="Agreed description")


class UnionMetadata(BaseModel):
    agreed: SameMetadataA | SameMetadataB
    overridden: SameMetadataA | SameMetadataB = Field(title="Enclosing title")


def test_union_metadata_requires_agreement_and_enclosing_metadata_wins() -> None:
    contract = model_data_contract(UnionMetadata, mode="serialization")
    agreed = next(edge.contract for edge in contract.catalogue.fields if edge.key == "agreed")
    overridden = next(edge.contract for edge in contract.catalogue.fields if edge.key == "overridden")

    shared = next(edge.contract for edge in agreed.fields if edge.key == "shared")
    assert shared.title == "Shared"
    assert shared.description == "Agreed description"
    assert overridden.title == "Enclosing title"


class RecursiveNode(BaseModel):
    name: str
    child: RecursiveNode | None = None


class DynamicPayload(BaseModel):
    values: dict[str, int]
    recursive: RecursiveNode


class ForbidExtras(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: Address


class AnnotatedNullable(BaseModel):
    address: Address | Annotated[None, Field(title="No address", description="Explicit null branch")]


def test_dynamic_and_recursive_locations_are_terminal_unknown_nodes() -> None:
    contract = model_data_contract(DynamicPayload, mode="serialization")

    assert contract.matches_path(["values"])
    assert not contract.matches_path(["values", "arbitrary"])
    assert contract.matches_path(["recursive", "name"])
    assert contract.matches_path(["recursive", "child"])
    assert not contract.matches_path(["recursive", "child", "name"])


def test_additional_properties_policy_does_not_hide_declared_fields() -> None:
    contract = model_data_contract(ForbidExtras, mode="validation")

    assert contract.raw_schema is not None
    assert contract.raw_schema["additionalProperties"] is False
    assert contract.matches_path(["address", "city"])


def test_descriptive_null_branch_remains_a_nullable_object() -> None:
    contract = model_data_contract(AnnotatedNullable, mode="validation")

    assert contract.matches_path(["address", "city"])
    address = next(edge.contract for edge in contract.catalogue.fields if edge.key == "address")
    assert address.nullable


class EscapedKeys(BaseModel):
    brackets: str = Field(alias="[]")
    dotted: str = Field(alias="a.b")
    slash: str = Field(alias="a/b")


def test_property_keys_are_literal_path_segments() -> None:
    contract = model_data_contract(EscapedKeys, mode="validation")

    assert contract.matches_path(["[]"])
    assert contract.matches_path(["a.b"])
    assert contract.matches_path(["a/b"])
    assert not contract.matches_path(["a", "b"])


class UnsupportedStructures(BaseModel):
    fixed_tuple: tuple[str, int]
    ref_with_sibling: Address = Field(json_schema_extra={"allOf": [{"type": "object"}]})
    conditional: Address = Field(json_schema_extra={"if": {"required": ["city"]}})


def test_pydantic_structural_vocabulary_outside_the_bounded_subset_is_unknown() -> None:
    contract = model_data_contract(UnsupportedStructures, mode="serialization")

    assert contract.matches_path(["fixed_tuple"])
    assert not contract.matches_path(["fixed_tuple", 0])
    assert contract.matches_path(["ref_with_sibling"])
    assert not contract.matches_path(["ref_with_sibling", "city"])
    assert contract.matches_path(["conditional"])
    assert not contract.matches_path(["conditional", "city"])


def test_schema_less_declaration_has_no_raw_schema_and_an_unknown_root() -> None:
    contract = model_data_contract(None, mode="validation")

    assert contract.raw_schema is None
    assert contract.catalogue.kind == "unknown"
    assert contract.matches_path([])
    assert not contract.matches_path(["anything"])


def test_unsupported_and_remote_refs_are_root_only_unknown() -> None:
    from angee.workflows.data_contracts import _CatalogueProjector

    dynamic = _CatalogueProjector({"additionalProperties": True}).project()
    remote = _CatalogueProjector({"$ref": "https://example.test/schema"}).project()

    assert dynamic.matches([]) and not dynamic.matches(["key"])
    assert remote.matches([]) and not remote.matches(["key"])


def test_publish_proofs_fail_closed_on_unhandled_structural_vocabulary() -> None:
    schemas = [
        {
            "allOf": [
                {
                    "type": "object",
                    "required": ["result"],
                    "properties": {"result": {"const": "accepted"}},
                }
            ]
        },
        {
            "type": "object",
            "required": ["result"],
            "properties": {"result": {"const": "accepted", "not": {"const": "rejected"}}},
        },
        {
            "type": "array",
            "minItems": 1,
            "prefixItems": [{"type": "integer", "minimum": 1}],
            "items": {"type": "integer"},
        },
    ]

    all_of, conditional_leaf, tuple_array = (schema_data_contract(schema) for schema in schemas)
    assert not all_of.guarantees_path(["result"])
    assert all_of.literal_values_at_path(["result"]) is None
    assert conditional_leaf.literal_values_at_path(["result"]) is None
    assert not tuple_array.guarantees_path([0])
    assert tuple_array.numeric_ranges_at_path([0]) is None


def test_publish_proofs_reject_structural_siblings_of_refs_and_unions() -> None:
    reference = schema_data_contract(
        {
            "$defs": {
                "Result": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"const": "accepted"}},
                }
            },
            "$ref": "#/$defs/Result",
            "if": {"properties": {"value": {"const": "accepted"}}},
        }
    )
    union = schema_data_contract(
        {
            "oneOf": [{"const": 1}, {"const": 2}],
            "not": {"const": 3},
        }
    )

    assert not reference.guarantees_path(["value"])
    assert reference.literal_values_at_path(["value"]) is None
    assert union.literal_values_at_path([]) is None


@pytest.mark.parametrize("reference", ["#/$defs/Result~1~0%20value", "#result", "#/$defs/Alias"])
@pytest.mark.parametrize("root_id", [{}, {"$id": "https://schema.example/root"}])
def test_local_references_preserve_catalogue_and_structural_proofs(reference: str, root_id: dict[str, str]) -> None:
    contract = schema_data_contract(
        {
            **root_id,
            "$defs": {
                "Result/~ value": {
                    "$anchor": "result",
                    "type": "object",
                    "required": ["status", "amount", "label"],
                    "properties": {
                        "status": {"type": "string", "const": "accepted"},
                        "amount": {"type": "integer", "minimum": 2, "exclusiveMaximum": 7},
                        "label": {"type": "string", "minLength": 1, "maxLength": 8},
                    },
                },
                "Alias": {"$ref": "#/$defs/Result~1~0%20value"},
            },
            "$ref": reference,
        }
    )

    for field in ("status", "amount", "label"):
        assert contract.matches_path([field])
        assert contract.guarantees_path([field])
    assert contract.literal_values_at_path(["status"]) == ("accepted",)
    assert contract.numeric_ranges_at_path(["amount"]) == ((2, False, 7, True),)
    assert contract.string_length_ranges_at_path(["label"]) == ((1, 8),)


@pytest.mark.parametrize("base_constraint", [False, True])
@pytest.mark.parametrize(("method", "variants", "expected"), [
    ("literal_values_at_path", [{"const": "a"}, {"const": "b"}], ("a", "b")),
    ("numeric_ranges_at_path", [
        {"type": "integer", "minimum": 1}, {"type": "number", "exclusiveMaximum": 5},
    ], ((1, False, None, False), (None, False, 5, True))),
    ("string_length_ranges_at_path", [
        {"type": "string", "minLength": 1}, {"type": "string", "maxLength": 5},
    ], ((1, None), (0, 5))),
])
def test_scalar_proofs_share_array_union_traversal(method, variants, expected, base_constraint) -> None:
    item = {"anyOf": variants, **(variants[0] if base_constraint else {})}
    contract = schema_data_contract({
        "type": "object", "properties": {"values": {"type": "array", "items": item}},
    })
    assert getattr(contract, method)(["values", 0]) == (expected[:1] if base_constraint else expected)


def test_root_property_pointers_are_supported_beyond_defs() -> None:
    contract = schema_data_contract({
        "type": "object", "required": ["value"],
        "properties": {
            "source": {"type": "string", "const": "accepted", "minLength": 1, "maxLength": 8},
            "value": {"$ref": "#/properties/source"},
        },
    })
    assert contract.matches_path(["value"])
    assert contract.guarantees_path(["value"])
    assert contract.literal_values_at_path(["value"]) == ("accepted",)
    assert contract.string_length_ranges_at_path(["value"]) == ((1, 8),)


@pytest.mark.parametrize("location", ["nested", "reference", "pointer"])
def test_scoped_references_never_prove_against_the_outer_document(location: str) -> None:
    scoped = {
        "$id": "child",
        "$defs": {"Value": {"type": "integer", "const": 7, "minimum": 7}},
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"$ref": "#/$defs/Value"}},
    }
    schema = {
        "$id": "https://schema.example/root",
        "$defs": {"Value": {"type": "string", "const": "outer", "minLength": 1, "maxLength": 5}},
    }
    if location == "nested":
        schema.update(type="object", required=["child"], properties={"child": scoped})
        path = ["child", "value"]
    else:
        schema["$defs"]["Scoped"] = scoped
        schema["$ref"] = "#/$defs/Scoped" + ("/properties/value" if location == "pointer" else "")
        path = [] if location == "pointer" else ["value"]
    contract = schema_data_contract(schema)

    node = contract.catalogue.at_path(path)
    assert node is None or node.kind == "unknown"
    assert not contract.guarantees_path(path)
    assert contract.literal_values_at_path(path) is None
    assert contract.numeric_ranges_at_path(path) is None
    assert contract.string_length_ranges_at_path(path) is None


def test_root_resource_identity_is_retained_across_shape_projection() -> None:
    shape = {
        "$id": "https://schema.example/root",
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string", "const": "accepted"}},
    }
    nullable = schema_data_contract({**shape, "type": ["object", "null"]})
    assert nullable.matches_path(["value"])
    assert nullable.catalogue.nullable
    union = schema_data_contract({**shape, "anyOf": [{"type": "object"}, {"type": "object"}]})
    assert union.guarantees_path(["value"])
    assert union.literal_values_at_path(["value"]) == ("accepted",)


@pytest.mark.parametrize("reference", ["#/$defs/Missing", "#missing", "#/$defs/Cycle", "#", "https://example.test"])
def test_unresolved_or_recursive_references_leave_proofs_unknown(reference: str) -> None:
    contract = schema_data_contract({"$defs": {"Cycle": {"$ref": "#/$defs/Cycle"}}, "$ref": reference})

    assert contract.catalogue.kind == "unknown"
    assert not contract.guarantees_path([])
    assert contract.literal_values_at_path([]) is None
    assert contract.numeric_ranges_at_path([]) is None
    assert contract.string_length_ranges_at_path([]) is None


def test_publish_proofs_accept_builder_base_schema_intersected_with_every_action_branch() -> None:
    authored = build_decision_action(
        actions=(
            ReviewAction(value="approve", label="Approve", verdict="COMPLETE"),
            ReviewAction(value="reject", label="Reject", verdict="REJECT"),
        )
    )
    contract = schema_data_contract(authored.decision_schema)

    assert contract.guarantees_path(["action"])
    assert contract.literal_values_at_path(["action"]) == ("approve", "reject")
