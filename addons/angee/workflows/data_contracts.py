"""JSON Schema validation and bounded static workflow data contracts.

JSON Schema's validators check concrete instances; workflow publication must
also prove that a binding path exists for every value a producer can emit.
This analyser supplies that conservative static check over supported shapes,
while jsonschema owns declaration and format-aware runtime instance validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Mapping, Sequence, TypeAlias, cast

from django.core.exceptions import ValidationError
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.protocols import Validator
from pydantic import AfterValidator, BaseModel, Field
from referencing.jsonschema import SchemaRegistry

SchemaMode: TypeAlias = Literal["validation", "serialization"]
ConcretePath: TypeAlias = Sequence[str | int]
JsonScalarType: TypeAlias = Literal["string", "integer", "number", "boolean", "null"]
JsonNumber: TypeAlias = int | float
NumericRange: TypeAlias = tuple[JsonNumber | None, bool, JsonNumber | None, bool]
StringLengthRange: TypeAlias = tuple[int | None, int | None]


def _check_json_schema(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one JSON Schema declaration through the installed draft owner."""

    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:  # noqa: BLE001 - jsonschema reports several exception types.
        raise ValueError("Value is not valid JSON Schema.") from error
    return value


JsonPath: TypeAlias = Annotated[
    tuple[Annotated[str, Field(min_length=1)], ...],
    Field(min_length=1, description="Object keys and array indices encoded as decimal strings, such as '0'."),
]
JsonSchemaDict: TypeAlias = Annotated[dict[str, Any], AfterValidator(_check_json_schema)]
_FORMAT_CHECKER = FormatChecker()


def json_schema_validator(
    schema: Mapping[str, Any] | bool,
    *,
    validator_class: type[Validator] = Draft202012Validator,
    registry: SchemaRegistry | None = None,
) -> Validator:
    """Assert Draft 2020-12 instance constraints, including registered formats.

    Decision relation checks can supply their native validator extension and
    retained-reference registry without changing the shared validation policy.
    """

    # Preserve jsonschema's default warning-backed remote-reference registry;
    # Decision validation explicitly supplies an empty registry to forbid retrieval.
    options = {} if registry is None else {"registry": registry}
    return validator_class(schema, format_checker=_FORMAT_CHECKER, **options)


def _array_index(segment: str | int) -> int | None:
    """Decode a nonnegative integer or its canonical decimal string spelling."""

    if isinstance(segment, str):
        try:
            index = int(segment)
        except ValueError:
            return None
        return index if index >= 0 and str(index) == segment else None
    return segment if type(segment) is int and segment >= 0 else None


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

        return self.at_path(path) is not None

    def resolve_path(self, path: Sequence[str]) -> tuple[str | int, ...] | None:
        """Resolve config strings to typed segments, preserving object keys verbatim.

        Array indices use canonical decimal strings (``"0"``, ``"1"``, ...).
        Binding paths already carry typed indices and use ``at_path`` directly.
        """

        current = self
        resolved: list[str | int] = []
        for segment in path:
            part = _array_index(segment) if current.kind == "array" else segment
            if part is None or (child := current.at_path((part,))) is None:
                return None
            resolved.append(part)
            current = child
        return tuple(resolved)

    def at_path(self, path: ConcretePath) -> "DataContractNode | None":
        """Return the existing path catalogue node for bounded type checks."""

        current = self
        for segment in path:
            is_array_index = isinstance(segment, int) and not isinstance(segment, bool) and segment >= 0
            if current.kind == "object" and isinstance(segment, str):
                edge = next((edge for edge in current.fields if edge.key == segment), None)
                if edge is None:
                    return None
                current = edge.contract
            elif current.kind == "array" and is_array_index:
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
        return _literal_values_at_path(self.raw_schema, tuple(path), self.raw_schema, frozenset())

    def numeric_ranges_at_path(self, path: ConcretePath) -> tuple[NumericRange, ...] | None:
        """Return every declared numeric range at a bounded scalar path."""

        if self.raw_schema is None:
            return None
        return _numeric_ranges_at_path(self.raw_schema, tuple(path), self.raw_schema, frozenset())

    def string_length_ranges_at_path(self, path: ConcretePath) -> tuple[StringLengthRange, ...] | None:
        """Return every declared string-length range at a bounded scalar path."""

        if self.raw_schema is None:
            return None
        return _string_length_ranges_at_path(self.raw_schema, tuple(path), self.raw_schema, frozenset())

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


def json_value_at_path(value: Any, path: ConcretePath, *, field: str) -> Any:
    """Select JSON by object keys and nonnegative array indices.

    Config paths encode indices as canonical decimal strings (``"0"``, ``"1"``,
    ...); integer indices are also accepted. Numeric object keys remain strings.
    """

    current = value
    for part in path:
        if isinstance(part, str) and isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and (index := _array_index(part)) is not None and index < len(current):
            current = current[index]
        else:
            raise ValidationError({field: "The declared JSON path does not exist."})
    return current


@dataclass(frozen=True, slots=True)
class _SupportedSchemaNode:
    """One checked plain node and any schema variants intersected with it."""

    schema: Mapping[str, Any]
    variants: tuple[Any, ...]
    active_refs: frozenset[str]


def _supported_node(
    schema: Any,
    root: Mapping[str, Any],
    active_refs: frozenset[str],
) -> _SupportedSchemaNode | None:
    """Resolve local refs and admit only the structural vocabulary proofs handle."""

    if not isinstance(schema, Mapping):
        return None
    reference = schema.get("$ref")
    if reference is not None:
        if (
            not isinstance(reference, str)
            or reference in active_refs
            or not reference.startswith("#/$defs/")
            or _has_unsupported_structure(schema, allowed={"$ref"})
        ):
            return None
        definitions = root.get("$defs", {})
        target = definitions.get(reference.removeprefix("#/$defs/")) if isinstance(definitions, Mapping) else None
        return _supported_node(target, root, active_refs | {reference})

    one_of = schema.get("oneOf")
    any_of = schema.get("anyOf")
    if one_of is not None or any_of is not None:
        if one_of is not None and any_of is not None:
            return None
        key = "oneOf" if one_of is not None else "anyOf"
        variants = one_of if one_of is not None else any_of
        base = {name: value for name, value in schema.items() if name != key}
        if not isinstance(variants, list) or not variants or _has_unsupported_plain_structure(base):
            return None
        return _SupportedSchemaNode(base, tuple(variants), active_refs)

    if _has_unsupported_plain_structure(schema):
        return None
    return _SupportedSchemaNode(schema, (), active_refs)


def _guarantees_path(
    schema: Any, path: tuple[str | int, ...], root: Mapping[str, Any], active_refs: frozenset[str]
) -> bool:
    supported = _supported_node(schema, root, active_refs)
    if supported is None:
        return False
    schema = supported.schema
    active_refs = supported.active_refs
    if supported.variants:
        return _guarantees_path(schema, path, root, active_refs) or all(
            _guarantees_path(choice, path, root, active_refs) for choice in supported.variants
        )
    if not path:
        return True
    segment, rest = path[0], path[1:]
    if isinstance(segment, str):
        if schema.get("type") != "object":
            return False
        required, properties = schema.get("required", ()), schema.get("properties", {})
        return (
            isinstance(required, list | tuple)
            and segment in required
            and isinstance(properties, Mapping)
            and segment in properties
            and _guarantees_path(properties[segment], rest, root, active_refs)
        )
    if isinstance(segment, int) and not isinstance(segment, bool) and segment >= 0:
        return (
            schema.get("type") == "array"
            and type(schema.get("minItems", 0)) is int
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

    supported = _supported_node(schema, root, active_refs)
    if supported is None:
        return None
    schema = supported.schema
    active_refs = supported.active_refs
    if supported.variants:
        base_values = _literal_values_at_path(schema, path, root, active_refs)
        if base_values is not None:
            return base_values
        values: list[Any] = []
        for choice in supported.variants:
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

    supported = _supported_node(schema, root, active_refs)
    if supported is None:
        return None
    schema = supported.schema
    active_refs = supported.active_refs
    if supported.variants:
        base_ranges = _numeric_ranges_at_path(schema, path, root, active_refs)
        if base_ranges is not None:
            return base_ranges
        ranges: list[NumericRange] = []
        for choice in supported.variants:
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


def _string_length_ranges_at_path(
    schema: Any,
    path: tuple[str | int, ...],
    root: Mapping[str, Any],
    active_refs: frozenset[str],
) -> tuple[StringLengthRange, ...] | None:
    """Resolve string-length bounds through local refs and closed schema variants."""

    supported = _supported_node(schema, root, active_refs)
    if supported is None:
        return None
    schema = supported.schema
    active_refs = supported.active_refs
    if supported.variants:
        base_ranges = _string_length_ranges_at_path(schema, path, root, active_refs)
        if base_ranges is not None:
            return base_ranges
        ranges: list[StringLengthRange] = []
        for choice in supported.variants:
            choice_ranges = _string_length_ranges_at_path(choice, path, root, active_refs)
            if choice_ranges is None:
                return None
            ranges.extend(choice_ranges)
        return tuple(ranges) if ranges else None
    if path:
        segment, rest = path[0], path[1:]
        if isinstance(segment, str) and schema.get("type") == "object":
            properties = schema.get("properties", {})
            if isinstance(properties, Mapping):
                return _string_length_ranges_at_path(properties.get(segment), rest, root, active_refs)
        if (
            isinstance(segment, int)
            and not isinstance(segment, bool)
            and segment >= 0
            and schema.get("type") == "array"
        ):
            return _string_length_ranges_at_path(schema.get("items"), rest, root, active_refs)
        return None
    if schema.get("type") != "string":
        return None
    minimum = schema.get("minLength", 0)
    maximum = schema.get("maxLength")
    if type(minimum) is not int or minimum < 0 or maximum is not None and (
        type(maximum) is not int or maximum < 0
    ):
        return None
    return ((minimum, maximum),)


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


def _has_unsupported_plain_structure(schema: Mapping[str, Any]) -> bool:
    """Fail proof closed when an ordinary node carries unhandled structure."""

    allowed = {"type"}
    if schema.get("type") == "object":
        allowed.add("properties")
    elif schema.get("type") == "array":
        allowed.add("items")
    return _has_unsupported_structure(schema, allowed=allowed)


def _is_null_schema(schema: Any) -> bool:
    return (
        isinstance(schema, Mapping)
        and schema.get("type") == "null"
        and not ((_STRUCTURAL_KEYWORDS & schema.keys()) - {"type"})
    )


_UNKNOWN = DataContractNode("unknown")
