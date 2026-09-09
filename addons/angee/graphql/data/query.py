"""Finalize the resource query contract against the composed GraphQL schema.

Django and resource policy supply intent; graphql-core owns the final field and
input shapes. Native aggregate metadata supplies aliases and bucket predicates.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from django.db import connection, models
from strawberry_django_aggregates import TimeGranularity, group_by_alias
from strawberry_django_hasura.filtering import PORTABLE_LOOKUPS

from angee.data.field_classification import is_to_one_relation
from angee.data.metadata import (
    DataDefaultSortMetadata,
    DataQueryAxis,
    DataQueryDrill,
    DataQueryExtraction,
    DataQueryField,
    DataQueryFilter,
    DataQueryIdentity,
    DataQueryOrder,
    DataQueryRelation,
    DataQueryRow,
    DataQuerySort,
    DataQueryValueMap,
    DataResourceEnumValueMetadata,
    DataResourceFieldMetadata,
    DataResourceQuery,
    DataResourceTypeNames,
)
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.resource_fields import PREFERRED_DISPLAY_FIELDS, final_wire_field_names
from angee.graphql.introspection import FieldPathError, require_field_for_path
from graphql import (
    GraphQLEnumType,
    GraphQLField,
    GraphQLInputObjectType,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    get_named_type,
)

# Angee names are contract vocabulary; executable lookup support belongs to the
# Hasura evaluator. Several text conveniences intentionally share native LIKE.
_QUERY_OPERATORS = (
    ("exact", "_eq", "eq"),
    ("ne", "_neq", "neq"),
    ("gt", "_gt", "gt"),
    ("gte", "_gte", "gte"),
    ("lt", "_lt", "lt"),
    ("lte", "_lte", "lte"),
    ("inList", "_in", "in_"),
    ("notInList", "_nin", "nin"),
    ("isNull", "_is_null", "is_null"),
    ("contains", "_like", "like"),
    ("iContains", "_ilike", "ilike"),
    ("startsWith", "_like", "like"),
    ("iStartsWith", "_ilike", "ilike"),
    ("endsWith", "_like", "like"),
    ("iEndsWith", "_ilike", "ilike"),
    ("like", "_like", "like"),
    ("iLike", "_ilike", "ilike"),
    ("notLike", "_nlike", "nlike"),
    ("notILike", "_nilike", "nilike"),
    ("similar", "_similar", "similar"),
    ("notSimilar", "_nsimilar", "nsimilar"),
    ("regex", "_regex", "regex"),
    ("iRegex", "_iregex", "iregex"),
    ("notRegex", "_nregex", "nregex"),
    ("notIRegex", "_niregex", "niregex"),
    ("jsonContains", "_contains", "contains"),
    ("jsonContainedIn", "_contained_in", "contained_in"),
    ("hasKey", "_has_key", "has_key"),
    ("hasKeysAny", "_has_keys_any", "has_keys_any"),
    ("hasKeysAll", "_has_keys_all", "has_keys_all"),
)


@dataclass(frozen=True)
class ResourceQueryProjection:
    """Schema-bound owner of final query field, identity and axis projections."""

    schema: GraphQLSchema
    types: DataResourceTypeNames
    fields: tuple[DataResourceFieldMetadata, ...]
    identity: str
    filter_fields: tuple[str, ...] = ()
    order_fields: tuple[str, ...] = ()
    axes: tuple[DataQueryAxis, ...] = ()
    label_axes: dict[str, str] | None = None
    default_sort: tuple[DataDefaultSortMetadata, ...] = ()
    row_model: str = "server"
    filter_operators: tuple[str, ...] = ()
    model: type[models.Model] | None = None
    identity_policies: dict[str, str] | None = None

    def build(self) -> DataResourceQuery:
        """Resolve all projections once, retaining only executable capabilities."""

        fields = {field.name: self.field(field.name, field) for field in self.fields if field.readable}
        for name in (*self.filter_fields, *self.order_fields):
            canonical = self.canonical(name)
            descriptor = fields.get(canonical, self.field(canonical))
            comparison = self.comparison(name) if name in self.filter_fields else None
            operators = self.operators(comparison)
            fields[canonical] = replace(
                descriptor,
                filter=self.filter(name, comparison, operators)
                if comparison is not None and operators
                else descriptor.filter,
                sort=DataQueryOrder(name) if name in self.order_fields else descriptor.sort,
            )
        axes: dict[str, DataQueryAxis] = {}
        finalized_axes = self.final_axes()
        consumed_labels = set((self.label_axes or {}).values())
        for axis in finalized_axes:
            if axis.field in consumed_labels:
                continue
            canonical = self.canonical(axis.field)
            descriptor = self.field(canonical, axis=axis)
            existing = fields.get(canonical)
            if existing is not None:
                descriptor = replace(descriptor, filter=existing.filter, sort=existing.sort)
            fields[canonical] = descriptor
            relation = descriptor.relation
            identity_path, paths = self.row_paths(canonical)
            if relation:
                identity_path = relation.identity_path
                paths = (
                    tuple(path for path in (relation.identity_path, relation.label_path) if path)
                    if identity_path
                    else ()
                )
            label_axis = (self.label_axes or {}).get(axis.field)
            label = next((item for item in finalized_axes if item.field == label_axis), None)
            server = axis.server
            if server is not None and label is not None and label.server is not None:
                server = replace(server, label_input=label.server.input, label_key=label.server.key)
            axes[canonical] = replace(
                axis,
                field=canonical,
                server=server,
                identity_path=identity_path,
                paths=paths,
                label_path=relation.label_path if relation else None,
                drill=self.drill(axis.drill, fields),
                extractions=tuple(replace(item, drill=self.drill(item.drill, fields)) for item in axis.extractions),
            )
        if self.row_model == "client":
            canonical_labels = {self.canonical(label) for label in consumed_labels}
            for name, field in fields.items():
                if field.row is None:
                    fields[name] = replace(field, filter=None, sort=None)
                    continue
                if field.kind not in {"list", "object"} and name not in canonical_labels:
                    axes.setdefault(
                        name,
                        DataQueryAxis(
                            field=name,
                            extractions=tuple(
                                DataQueryExtraction(value.value, value.name, group_by_alias(name, value))
                                for value in TimeGranularity
                            )
                            if field.scalar in {"Date", "DateTime"}
                            else (),
                            kind="relation"
                            if field.relation
                            else "date"
                            if field.scalar in {"Date", "DateTime"}
                            else "json"
                            if field.scalar == "JSON"
                            else "column",
                            identity_path=field.row.path,
                            paths=tuple(
                                item for item in (field.relation.identity_path, field.relation.label_path) if item
                            )
                            if field.relation
                            else field.row.paths,
                            label_path=field.relation.label_path if field.relation else None,
                        ),
                    )
        return DataResourceQuery(
            identity=DataQueryIdentity(self.identity),
            fields=fields,
            axes=axes,
            sort=DataQuerySort(
                tuple(
                    replace(item, field=name)
                    for item in self.default_sort
                    if (name := self.canonical(item.field)) in fields and fields[name].sort is not None
                )
            ),
        )

    def final_axes(self) -> tuple[DataQueryAxis, ...]:
        """Intersect native axis intent with the composed input and output types."""

        spec = self.schema.get_type(self.types.group_by_spec) if self.types.group_by_spec else None
        key = self.schema.get_type(self.types.group_key) if self.types.group_key else None
        inputs = spec.fields if isinstance(spec, GraphQLInputObjectType) else {}
        keys = key.fields if isinstance(key, GraphQLObjectType) else {}
        field = get_named_type(inputs["field"].type) if "field" in inputs else None
        granularity = get_named_type(inputs["granularity"].type) if "granularity" in inputs else None
        accepted_fields = field.values if isinstance(field, GraphQLEnumType) else {}
        accepted_granularities = granularity.values if isinstance(granularity, GraphQLEnumType) else {}
        axes: list[DataQueryAxis] = []
        for axis in self.axes:
            if axis.server is None:
                axes.append(axis)
                continue
            if axis.server.input not in accepted_fields or axis.server.key not in keys:
                continue
            extractions: list[DataQueryExtraction] = []
            for extraction in axis.extractions:
                if extraction.input not in accepted_granularities or extraction.key not in keys:
                    continue
                range_field = keys.get(extraction.range_key) if extraction.range_key else None
                range_type = get_named_type(range_field.type) if range_field is not None else None
                range_valid = isinstance(range_type, GraphQLObjectType) and {"from", "to"} <= range_type.fields.keys()
                extractions.append(
                    extraction
                    if extraction.range_key is None or range_valid
                    else replace(extraction, range_key=None, drill=None)
                )
            axes.append(replace(axis, extractions=tuple(extractions)))
        return tuple(axes)

    def canonical(self, name: str) -> str:
        """Resolve every authored object-path segment through its final field alias."""

        node = self.schema.get_type(self.types.node) if self.types.node else None
        parts: list[str] = []
        for part in name.replace("__", ".").split("."):
            if isinstance(node, GraphQLObjectType):
                part = final_wire_field_names(self.schema, node.name, (part,))[0]
                selected = node.fields.get(part)
                node = get_named_type(selected.type) if selected is not None else None
            parts.append(part)
        return ".".join(parts)

    def selection(self, path: str) -> tuple[str | None, GraphQLField | None]:
        """Resolve a selectable path without inventing subfields for ID leaves."""

        node = self.schema.get_type(self.types.node) if self.types.node else None
        selected: list[str] = []
        field = None
        for part in path.replace("__", ".").split("."):
            if not isinstance(node, GraphQLObjectType):
                return None, None
            field = node.fields.get(part)
            if field is None:
                return None, None
            selected.append(part)
            node = get_named_type(field.type)
        return ".".join(selected), field

    def row_paths(self, path: str) -> tuple[str | None, tuple[str, ...]]:
        """Resolve row access and legal GraphQL selections, including JSON leaves."""

        selected, _ = self.selection(path)
        if selected:
            return selected, (selected,)
        parts = path.split(".")
        for size in range(1, len(parts)):
            prefix, field = self.selection(".".join(parts[:size]))
            if prefix and field is not None and getattr(get_named_type(field.type), "name", None) == "JSON":
                return path, (prefix,)
        return None, ()

    def comparison(self, name: str) -> GraphQLInputObjectType | None:
        input_type = self.schema.get_type(self.types.filter) if self.types.filter else None
        field = input_type.fields.get(name) if isinstance(input_type, GraphQLInputObjectType) else None
        comparison = get_named_type(field.type) if field is not None else None
        return comparison if isinstance(comparison, GraphQLInputObjectType) else None

    def filter(self, name: str, comparison: GraphQLInputObjectType, operators: tuple[str, ...]) -> DataQueryFilter:
        """Read the operand domain from the final comparison, not the output type."""

        value = comparison.fields.get("_eq")
        named = get_named_type(value.type) if value is not None else None
        values = (
            tuple(DataResourceEnumValueMetadata(key, item.description) for key, item in named.values.items())
            if isinstance(named, GraphQLEnumType)
            else ()
        )
        scalar = "Enum" if isinstance(named, GraphQLEnumType) else getattr(named, "name", "String")
        _, selected = self.selection(self.canonical(name))
        output = get_named_type(selected.type) if selected is not None else None
        value_map: list[DataQueryValueMap] = []
        if isinstance(output, GraphQLEnumType):
            for key, item in output.values.items():
                raw = getattr(item.value, "value", item.value)
                operand = (
                    next(
                        (key for key, item in named.values.items() if getattr(item.value, "value", item.value) == raw),
                        raw,
                    )
                    if isinstance(named, GraphQLEnumType)
                    else raw
                )
                if key != operand:
                    value_map.append(DataQueryValueMap(key, operand))
        return DataQueryFilter(
            field=name, operators=operators, scalar=scalar, values=values, value_map=tuple(value_map)
        )

    def operators(self, comparison: GraphQLInputObjectType | None) -> tuple[str, ...]:
        if comparison is None:
            return ()
        executable = PORTABLE_LOOKUPS | {"is_null"}
        if self.row_model != "client":
            executable |= set(self.filter_operators)
        if self.model is not None and not connection.features.supports_json_field_contains:
            executable = executable - {"contains"}
        return tuple(
            name for name, wire, lookup in _QUERY_OPERATORS if wire in comparison.fields and lookup in executable
        )

    def field(
        self, name: str, display: DataResourceFieldMetadata | None = None, *, axis: DataQueryAxis | None = None
    ) -> DataQueryField:
        display = display or next(
            (field for field in self.fields if field.name == name or field.model_field_name == name), None
        )
        path, selected = self.selection(name)
        named = get_named_type(selected.type) if selected is not None else None
        if named is None:
            comparison = self.comparison(name)
            value = comparison.fields.get("_eq") if comparison is not None else None
            if value is not None:
                named = get_named_type(value.type)
            elif axis is not None and axis.server is not None and self.types.group_key:
                key_type = self.schema.get_type(self.types.group_key)
                key = key_type.fields.get(axis.server.key) if isinstance(key_type, GraphQLObjectType) else None
                named = get_named_type(key.type) if key is not None else None
        source_field = None
        if self.model is not None:
            try:
                source_field = require_field_for_path(self.model, axis.field if axis else name.replace(".", "__"))
            except FieldPathError:
                pass  # A final computed/aliased scalar has no model field.
        relation_field = is_to_one_relation(source_field) if source_field is not None else False
        kind = (
            display.kind
            if display
            else "enum"
            if isinstance(named, GraphQLEnumType)
            else "relation"
            if relation_field or axis and axis.kind == "relation"
            else "scalar"
        )
        scalar = display.scalar if display else getattr(named, "name", None)
        if scalar == "JSON":
            kind = "json"
        values = (
            display.values
            if display
            else tuple(DataResourceEnumValueMetadata(value) for value in named.values)
            if isinstance(named, GraphQLEnumType)
            else ()
        )
        model_label = display.relation_model_label if display else None
        # GenericForeignKey has no single related model. Its authored object
        # projection must not acquire a fabricated resource relation.
        if model_label is None and relation_field and source_field.related_model is not None:
            model_label = source_field.related_model._meta.label
        relation = self.relation(path, named, model_label) if kind == "relation" else None
        row_path, row_paths = self.row_paths(name)
        if relation is not None:
            row_path = relation.identity_path
            row_paths = (row_path,) if row_path else ()
        elif kind == "object" or isinstance(named, GraphQLObjectType):
            row_path, row_paths = None, ()
        return DataQueryField(
            kind=kind,
            scalar="ID" if kind == "relation" else scalar,
            values=values,
            nullable=not isinstance(selected.type, GraphQLNonNull) if selected is not None else True,
            relation=relation,
            row=DataQueryRow(row_path, row_paths) if row_path else None,
        )

    def relation(self, path: str | None, node: object, model: str | None) -> DataQueryRelation | None:
        if model is None:
            return None
        if path is None or not isinstance(node, GraphQLObjectType):
            return DataQueryRelation(model=model, identity_path=path)
        identities = [
            name for name, field in node.fields.items() if getattr(get_named_type(field.type), "name", None) == "ID"
        ]
        declared = (self.identity_policies or {}).get(node.name, PUBLIC_ID_FIELD_NAME)
        mapped = final_wire_field_names(self.schema, node.name, (declared,))[0]
        node_identity = tuple(
            name
            for interface in node.interfaces
            if interface.name == "Node"
            for name, field in interface.fields.items()
            if getattr(get_named_type(field.type), "name", None) == "ID"
        )
        identity = mapped if mapped in identities else node_identity[0] if len(node_identity) == 1 else None
        labels = [
            name for name, field in node.fields.items() if getattr(get_named_type(field.type), "name", None) == "String"
        ]
        label = next((name for name in PREFERRED_DISPLAY_FIELDS if name in labels), labels[0] if labels else None)
        return DataQueryRelation(
            model=model,
            identity_path=f"{path}.{identity}" if identity else None,
            label_path=f"{path}.{label}" if label else None,
        )

    def drill(self, drill: DataQueryDrill | None, fields: dict[str, DataQueryField]) -> DataQueryDrill | None:
        if drill is None:
            return None
        name = self.canonical(drill.field)
        field = fields.get(name)
        required = {"gte", "lt"} if drill.kind == "range" else {"jsonContains"} if drill.kind == "json" else {"exact"}
        if field is None or field.filter is None or not required.issubset(field.filter.operators):
            return None
        null_mode = drill.null_mode
        if null_mode == "isNull" and "isNull" not in field.filter.operators:
            null_mode = "unavailable"
        return replace(drill, field=name, null_mode=null_mode)
