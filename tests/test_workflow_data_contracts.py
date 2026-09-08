from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_serializer, model_serializer

from angee.workflows.data_contracts import model_data_contract


class Address(BaseModel):
    """A postal address."""

    city: str


class Payload(BaseModel):
    address: Address | None = Field(title="Destination", description="Where to send it.")
    values: list[int]
    aliased: str = Field(serialization_alias="wire.name", validation_alias="input.name")
    literal_brackets: str = Field(alias="[]")


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
