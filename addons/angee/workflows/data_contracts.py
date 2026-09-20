"""Bounded path discovery for Pydantic workflow data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence, TypeAlias, cast

from pydantic import BaseModel

SchemaMode: TypeAlias = Literal["validation", "serialization"]
ConcretePath: TypeAlias = Sequence[str | int]
JsonScalarType: TypeAlias = Literal["string", "integer", "number", "boolean", "null"]
JsonNumber: TypeAlias = int | float
NumericRange: TypeAlias = tuple[JsonNumber | None, bool, JsonNumber | None, bool]


@dataclass(frozen=True, slots=True)
class FieldEdge:
    """A declared JSON object key and the contract below it."""

    key: str
    contract: "DataContractNode"
    kind: Literal["field"] = "field"


@dataclass(frozen=True, slots=True)
class ItemEdge:
    """A homogeneous JSON array item and the contract below it."""

    contract: "DataContractNode"
    kind: Literal["item"] = "item"


@dataclass(frozen=True, slots=True)
class DataContractNode:
    """One discoverable JSON location; unknown nodes expose no descendants."""

    kind: Literal["object", "array", "scalar", "unknown"]
    json_type: JsonScalarType | None = None
    title: str | None = None
    description: str | None = None
    nullable: bool = False
    fields: tuple[FieldEdge, ...] = ()
    item: ItemEdge | None = None

    def matches(self, path: ConcretePath) -> bool:
        """Return whether a concrete string/index path is declared below this node."""

        current = self
        for segment in path:
            if current.kind == "object" and isinstance(segment, str):
                edge = next((edge for edge in current.fields if edge.key == segment), None)
                if edge is None:
                    return False
                current = edge.contract
                continue
            if current.kind == "array" and isinstance(segment, int) and not isinstance(segment, bool) and segment >= 0:
                if current.item is None:
                    return False
                current = current.item.contract
                continue
            return False
        return True

    def at_path(self, path: ConcretePath) -> "DataContractNode | None":
        """Return the existing path catalogue node for bounded type checks."""

        current = self
        for segment in path:
            if current.kind == "object" and isinstance(segment, str):
                edge = next((edge for edge in current.fields if edge.key == segment), None)
                if edge is None:
                    return None
                current = edge.contract
            elif current.kind == "array" and isinstance(segment, int) and not isinstance(segment, bool) and segment >= 0:
                if current.item is None:
                    return None
                current = current.item.contract
            else:
                return None
        return current


@dataclass(frozen=True, slots=True)
class DataContract:
    """Pydantic's raw declaration and its conservative path catalogue."""

    raw_schema: dict[str, Any] | None
    catalogue: DataContractNode

    def matches_path(self, path: ConcretePath) -> bool:
        return self.catalogue.matches(path)

    def guarantees_path(self, path: ConcretePath) -> bool:
        """Prove a referenced property/index is present in every schema variant."""

        if self.raw_schema is None:
            return False
        return _guarantees_path(self.raw_schema, tuple(path), self.raw_schema, frozenset())

    def literal_values_at_path(self, path: ConcretePath) -> tuple[Any, ...] | None:
        """Return every exact JSON value allowed at a bounded scalar path."""

        if self.raw_schema is None:
            return None
        return _literal_values_at_path(
            self.raw_schema, tuple(path), self.raw_schema, frozenset()
        )

    def numeric_ranges_at_path(self, path: ConcretePath) -> tuple[NumericRange, ...] | None:
        """Return every declared numeric range at a bounded scalar path."""

        if self.raw_schema is None:
            return None
        return _numeric_ranges_at_path(
            self.raw_schema, tuple(path), self.raw_schema, frozenset()
        )

    def flat_catalogue(self) -> "FlatDataContract":
        """Return deterministic rows for depth-independent transport."""

        nodes: list[FlatDataContractNode] = []
        edges: list[FlatDataContractEdge] = []

        def visit(node: DataContractNode) -> int:
            identity = len(nodes)
            nodes.append(
                FlatDataContractNode(
                    identity,
                    node.kind,
                    node.json_type,
                    node.title,
                    node.description,
                    node.nullable,
                )
            )
            for edge in node.fields:
                child = visit(edge.contract)
                edges.append(FlatDataContractEdge(identity, child, edge.kind, edge.key))
            if node.item is not None:
                child = visit(node.item.contract)
                edges.append(FlatDataContractEdge(identity, child, node.item.kind))
            return identity

        root = visit(self.catalogue)
        return FlatDataContract(root, tuple(nodes), tuple(edges))


@dataclass(frozen=True, slots=True)
class FlatDataContractNode:
    id: int
    kind: Literal["object", "array", "scalar", "unknown"]
    json_type: JsonScalarType | None
    title: str | None
    description: str | None
    nullable: bool


@dataclass(frozen=True, slots=True)
class FlatDataContractEdge:
    parent_node_id: int
    child_node_id: int
    kind: Literal["field", "item"]
    key: str | None = None

    def __post_init__(self) -> None:
        if (self.kind == "field") != (self.key is not None):
            raise ValueError("Field edges require a key and item edges cannot carry one.")


@dataclass(frozen=True, slots=True)
class FlatDataContract:
    root_node_id: int
    nodes: tuple[FlatDataContractNode, ...]
    edges: tuple[FlatDataContractEdge, ...]

    def __post_init__(self) -> None:
        identities = {node.id for node in self.nodes}
        if len(identities) != len(self.nodes) or self.root_node_id not in identities:
            raise ValueError("Data contract node identities must be unique and include the root.")
        if any(edge.parent_node_id not in identities or edge.child_node_id not in identities for edge in self.edges):
            raise ValueError("Data contract edges must reference declared nodes.")


def model_data_contract(model: type[BaseModel] | None, *, mode: SchemaMode) -> DataContract:
    """Project the JSON paths Pydantic declares for one validation or serialization boundary."""

    if model is None:
        return DataContract(raw_schema=None, catalogue=_UNKNOWN)
    schema = model.model_json_schema(mode=mode, by_alias=True)
    return DataContract(raw_schema=schema, catalogue=_CatalogueProjector(schema).project())


def schema_data_contract(schema: Mapping[str, Any]) -> DataContract:
    """Project a checked published JSON Schema through the same path catalogue."""

    copied = dict(schema)
    return DataContract(raw_schema=copied, catalogue=_CatalogueProjector(copied).project())


def _guarantees_path(
    schema: Any, path: tuple[str | int, ...], root: Mapping[str, Any], active_refs: frozenset[str]
) -> bool:
    if not isinstance(schema, Mapping):
        return False
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in active_refs or not reference.startswith("#/$defs/"):
            return False
        definitions = root.get("$defs", {})
        target = definitions.get(reference.removeprefix("#/$defs/")) if isinstance(definitions, Mapping) else None
        return _guarantees_path(target, path, root, active_refs | {reference})
    variants = schema.get("oneOf", schema.get("anyOf"))
    if isinstance(variants, list):
        return bool(variants) and all(
            _guarantees_path(choice, path, root, active_refs) for choice in variants
        )
    if not path:
        return True
    segment, rest = path[0], path[1:]
    if isinstance(segment, str):
        if schema.get("type") != "object":
            return False
        required, properties = schema.get("required", ()), schema.get("properties", {})
        return (
            isinstance(required, list | tuple) and segment in required
            and isinstance(properties, Mapping) and segment in properties
            and _guarantees_path(properties[segment], rest, root, active_refs)
        )
    if isinstance(segment, int) and not isinstance(segment, bool) and segment >= 0:
        return (
            schema.get("type") == "array" and type(schema.get("minItems", 0)) is int
            and schema.get("minItems", 0) > segment
            and _guarantees_path(schema.get("items"), rest, root, active_refs)
        )
    return False


def _literal_values_at_path(
    schema: Any,
    path: tuple[str | int, ...],
    root: Mapping[str, Any],
    active_refs: frozenset[str],
) -> tuple[Any, ...] | None:
    """Resolve refs/unions and retain only paths with finite literal values."""

    if not isinstance(schema, Mapping):
        return None
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in active_refs or not reference.startswith("#/$defs/"):
            return None
        definitions = root.get("$defs", {})
        target = definitions.get(reference.removeprefix("#/$defs/")) if isinstance(definitions, Mapping) else None
        return _literal_values_at_path(target, path, root, active_refs | {reference})
    variants = schema.get("oneOf", schema.get("anyOf"))
    if isinstance(variants, list):
        if not variants:
            return None
        values: list[Any] = []
        for choice in variants:
            choice_values = _literal_values_at_path(choice, path, root, active_refs)
            if choice_values is None:
                return None
            values.extend(choice_values)
        return tuple(values)
    if path:
        segment, rest = path[0], path[1:]
        if isinstance(segment, str):
            properties = schema.get("properties", {})
            if schema.get("type") != "object" or not isinstance(properties, Mapping):
                return None
            return _literal_values_at_path(properties.get(segment), rest, root, active_refs)
        if isinstance(segment, int) and not isinstance(segment, bool) and segment >= 0:
            if schema.get("type") != "array":
                return None
            return _literal_values_at_path(schema.get("items"), rest, root, active_refs)
        return None
    if "const" in schema:
        return (schema["const"],)
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return tuple(enum)
    if schema.get("type") == "null":
        return (None,)
    return None


def _numeric_ranges_at_path(
    schema: Any,
    path: tuple[str | int, ...],
    root: Mapping[str, Any],
    active_refs: frozenset[str],
) -> tuple[NumericRange, ...] | None:
    """Resolve numeric bounds through local refs and closed schema variants."""

    if not isinstance(schema, Mapping):
        return None
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in active_refs or not reference.startswith("#/$defs/"):
            return None
        definitions = root.get("$defs", {})
        target = definitions.get(reference.removeprefix("#/$defs/")) if isinstance(definitions, Mapping) else None
        return _numeric_ranges_at_path(target, path, root, active_refs | {reference})
    variants = schema.get("oneOf", schema.get("anyOf"))
    if isinstance(variants, list):
        ranges: list[NumericRange] = []
        for choice in variants:
            choice_ranges = _numeric_ranges_at_path(choice, path, root, active_refs)
            if choice_ranges is None:
                return None
            ranges.extend(choice_ranges)
        return tuple(ranges) if ranges else None
    if path:
        segment, rest = path[0], path[1:]
        if isinstance(segment, str) and schema.get("type") == "object":
            properties = schema.get("properties", {})
            if isinstance(properties, Mapping):
                return _numeric_ranges_at_path(properties.get(segment), rest, root, active_refs)
        if (
            isinstance(segment, int)
            and not isinstance(segment, bool)
            and segment >= 0
            and schema.get("type") == "array"
        ):
            return _numeric_ranges_at_path(schema.get("items"), rest, root, active_refs)
        return None
    schema_type = schema.get("type")
    allowed = set(schema_type) if isinstance(schema_type, list) else {schema_type}
    if not allowed or not allowed.issubset({"integer", "number"}):
        return None

    def boundary(keyword: str) -> JsonNumber | None:
        value = schema.get(keyword)
        return value if isinstance(value, int | float) and not isinstance(value, bool) else None

    lower_exclusive = "exclusiveMinimum" in schema
    upper_exclusive = "exclusiveMaximum" in schema
    lower = boundary("exclusiveMinimum" if lower_exclusive else "minimum")
    upper = boundary("exclusiveMaximum" if upper_exclusive else "maximum")
    return ((lower, lower_exclusive, upper, upper_exclusive),)


class _CatalogueProjector:
    """Project refs, unions, objects, and homogeneous arrays; stop at other structural vocabulary."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema
        definitions = schema.get("$defs", {})
        self.references = (
            {f"#/$defs/{key}": value for key, value in definitions.items()} if isinstance(definitions, dict) else {}
        )

    def project(self) -> DataContractNode:
        return self._project(self.schema, active_refs=frozenset())

    def _project(self, schema: Any, *, active_refs: frozenset[str]) -> DataContractNode:
        if not isinstance(schema, Mapping):
            return _UNKNOWN

        reference = schema.get("$ref")
        if reference is not None:
            if (
                not isinstance(reference, str)
                or reference in active_refs
                or _has_unsupported_structure(schema, allowed={"$ref", "$defs"})
            ):
                return _with_metadata(_UNKNOWN, schema)
            target = self.references.get(reference)
            if target is None:
                return _with_metadata(_UNKNOWN, schema)
            return _with_metadata(self._project(target, active_refs=active_refs | {reference}), schema)

        union = schema.get("anyOf", schema.get("oneOf"))
        if union is not None:
            union_key = "anyOf" if "anyOf" in schema else "oneOf"
            if (
                not isinstance(union, list)
                or not union
                or ("anyOf" in schema and "oneOf" in schema)
                or _has_unsupported_structure(schema, allowed={union_key, "$defs"})
            ):
                return _with_metadata(_UNKNOWN, schema)
            nullable = False
            variants: list[DataContractNode] = []
            for choice in union:
                if _is_null_schema(choice):
                    nullable = True
                else:
                    variants.append(self._project(choice, active_refs=active_refs))
            if not variants:
                return _with_metadata(DataContractNode("scalar", json_type="null", nullable=True), schema)
            return _with_metadata(_common_contract(variants, nullable=nullable), schema)

        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            concrete = [value for value in schema_type if value != "null"]
            if len(concrete) != 1 or not all(isinstance(value, str) for value in schema_type):
                return _with_metadata(_UNKNOWN, schema)
            projected = self._project({**schema, "type": concrete[0]}, active_refs=active_refs)
            return _with_nullable(projected, "null" in schema_type)

        if schema_type == "object":
            if _has_unsupported_structure(schema, allowed={"type", "properties", "$defs"}):
                return _with_metadata(_UNKNOWN, schema)
            properties = schema.get("properties")
            if not isinstance(properties, Mapping):
                return _with_metadata(_UNKNOWN, schema)
            fields: list[FieldEdge] = []
            for key, child in properties.items():
                if not isinstance(key, str):
                    return _with_metadata(_UNKNOWN, schema)
                fields.append(FieldEdge(key, self._project(child, active_refs=active_refs)))
            return _with_metadata(DataContractNode("object", fields=tuple(fields)), schema)

        if schema_type == "array":
            if _has_unsupported_structure(schema, allowed={"type", "items", "$defs"}):
                return _with_metadata(_UNKNOWN, schema)
            items = schema.get("items")
            if not isinstance(items, Mapping):
                return _with_metadata(_UNKNOWN, schema)
            return _with_metadata(
                DataContractNode("array", item=ItemEdge(self._project(items, active_refs=active_refs))), schema
            )

        if schema_type in {"string", "integer", "number", "boolean", "null"}:
            return _with_metadata(
                DataContractNode("scalar", json_type=schema_type, nullable=schema_type == "null"), schema
            )
        return _with_metadata(_UNKNOWN, schema)


def _common_contract(variants: Sequence[DataContractNode], *, nullable: bool) -> DataContractNode:
    first = variants[0]
    if any(variant.kind != first.kind for variant in variants):
        return DataContractNode("unknown", nullable=nullable)
    title = _common_value([variant.title for variant in variants])
    description = _common_value([variant.description for variant in variants])
    if first.kind == "object":
        field_maps = [{edge.key: edge.contract for edge in variant.fields} for variant in variants]
        common_keys = set(field_maps[0]).intersection(*(set(fields) for fields in field_maps[1:]))
        fields = tuple(
            FieldEdge(key, _common_contract([fields[key] for fields in field_maps], nullable=False))
            for key in field_maps[0]
            if key in common_keys
        )
        return DataContractNode(
            "object",
            title=title,
            description=description,
            nullable=nullable or any(variant.nullable for variant in variants),
            fields=fields,
        )
    if first.kind == "array":
        if any(variant.item is None for variant in variants):
            return DataContractNode("unknown", nullable=nullable)
        children = [variant.item.contract for variant in variants if variant.item is not None]
        return DataContractNode(
            "array",
            title=title,
            description=description,
            nullable=nullable or any(variant.nullable for variant in variants),
            item=ItemEdge(_common_contract(children, nullable=False)),
        )
    json_type = cast(JsonScalarType | None, _common_value([variant.json_type for variant in variants]))
    return DataContractNode(
        first.kind,
        json_type=json_type,
        title=title,
        description=description,
        nullable=nullable or any(variant.nullable for variant in variants),
    )


def _with_nullable(contract: DataContractNode, nullable: bool) -> DataContractNode:
    if not nullable or contract.nullable:
        return contract
    return DataContractNode(
        contract.kind,
        json_type=contract.json_type,
        title=contract.title,
        description=contract.description,
        nullable=True,
        fields=contract.fields,
        item=contract.item,
    )


def _with_metadata(contract: DataContractNode, schema: Mapping[str, Any]) -> DataContractNode:
    title = schema.get("title")
    description = schema.get("description")
    return DataContractNode(
        contract.kind,
        json_type=contract.json_type,
        title=title if isinstance(title, str) else contract.title,
        description=description if isinstance(description, str) else contract.description,
        nullable=contract.nullable,
        fields=contract.fields,
        item=contract.item,
    )


def _common_value[T](values: Sequence[T | None]) -> T | None:
    first = values[0]
    return first if first is not None and all(value == first for value in values[1:]) else None


_STRUCTURAL_KEYWORDS = frozenset(
    {
        "$ref",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "type",
        "properties",
        "patternProperties",
        "dependentSchemas",
        "propertyNames",
        "items",
        "prefixItems",
        "contains",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


def _has_unsupported_structure(schema: Mapping[str, Any], *, allowed: set[str]) -> bool:
    return bool((_STRUCTURAL_KEYWORDS & schema.keys()) - allowed)


def _is_null_schema(schema: Any) -> bool:
    return (
        isinstance(schema, Mapping)
        and schema.get("type") == "null"
        and not ((_STRUCTURAL_KEYWORDS & schema.keys()) - {"type"})
    )


_UNKNOWN = DataContractNode("unknown")
