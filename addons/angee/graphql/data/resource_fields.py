"""GraphQL/Django projection into neutral resource-field descriptions."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry.types import get_object_definition
from strawberry.types.base import StrawberryList, StrawberryOptional
from strawberry.types.enum import StrawberryEnumDefinition
from strawberry.types.lazy_type import LazyType
from strawberry_django.utils.typing import get_django_definition
from strawberry_django_hasura import SnakeNameConverter

from angee.base.impl import ImplClassField
from angee.data import metadata as data_contract
from angee.data.field_classification import (
    RESOURCE_FIELD_KINDS as _RESOURCE_FIELD_KINDS,
)
from angee.data.field_classification import (
    RESOURCE_FIELD_SCALARS as _RESOURCE_FIELD_SCALARS,
)
from angee.data.field_classification import (
    is_archive_field,
    is_resource_field_widget,
    money_currency_field,
    resource_field_kind,
    resource_field_widget,
)
from angee.graphql.introspection import surface_field_names, surface_name
from graphql import (
    GraphQLEnumType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLScalarType,
    GraphQLSchema,
    get_named_type,
)

#: Backend-owned display-field precedence, shared by record representation and
#: the relation group-label fallback.
PREFERRED_DISPLAY_FIELDS: tuple[str, ...] = (
    "title",
    "name",
    "displayName",
    "display_name",
    "fullName",
    "full_name",
    "label",
    "username",
    "email",
    "slug",
)


# The schema is built with ``hasura_config()`` (``angee/graphql/schema.py``); its
# ``SnakeNameConverter`` owns the python-name -> wire-name rule, keeping snake_case
# verbatim unless a field pins an explicit ``graphql_name``. The metadata the
# frontend codegen reads must name every field exactly as the schema does, so it
# asks the same converter instead of re-deriving the rule.
_WIRE_NAME_CONVERTER = SnakeNameConverter()


def resource_wire_field_name(surface: type | None, name: str | None) -> str | None:
    """Return the actual GraphQL wire field name for a Strawberry surface field."""

    if surface is None or name is None:
        return None
    definition = get_object_definition(surface)
    if definition is not None:
        for field in definition.fields:
            if field.python_name == name:
                return _wire_field_name(field)
    return name


def resource_wire_field_names(surface: type | None, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return all declared GraphQL wire field names for a Strawberry surface."""

    if surface is None:
        return ()
    excluded = set(exclude)
    return tuple(
        resource_wire_field_name(surface, name) or name for name in surface_field_names(surface) if name not in excluded
    )


def resource_type_name(surface: type | None) -> str | None:
    """Return the GraphQL type name for ``surface`` when present."""

    if surface is None:
        return None
    definition = get_object_definition(surface)
    if definition is not None:
        return str(definition.name)
    definition = getattr(surface, "__strawberry_definition__", None)
    if definition is not None:
        return str(definition.name)
    return surface_name(surface)


def resource_relation_surface(surface: type | None, name: str) -> type | None:
    """Return the object surface projected by one to-one field, if any."""

    value = _surface_field_type(surface, name)
    try:
        related_surface, is_list = _selection_surface(value)
    except NotImplementedError:
        return None
    if is_list or not isinstance(related_surface, type) or get_object_definition(related_surface) is None:
        return None
    return related_surface


def resource_string_field_names(surface: type | None) -> tuple[str, ...]:
    """Return projected Python field names whose native surface type is String."""

    definition = get_object_definition(surface) if surface is not None else None
    if definition is None:
        return ()
    names: list[str] = []
    for field in definition.fields:
        try:
            value = field.type
        except NotImplementedError:
            continue
        while isinstance(value, StrawberryOptional):
            value = value.of_type
        if isinstance(value, StrawberryList | StrawberryEnumDefinition):
            continue
        if isinstance(value, LazyType):
            value = value.resolve_type()
        scalar_definition = getattr(value, "_scalar_definition", None)
        scalar_name = getattr(scalar_definition, "name", None)
        if value is str or scalar_name == "String":
            names.append(str(field.python_name))
    return tuple(names)


def final_resource_fields(
    schema: GraphQLSchema,
    node_name: str,
    model: type[models.Model] | None,
    *,
    aggregate_fields: tuple[str, ...],
    create_fields: tuple[str, ...],
    update_fields: tuple[str, ...],
    required_create_fields: tuple[str, ...],
) -> tuple[data_contract.DataResourceFieldMetadata, ...]:
    """Project fields from the composed schema's final node map.

    Strawberry keeps the owning field on graphql-core's ``strawberry-definition``
    extension. That source link supplies the Python/model name for aliases while
    graphql-core supplies the actual post-extension wire type and enum values.
    """

    node = schema.get_type(node_name)
    if not isinstance(node, GraphQLObjectType):
        raise ImproperlyConfigured(f"resource metadata node type {node_name!r} is absent from the composed schema.")
    aggregatable = set(aggregate_fields)
    creatable = set(create_fields)
    updatable = set(update_fields)
    required_on_create = set(required_create_fields)
    projected: list[data_contract.DataResourceFieldMetadata] = []
    for name, graphql_field in node.fields.items():
        source = (graphql_field.extensions or {}).get("strawberry-definition")
        python_name = str(getattr(source, "python_name", None) or name)
        model_field = _model_field_or_none(model, python_name)
        named = get_named_type(graphql_field.type)
        is_list = _graphql_type_is_list(graphql_field.type)
        is_enum = isinstance(named, GraphQLEnumType)
        is_object = isinstance(named, GraphQLObjectType)
        relation_model_label = _relation_model_label(model_field) or _graphql_relation_model_label(named)
        kind = resource_field_kind(
            model_field,
            has_relation_axis=relation_model_label is not None,
            is_list=is_list,
            is_enum=is_enum,
            is_object=is_object,
        )
        scalar = _graphql_scalar(named, kind=kind, field_name=name, node_name=node_name)
        values = _graphql_enum_values(model_field, named) if kind == "enum" else ()
        projected.append(
            data_contract.DataResourceFieldMetadata(
                name=name,
                kind=kind,
                scalar=scalar,
                values=values,
                widget=_projected_widget(model_field, kind, scalar),
                aggregatable=name in aggregatable or python_name in aggregatable,
                creatable=name in creatable or python_name in creatable,
                updatable=name in updatable or python_name in updatable,
                required_on_create=name in required_on_create or python_name in required_on_create,
                archivable=is_archive_field(model_field),
                currency_field=money_currency_field(model_field),
                relation_model_label=relation_model_label,
                relation_object=kind == "relation" and is_object,
                model_field_name=python_name if model_field is not None else None,
            )
        )
    return tuple(projected)


def final_input_only_resource_fields(
    schema: GraphQLSchema,
    *,
    create_input_name: str | None,
    update_input_name: str | None,
    model: type[models.Model] | None,
    aggregate_fields: tuple[str, ...],
    create_fields: tuple[str, ...],
    update_fields: tuple[str, ...],
    required_create_fields: tuple[str, ...],
    readable_fields: tuple[data_contract.DataResourceFieldMetadata, ...],
) -> tuple[data_contract.DataResourceFieldMetadata, ...]:
    """Project accepted final input fields absent from the readable node."""

    aggregatable = set(aggregate_fields)
    create = set(create_fields)
    update = set(update_fields)
    required = set(required_create_fields)
    readable_sources = {field.model_field_name or field.name for field in readable_fields}
    candidates: dict[str, tuple[str, Any]] = {}
    for type_name, accepted in (
        (create_input_name, create),
        (update_input_name, update),
    ):
        input_type = schema.get_type(type_name) if type_name else None
        fields = getattr(input_type, "fields", None)
        if not isinstance(fields, dict):
            continue
        for wire_name, graphql_field in fields.items():
            if wire_name not in accepted:
                continue
            source = (graphql_field.extensions or {}).get("strawberry-definition")
            python_name = str(getattr(source, "python_name", None) or wire_name)
            candidates.setdefault(wire_name, (python_name, graphql_field))
    projected: list[data_contract.DataResourceFieldMetadata] = []
    for name, (python_name, graphql_field) in candidates.items():
        if python_name in readable_sources:
            continue
        model_field = _model_field_or_none(model, python_name)
        named = get_named_type(graphql_field.type)
        is_list = _graphql_type_is_list(graphql_field.type)
        is_enum = isinstance(named, GraphQLEnumType)
        is_object = isinstance(named, GraphQLObjectType)
        kind = resource_field_kind(
            model_field,
            is_list=is_list,
            is_enum=is_enum,
            is_object=is_object,
        )
        scalar = _graphql_scalar(named, kind=kind, field_name=name, node_name="input")
        projected.append(
            data_contract.DataResourceFieldMetadata(
                name=name,
                kind=kind,
                scalar=scalar,
                values=_graphql_enum_values(model_field, named) if kind == "enum" else (),
                widget=_projected_widget(model_field, kind, scalar),
                readable=False,
                aggregatable=name in aggregatable or python_name in aggregatable,
                creatable=name in create,
                updatable=name in update,
                required_on_create=name in required,
                archivable=is_archive_field(model_field),
                currency_field=money_currency_field(model_field),
                relation_model_label=_relation_model_label(model_field),
                relation_object=False,
                model_field_name=python_name if model_field is not None else None,
            )
        )
    return tuple(projected)


def final_wire_field_names(
    schema: GraphQLSchema,
    node_name: str,
    names: tuple[str, ...],
) -> tuple[str, ...]:
    """Map direct authored Python field names through final Strawberry aliases."""

    node = schema.get_type(node_name)
    fields = getattr(node, "fields", None)
    if not isinstance(fields, dict):
        return names
    by_source: dict[str, str] = {}
    for wire_name, graphql_field in fields.items():
        source = (graphql_field.extensions or {}).get("strawberry-definition")
        python_name = str(getattr(source, "python_name", None) or wire_name)
        by_source[python_name] = wire_name
    mapped: list[str] = []
    for path in names:
        separator = "__" if "__" in path else "." if "." in path else None
        if separator is None:
            mapped.append(by_source.get(path, path))
            continue
        head, tail = path.split(separator, 1)
        mapped.append(f"{by_source.get(head, head)}{separator}{tail}")
    return tuple(mapped)


def final_input_policy_fields(
    schema: GraphQLSchema,
    input_name: str | None,
    *,
    accepted: tuple[str, ...],
) -> tuple[str, ...]:
    """Map and validate authored read-policy paths against their final input."""

    if input_name is None:
        return ()
    input_type = schema.get_type(input_name)
    fields = getattr(input_type, "fields", None)
    if not isinstance(fields, dict):
        return ()
    by_source: dict[str, str] = {}
    for wire_name, input_field in fields.items():
        source = (input_field.extensions or {}).get("strawberry-definition")
        python_name = str(getattr(source, "python_name", None) or wire_name)
        by_source[python_name] = wire_name
    projected: list[str] = []
    for path in accepted:
        separator = "__" if "__" in path else "." if "." in path else None
        head, tail = path.split(separator, 1) if separator is not None else (path, "")
        wire_head = by_source.get(head)
        if wire_head is None:
            continue
        projected.append(wire_head if separator is None else f"{wire_head}{separator}{tail}")
    return tuple(projected)


def final_aggregate_wire_fields(
    schema: GraphQLSchema,
    aggregate_container_name: str | None,
    *,
    accepted: tuple[str, ...],
) -> tuple[str, ...]:
    """Map authored aggregate columns through the final native aggregate types."""

    container = schema.get_type(aggregate_container_name) if aggregate_container_name else None
    container_fields = getattr(container, "fields", None)
    aggregate_field = container_fields.get("aggregate") if isinstance(container_fields, dict) else None
    aggregate = get_named_type(aggregate_field.type) if aggregate_field is not None else None
    operation_fields = getattr(aggregate, "fields", None)
    by_source: dict[str, str] = {}
    if isinstance(operation_fields, dict):
        for operation in operation_fields.values():
            columns = getattr(get_named_type(operation.type), "fields", None)
            if isinstance(columns, dict):
                for wire_name, column in columns.items():
                    source = (column.extensions or {}).get("strawberry-definition")
                    python_name = str(getattr(source, "python_name", None) or wire_name)
                    by_source[python_name] = wire_name
            for argument in operation.args.values():
                enum_type = get_named_type(argument.type)
                if not isinstance(enum_type, GraphQLEnumType):
                    continue
                for enum_value in enum_type.values.values():
                    value = str(enum_value.value)
                    by_source[value] = value
    return tuple(by_source[name] for name in accepted if name in by_source)


def final_input_wire_fields(
    schema: GraphQLSchema,
    input_name: str | None,
    *,
    accepted: tuple[str, ...],
    exclude: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Return final executable input fields restricted by authored write policy."""

    if input_name is None:
        return ()
    input_type = schema.get_type(input_name)
    fields = getattr(input_type, "fields", None)
    if not isinstance(fields, dict):
        raise ImproperlyConfigured(f"resource metadata input type {input_name!r} is absent from the composed schema.")
    excluded = set(exclude)
    by_source: dict[str, str] = {}
    for wire_name, input_field in fields.items():
        source = (input_field.extensions or {}).get("strawberry-definition")
        python_name = str(getattr(source, "python_name", None) or wire_name)
        by_source[python_name] = wire_name
    return tuple(
        wire_name for name in accepted if (wire_name := by_source.get(name)) is not None and wire_name not in excluded
    )


def final_required_input_wire_fields(
    schema: GraphQLSchema,
    input_name: str | None,
    *,
    accepted: tuple[str, ...],
) -> tuple[str, ...]:
    """Return accepted final inputs required by GraphQL coercion."""

    from graphql import Undefined

    if input_name is None:
        return ()
    input_type = schema.get_type(input_name)
    fields = getattr(input_type, "fields", None)
    if not isinstance(fields, dict):
        return ()
    return tuple(
        name
        for name in accepted
        if name in fields and isinstance(fields[name].type, GraphQLNonNull) and fields[name].default_value is Undefined
    )


def require_final_selection_path(
    schema: GraphQLSchema,
    node_name: str,
    path: str,
    *,
    model_label: str,
    fact: str,
) -> None:
    """Validate a dotted selection against the composed schema."""

    current = schema.get_type(node_name)
    parts = path.split(".")
    for index, part in enumerate(parts):
        fields = getattr(current, "fields", None)
        field = fields.get(part) if isinstance(fields, dict) else None
        if field is None:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares {fact} selection path "
                f"{path!r} with unknown field {part!r}."
            )
        named = get_named_type(field.type)
        if index == len(parts) - 1:
            if isinstance(named, GraphQLObjectType):
                raise ImproperlyConfigured(
                    f"resource metadata for {model_label} declares {fact} selection path "
                    f"{path!r} ending at object field {part!r}; declare a scalar subfield instead."
                )
            return
        if _graphql_type_is_list(field.type) or not isinstance(named, GraphQLObjectType):
            traversed = ".".join(parts[: index + 1])
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares {fact} selection path "
                f"{path!r} through non-object field {traversed!r}."
            )
        current = named


def _graphql_type_is_list(value: object) -> bool:
    while isinstance(value, GraphQLNonNull):
        value = value.of_type
    return isinstance(value, GraphQLList)


def _graphql_scalar(value: object, *, kind: str, field_name: str, node_name: str) -> str | None:
    if kind in {"relation", "enum", "object"}:
        return None
    if not isinstance(value, GraphQLScalarType):
        if kind == "list":
            return None
        raise ImproperlyConfigured(
            f"resource metadata for {node_name} cannot classify GraphQL scalar for field {field_name!r}."
        )
    scalar = value.name
    if scalar not in _RESOURCE_FIELD_SCALARS:
        raise ImproperlyConfigured(
            f"resource metadata for {node_name} cannot classify GraphQL scalar for field {field_name!r} ({scalar})."
        )
    return scalar


def _graphql_enum_values(
    field: models.Field[Any, Any] | None,
    value: object,
) -> tuple[data_contract.DataResourceEnumValueMetadata, ...]:
    if not isinstance(value, GraphQLEnumType):
        return ()
    labels = _field_choice_labels(field)
    result: list[data_contract.DataResourceEnumValueMetadata] = []
    for name, enum_value in value.values.items():
        raw = getattr(enum_value.value, "value", enum_value.value)
        description = labels.get(str(raw)) or enum_value.description
        result.append(
            data_contract.DataResourceEnumValueMetadata(
                value=name,
                description=str(description) if description is not None and str(description).strip() else None,
            )
        )
    return tuple(result)


def _graphql_relation_model_label(value: object) -> str | None:
    """Return the Django owner retained by a final object type's source link."""

    if not isinstance(value, GraphQLObjectType):
        return None
    definition = (value.extensions or {}).get("strawberry-definition")
    origin = getattr(definition, "origin", None)
    django_definition = get_django_definition(origin) if isinstance(origin, type) else None
    return django_definition.model._meta.label if django_definition is not None else None


def require_unique_resource_fields(
    model_label: str,
    fields: tuple[data_contract.DataResourceFieldMetadata, ...],
) -> tuple[data_contract.DataResourceFieldMetadata, ...]:
    """Return resource field metadata after rejecting duplicate field names."""

    seen: set[str] = set()
    for field in fields:
        if field.name in seen:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares duplicate resource field '{field.name}'."
            )
        seen.add(field.name)
        _validate_resource_field(model_label, field)
    return fields


def _validate_resource_field(model_label: str, field: data_contract.DataResourceFieldMetadata) -> None:
    """Reject impossible explicit resource field metadata."""

    if field.kind not in _RESOURCE_FIELD_KINDS:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' declares unsupported kind '{field.kind}'."
        )
    if field.scalar is not None and field.scalar not in _RESOURCE_FIELD_SCALARS:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' declares unsupported scalar '{field.scalar}'."
        )
    if field.widget is not None and not is_resource_field_widget(field.widget):
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' declares unsupported widget '{field.widget}'."
        )
    if field.kind in {"enum", "relation"} and field.scalar is not None:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' cannot declare "
            f"scalar '{field.scalar}' for {field.kind} fields."
        )
    # A namespaced addon widget changes presentation, never the relation's
    # target, wire selection, filters or public-ID semantics.
    custom_widget = field.widget is not None and "." in field.widget
    if field.kind == "relation" and field.widget not in {None, "many2one"} and not custom_widget:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' cannot declare "
            f"widget '{field.widget}' for relation fields."
        )
    if field.kind == "enum" and field.widget not in {None, "select"}:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' cannot declare "
            f"widget '{field.widget}' for enum fields."
        )


def _projected_widget(field: models.Field[Any, Any] | None, kind: str, scalar: str | None) -> str | None:
    """Return the rendered widget for a projected surface field.

    A plain ``ID`` scalar (a record's own public id) renders no widget. A to-one
    relation exposed as an ID leaf keeps its Django ``many2one`` widget because
    relation semantics are independent of the GraphQL selection shape.
    """

    if scalar == "ID" and not (field is not None and field.is_relation):
        return None
    return resource_field_widget(field, kind)


def _relation_model_label(
    field: models.Field[Any, Any] | None,
) -> str | None:
    if field is None or not field.is_relation:
        return None
    remote_field = getattr(field, "remote_field", None)
    remote_model = getattr(remote_field, "model", None)
    meta = getattr(remote_model, "_meta", None)
    return str(meta.label) if meta is not None else None


def _wire_field_name(field: Any) -> str:
    """Return the GraphQL wire name the schema gives one Strawberry field."""

    return str(_WIRE_NAME_CONVERTER.get_graphql_name(field))


def _surface_field_type(surface: type | None, name: str) -> object | None:
    """Return the Strawberry type object for ``name`` when the surface exposes it."""

    if surface is None:
        return None
    definition = get_object_definition(surface)
    if definition is None:
        return None
    for field in definition.fields:
        if field.python_name == name:
            try:
                return field.type
            except NotImplementedError as exc:
                raise ImproperlyConfigured(
                    f"resource metadata for {surface_name(surface)} cannot resolve "
                    f"GraphQL type for field '{name}': {exc}"
                ) from exc
    return None


def _selection_surface(value: object) -> tuple[object, bool]:
    """Return a selection field's unwrapped type and whether it crossed a list."""

    is_list = False
    while isinstance(value, StrawberryOptional | StrawberryList):
        if isinstance(value, StrawberryList):
            is_list = True
        value = value.of_type
    if isinstance(value, LazyType):
        value = value.resolve_type()
    return value, is_list


def _field_choice_labels(field: models.Field[Any, Any] | None) -> dict[str, str]:
    """Return a stored-value -> human-label map for an enum-backed field, or empty.

    An ``ImplClassField`` owns registry labels keyed by impl key; any other
    ``TextChoicesField`` (a ``StateField`` or a plain choices enum) carries its
    labels on the ``choices_enum`` members. A field with no enum choices yields an
    empty map, so a non-enum field folds nothing.
    """

    if isinstance(field, ImplClassField):
        return {str(choice.key): str(choice.label) for choice in field.impl_choices()}
    choices_enum = getattr(field, "choices_enum", None)
    if choices_enum is None:
        return {}
    return {str(member.value): str(member.label) for member in choices_enum}


def _model_field_or_none(model: type[models.Model] | None, name: str) -> models.Field[Any, Any] | None:
    """Return a Django model field for ``name`` when one owns that GraphQL field."""

    if model is None:
        return None
    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None
