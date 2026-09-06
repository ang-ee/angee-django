"""GraphQL projection and attachment of neutral data-surface descriptions."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from rebac.resources import model_resource_type
from strawberry_django_hasura import HasuraResource

from angee.base.impl import ImplClassField
from angee.base.refs import canonical_record_model
from angee.data import metadata as data_contract
from angee.data.field_classification import is_to_one_relation, model_field_scalar
from angee.graphql.access import is_gated_read_axis
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.final_schema import final_schema_references
from angee.graphql.data.resource_fields import (
    final_aggregate_wire_fields,
    final_input_only_resource_fields,
    final_input_policy_fields,
    final_input_wire_fields,
    final_required_input_wire_fields,
    final_resource_fields,
    final_wire_field_names,
    require_final_selection_path,
    require_unique_resource_fields,
    resource_relation_surface,
    resource_string_field_names,
    resource_type_name,
    resource_wire_field_name,
    resource_wire_field_names,
)
from angee.graphql.introspection import (
    FieldPathError,
    require_field_for_path,
)
from graphql import GraphQLEnumType, GraphQLSchema, get_named_type

__all__ = [
    "attach_data_resource_contribution",
    "data_resource_contributions",
    "DataResourceContribution",
    "DataResourcePolicy",
    "finalize_data_resources",
    "readable_model_field_names",
    "relation_group_by_fields",
    "resource_type_name",
    "resource_wire_field_name",
    "resource_wire_field_names",
]

DATA_RESOURCE_CONTRIBUTIONS_ATTR = "__angee_data_resource_contributions__"


@dataclass(frozen=True, slots=True)
class DataResourcePolicy:
    """Angee-only resource policy not recoverable from a composed schema."""

    filter_fields: tuple[str, ...] | None = None
    order_fields: tuple[str, ...] | None = None
    aggregate_fields: tuple[str, ...] | None = None
    group_by_fields: tuple[str, ...] | None = None
    group_dimensions: tuple[data_contract.DataGroupDimensionMetadata, ...] | None = None
    aggregate_measures: tuple[data_contract.DataAggregateMeasureMetadata, ...] | None = None
    default_measures: tuple[data_contract.DataAggregateMeasureMetadata, ...] | None = None
    revision_fields: tuple[str, ...] | None = None
    group_aliases: tuple[data_contract.DataGroupAliasMetadata, ...] | None = None
    lines_declaration: object | None = None
    subtitle: data_contract.DataResourceSubtitleMetadata | None = None
    public_id_field: str | None = None
    row_model: str | None = None


@dataclass(frozen=True, slots=True)
class DataResourceContribution:
    """Native/authored GraphQL references plus a small Angee policy delta."""

    model: type[models.Model] | None
    model_label: str
    origin: str = ""
    native_resource: HasuraResource | None = None
    roots: data_contract.DataResourceRoots = field(default_factory=data_contract.DataResourceRoots)
    type_names: data_contract.DataResourceTypeNames = field(default_factory=data_contract.DataResourceTypeNames)
    capabilities: tuple[str, ...] = ()
    policy: DataResourcePolicy = field(default_factory=DataResourcePolicy)


def data_resource_contributions(surface: object) -> tuple[DataResourceContribution, ...]:
    """Return deferred resource contributions attached to ``surface``."""

    value = getattr(surface, DATA_RESOURCE_CONTRIBUTIONS_ATTR, ())
    return value if isinstance(value, tuple) else ()


def attach_data_resource_contribution(
    surface: type[_SurfaceT], contribution: DataResourceContribution
) -> type[_SurfaceT]:
    """Attach one deferred contribution with its declaring origin."""

    origin = resource_type_name(surface) or surface.__name__
    existing = data_resource_contributions(surface)
    setattr(
        surface,
        DATA_RESOURCE_CONTRIBUTIONS_ATTR,
        existing + (dataclasses.replace(contribution, origin=origin),),
    )
    return surface


def finalize_data_resources(
    schema: GraphQLSchema,
    surfaces: tuple[object, ...],
) -> tuple[data_contract.DataResourceMetadata, ...]:
    """Create one neutral description per model label from the composed schema."""

    by_label: dict[str, list[DataResourceContribution]] = {}
    for surface in surfaces:
        for contribution in data_resource_contributions(surface):
            by_label.setdefault(contribution.model_label, []).append(contribution)

    finalized: list[data_contract.DataResourceMetadata] = []
    for model_label, contributions in by_label.items():
        first = contributions[0]
        for contribution in contributions[1:]:
            if contribution.model is not first.model:
                raise ImproperlyConfigured(
                    f"resource metadata model label {model_label!r} is contributed by "
                    f"different model owners ({first.origin}, {contribution.origin})."
                )
        native_items = [item for item in contributions if item.native_resource is not None]
        native_resource = native_items[0].native_resource if native_items else None
        if any(item.native_resource is not native_resource for item in native_items[1:]):
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} has multiple native resource owners."
            )
        native_roots = data_contract.DataResourceRoots()
        native_type_names = data_contract.DataResourceTypeNames()
        create_fields: tuple[str, ...] = ()
        update_fields: tuple[str, ...] = ()
        lines: data_contract.DataLinesMetadata | None = None
        if native_resource is not None:
            query = native_resource.query
            mutation = native_resource.mutation
            query_fields = schema.query_type.fields if schema.query_type is not None else {}
            mutation_fields = schema.mutation_type.fields if schema.mutation_type is not None else {}

            def exposed(surface: type, python_name: str | None, fields: dict[str, Any]) -> str | None:
                wire_name = resource_wire_field_name(surface, python_name)
                return wire_name if wire_name in fields else None

            list_name = exposed(query, native_resource.list_root, query_fields)
            detail_name = exposed(query, native_resource.detail_root, query_fields)
            aggregate_name = exposed(query, native_resource.aggregate_root, query_fields)
            group_name = exposed(query, native_resource.groups_root, query_fields)
            group_count_name = exposed(query, native_resource.groups_count_root, query_fields)
            create_name = exposed(mutation, native_resource.insert_one_root, mutation_fields)
            update_name = exposed(mutation, native_resource.update_by_pk_root, mutation_fields)
            delete_name = exposed(mutation, native_resource.delete_by_pk_root, mutation_fields)
            save_name = _merge_description_values(
                model_label, contributions, "roots", data_contract.DataResourceRoots
            ).save_name
            if save_name not in mutation_fields:
                save_name = None
            native_roots = data_contract.DataResourceRoots(
                list_name=list_name,
                detail_name=detail_name,
                aggregate_name=aggregate_name,
                group_name=group_name,
                group_count_name=group_count_name,
                create_name=create_name,
                update_name=update_name,
                save_name=save_name,
                delete_name=delete_name,
            )
            native_type_names = data_contract.DataResourceTypeNames(
                query=resource_type_name(query),
                node=resource_type_name(native_resource.node_type),
                filter=resource_type_name(native_resource.filter_type),
                order=resource_type_name(native_resource.order_by_type),
                aggregate=resource_type_name(native_resource.aggregate_container_type),
                grouped=resource_type_name(native_resource.group_type),
                group_key=resource_type_name(native_resource.group_key_type),
                group_by_spec=resource_type_name(native_resource.group_by_spec_type),
                group_order=resource_type_name(native_resource.group_order_type),
                having=resource_type_name(native_resource.having_type),
                create_input=(
                    resource_type_name(native_resource.insert_input_type) if create_name else None
                ),
                update_input=(
                    resource_type_name(native_resource.set_input_type)
                    if update_name or save_name
                    else None
                ),
            )
            create_fields = native_resource.insertable_fields if create_name else ()
            update_fields = (
                native_resource.updatable_fields if update_name or save_name else ()
            )
            lines_declaration = _single_policy_value(
                model_label, contributions, "lines_declaration"
            )
            if lines_declaration is not None:
                from angee.graphql.data.hasura import HasuraLines, _line_metadata

                lines = _line_metadata(
                    cast(HasuraLines, lines_declaration), native_resource, schema
                )
        roots = _merge_description_values(
            model_label,
            contributions,
            "roots",
            data_contract.DataResourceRoots,
            initial=native_roots,
        )
        type_names = _merge_description_values(
            model_label,
            contributions,
            "type_names",
            data_contract.DataResourceTypeNames,
            initial=native_type_names,
        )
        roots, type_names, final_capabilities = final_schema_references(
            schema, roots, type_names
        )
        subtitle = _merge_subtitle_contributions(model_label, contributions)
        metadata = _finalize_data_resource(
            model=first.model,
            model_label=model_label,
            roots=roots,
            type_names=type_names,
            capabilities=final_capabilities,
            filter_fields=_single_sequence(model_label, contributions, "filter_fields"),
            order_fields=_single_sequence(model_label, contributions, "order_fields"),
            aggregate_fields=_single_sequence(model_label, contributions, "aggregate_fields"),
            group_by_fields=_single_sequence(model_label, contributions, "group_by_fields"),
            group_dimensions=_single_sequence(model_label, contributions, "group_dimensions"),
            aggregate_measures=_single_sequence(model_label, contributions, "aggregate_measures"),
            default_measures=_single_sequence(model_label, contributions, "default_measures"),
            create_fields=create_fields,
            update_fields=update_fields,
            revision_fields=_single_sequence(model_label, contributions, "revision_fields"),
            group_aliases=_single_sequence(model_label, contributions, "group_aliases"),
            lines=lines,
            subtitle=subtitle,
            public_id_field=cast(
                str,
                _single_policy_value(model_label, contributions, "public_id_field")
                or PUBLIC_ID_FIELD_NAME,
            ),
            row_model=cast(
                str, _single_policy_value(model_label, contributions, "row_model") or "server"
            ),
            graphql_schema=schema,
            contributors=tuple(dict.fromkeys(item.origin for item in contributions)),
        )
        finalized.append(metadata)
    return tuple(finalized)


def _single_sequence(
    model_label: str,
    contributions: list[DataResourceContribution],
    name: str,
) -> tuple[Any, ...]:
    active = [item for item in contributions if getattr(item.policy, name) is not None]
    if not active:
        return ()
    value = getattr(active[0].policy, name)
    for item in active[1:]:
        if getattr(item.policy, name) != value:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} has conflicting {name} from "
                f"{active[0].origin} and {item.origin}."
            )
    return cast(tuple[Any, ...], value)


def _single_policy_value(
    model_label: str,
    contributions: list[DataResourceContribution],
    name: str,
) -> object | None:
    values = [
        (getattr(item.policy, name), item.origin)
        for item in contributions
        if getattr(item.policy, name) is not None
    ]
    if not values:
        return None
    value, origin = values[0]
    for candidate, candidate_origin in values[1:]:
        if candidate != value:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} has conflicting {name}: "
                f"{value!r} from {origin} and {candidate!r} from {candidate_origin}."
            )
    return value


def _merge_description_values(
    model_label: str,
    contributions: list[DataResourceContribution],
    name: str,
    description_type: type[Any],
    *,
    initial: object | None = None,
) -> Any:
    values: dict[str, object] = {}
    origins: dict[str, str] = {}
    if initial is not None:
        for field_def in dataclasses.fields(description_type):
            candidate = getattr(initial, field_def.name)
            if candidate is not None:
                values[field_def.name] = candidate
                origins[field_def.name] = "native resource"
    for item in contributions:
        description = getattr(item, name)
        for field_def in dataclasses.fields(description_type):
            candidate = getattr(description, field_def.name)
            if candidate is None:
                continue
            existing = values.get(field_def.name)
            if existing is not None and existing != candidate:
                raise ImproperlyConfigured(
                    f"resource metadata for {model_label} has conflicting {name}.{field_def.name}: "
                    f"{existing!r} from {origins[field_def.name]} and {candidate!r} from {item.origin}."
                )
            values[field_def.name] = candidate
            origins[field_def.name] = item.origin
    return description_type(**values)


def _final_group_by_fields(
    schema: GraphQLSchema,
    group_by_spec_name: str | None,
    *,
    accepted: tuple[str, ...],
    dimensions: tuple[data_contract.DataGroupDimensionMetadata, ...],
) -> tuple[str, ...]:
    """Keep authored group axes accepted by the final native group input."""

    group_by_spec = schema.get_type(group_by_spec_name) if group_by_spec_name else None
    fields = getattr(group_by_spec, "fields", None)
    field_input = fields.get("field") if isinstance(fields, dict) else None
    enum_type = get_named_type(field_input.type) if field_input is not None else None
    if not isinstance(enum_type, GraphQLEnumType):
        return ()
    final_inputs = set(enum_type.values)
    dimensions_by_field = {dimension.field: dimension for dimension in dimensions}
    return tuple(
        name
        for name in accepted
        if (dimension := dimensions_by_field.get(name)) is not None
        and dimension.input in final_inputs
    )


def _merge_subtitle_contributions(
    model_label: str,
    contributions: list[DataResourceContribution],
) -> data_contract.DataResourceSubtitleMetadata | None:
    active = [item for item in contributions if item.policy.subtitle is not None]
    if not active:
        return None
    values: dict[str, str] = {}
    origins: dict[str, str] = {}
    for item in active:
        assert item.policy.subtitle is not None
        for field_def in dataclasses.fields(data_contract.DataResourceSubtitleMetadata):
            candidate = getattr(item.policy.subtitle, field_def.name)
            if candidate is None:
                continue
            existing = values.get(field_def.name)
            if existing is not None and existing != candidate:
                raise ImproperlyConfigured(
                    f"resource metadata for {model_label} has conflicting subtitle.{field_def.name}: "
                    f"{existing!r} from {origins[field_def.name]} and {candidate!r} from {item.origin}."
                )
            values[field_def.name] = candidate
            origins[field_def.name] = item.origin
    return data_contract.DataResourceSubtitleMetadata(**values)


def readable_model_field_names(
    metadata: data_contract.DataResourceMetadata,
) -> frozenset[str]:
    """Return concrete model fields projected readably by the GraphQL node."""

    if metadata.model is None:
        return frozenset()
    names: set[str] = set()
    by_name = {field.name: field for field in metadata.model._meta.local_fields}
    for resource_field in metadata.fields:
        if not resource_field.readable or resource_field.model_field_name is None:
            continue
        model_field = by_name.get(resource_field.model_field_name)
        if model_field is None:
            continue
        names.add(model_field.name)
        names.add(model_field.attname)
    return frozenset(names)


def _finalize_data_resource(
    *,
    graphql_schema: GraphQLSchema,
    model: type[models.Model] | None = None,
    roots: data_contract.DataResourceRoots,
    type_names: data_contract.DataResourceTypeNames,
    capabilities: tuple[str, ...],
    filter_fields: tuple[str, ...] = (),
    order_fields: tuple[str, ...] = (),
    aggregate_fields: tuple[str, ...] = (),
    group_by_fields: tuple[str, ...] = (),
    group_dimensions: tuple[data_contract.DataGroupDimensionMetadata, ...] = (),
    aggregate_measures: tuple[data_contract.DataAggregateMeasureMetadata, ...] = (),
    default_measures: tuple[data_contract.DataAggregateMeasureMetadata, ...] = (),
    default_sort: tuple[data_contract.DataDefaultSortMetadata, ...] = (),
    create_fields: tuple[str, ...] = (),
    update_fields: tuple[str, ...] = (),
    revision_fields: tuple[str, ...] = (),
    relation_axes: tuple[data_contract.DataRelationAxisMetadata, ...] = (),
    group_aliases: tuple[data_contract.DataGroupAliasMetadata, ...] = (),
    lines: data_contract.DataLinesMetadata | None = None,
    subtitle: data_contract.DataResourceSubtitleMetadata | None = None,
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
    row_model: str = "server",
    contributors: tuple[str, ...] = (),
) -> data_contract.DataResourceMetadata:
    """Build one final neutral resource description.

    ``model`` is the owning Django model for a model-backed resource. A computed
    (non-model) resource passes ``model=None`` and a dotted ``model_label`` (e.g.
    ``"platform.addon"``); the model is only ever used internally (it is
    ``{"wire": False}``), so the wire payload is identical either way.

    ``row_model`` is the client/server boundary signal the frontend reads
    (``"server"`` by default — Hasura ``where``/``order_by``/``limit`` + the
    ``_groups`` aggregate; ``"client"`` for a small computed set that fetches once
    and filters/sorts/paginates/groups in the browser).
    """

    if model_label is not None:
        exposed_model_label = model_label
    elif model is not None:
        exposed_model_label = model._meta.label
    else:
        raise ImproperlyConfigured("final resource metadata requires model_label when model is None.")
    app_label, model_name = _model_label_parts(exposed_model_label, model)
    filter_fields = _require_unique(exposed_model_label, "filter field", filter_fields)
    order_fields = _require_unique(exposed_model_label, "order field", order_fields)
    aggregate_fields = _require_unique(exposed_model_label, "aggregate field", aggregate_fields)
    group_by_fields = _require_unique(exposed_model_label, "group axis", group_by_fields)
    if model is not None and roots.group_name is not None and not relation_axes:
        relation_axes = _relation_axes(model, group_by_fields)
    if model is not None and order_fields and not default_sort:
        default_sort = _default_sort(model, order_fields)
    filter_fields = final_input_policy_fields(
        graphql_schema, type_names.filter, accepted=filter_fields
    )
    order_fields = final_input_policy_fields(
        graphql_schema, type_names.order, accepted=order_fields
    )
    aggregate_fields = final_aggregate_wire_fields(
        graphql_schema, type_names.aggregate, accepted=aggregate_fields
    )
    group_by_fields = _final_group_by_fields(
        graphql_schema,
        type_names.group_by_spec,
        accepted=group_by_fields,
        dimensions=group_dimensions,
    )
    default_sort = tuple(
        dataclasses.replace(item, field=mapped[0])
        for item in default_sort
        if (
            mapped := final_input_policy_fields(
                graphql_schema,
                type_names.order,
                accepted=(item.field,),
            )
        )
    )
    if type_names.node is not None:
        relation_axes = tuple(
            dataclasses.replace(
                axis,
                field=final_wire_field_names(
                    graphql_schema, type_names.node, (axis.field,)
                )[0],
                label_axis=(
                    final_wire_field_names(
                        graphql_schema, type_names.node, (axis.label_axis,)
                    )[0]
                    if axis.label_axis is not None
                    else None
                ),
            )
            for axis in relation_axes
        )
    active_create_fields = final_input_wire_fields(
        graphql_schema,
        type_names.create_input,
        accepted=create_fields,
        exclude=("id",),
    )
    active_update_fields = final_input_wire_fields(
        graphql_schema,
        type_names.update_input,
        accepted=update_fields,
        exclude=("id",),
    )
    active_required_create_fields = final_required_input_wire_fields(
        graphql_schema,
        type_names.create_input,
        accepted=active_create_fields,
    )
    active_create_fields = _require_unique(exposed_model_label, "create field", active_create_fields)
    active_update_fields = _require_unique(exposed_model_label, "update field", active_update_fields)
    active_required_create_fields = _require_unique(
        exposed_model_label, "required create field", active_required_create_fields
    )
    revision_fields = _require_unique(exposed_model_label, "revision field", revision_fields)
    generated_fields: tuple[data_contract.DataResourceFieldMetadata, ...] = ()
    if type_names.node is not None:
        generated_fields = final_resource_fields(
            graphql_schema,
            type_names.node,
            model,
            filter_fields=filter_fields,
            order_fields=order_fields,
            aggregate_fields=aggregate_fields,
            group_by_fields=group_by_fields,
            create_fields=active_create_fields,
            update_fields=active_update_fields,
            required_create_fields=active_required_create_fields,
            relation_axes=relation_axes,
        )
    generated_fields = (
        *generated_fields,
        *final_input_only_resource_fields(
            graphql_schema,
            create_input_name=type_names.create_input,
            update_input_name=type_names.update_input,
            model=model,
            filter_fields=filter_fields,
            order_fields=order_fields,
            aggregate_fields=aggregate_fields,
            group_by_fields=group_by_fields,
            create_fields=active_create_fields,
            update_fields=active_update_fields,
            required_create_fields=active_required_create_fields,
            relation_axes=relation_axes,
            readable_fields=generated_fields,
        ),
    )
    active_fields = require_unique_resource_fields(exposed_model_label, generated_fields)
    record_representation = _record_representation_field(active_fields)
    active_subtitle = _resource_subtitle(
        model=model,
        model_label=exposed_model_label,
        graphql_schema=graphql_schema,
        node_name=type_names.node,
        fields=active_fields,
        declared=subtitle,
    )
    return data_contract.DataResourceMetadata(
        model=model,
        model_label=exposed_model_label,
        resource_type=model_resource_type(model) if model is not None else None,
        app_label=app_label,
        model_name=model_name,
        public_id_field=public_id_field,
        roots=roots,
        type_names=type_names,
        contributors=contributors,
        canonical_label=canonical_record_model(model)._meta.label if model is not None else None,
        row_model=row_model,
        record_representation=record_representation,
        subtitle=active_subtitle,
        impl_fields=_impl_fields(model, active_fields),
        capabilities=capabilities,
        fields=active_fields,
        filter_fields=filter_fields,
        order_fields=order_fields,
        aggregate_fields=aggregate_fields,
        group_by_fields=group_by_fields,
        group_dimensions=group_dimensions,
        aggregate_measures=aggregate_measures,
        default_measures=default_measures,
        default_sort=default_sort,
        create_fields=active_create_fields,
        update_fields=active_update_fields,
        required_create_fields=active_required_create_fields,
        revision_fields=revision_fields,
        relation_axes=relation_axes,
        group_aliases=group_aliases,
        lines=lines,
    )


_SurfaceT = TypeVar("_SurfaceT")


def _require_unique(
    model_label: str,
    purpose: str,
    values: tuple[str, ...],
) -> tuple[str, ...]:
    """Return ``values`` after rejecting duplicate declarations."""

    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ImproperlyConfigured(f"resource metadata for {model_label} declares duplicate {purpose} '{value}'.")
        seen.add(value)
    return values


_SELECTION_PATH = re.compile(r"^[_A-Za-z][_0-9A-Za-z]*(?:\.[_A-Za-z][_0-9A-Za-z]*)*$")


def _resource_subtitle(
    *,
    model: type[models.Model] | None,
    model_label: str,
    graphql_schema: GraphQLSchema,
    node_name: str | None,
    fields: tuple[data_contract.DataResourceFieldMetadata, ...],
    declared: data_contract.DataResourceSubtitleMetadata | None,
) -> data_contract.DataResourceSubtitleMetadata | None:
    """Return validated subtitle paths plus canonical timestamp defaults.

    Facts are GraphQL dotted selection paths, rather than model-field paths, so
    an addon may name a nested projection such as ``markdown.word_count``. Exact
    projected model fields carrying Django's ``auto_now_add``/``auto_now``
    flags supply the created/updated defaults; every other semantic fact is
    declared by its owning addon.
    """

    def timestamp_path(flag: str) -> str | None:
        if model is None:
            return None
        candidates = tuple(
            projected.name
            for projected in fields
            if projected.model_field_name is not None
            if getattr(model._meta.get_field(projected.model_field_name), flag, False)
        )
        if len(candidates) > 1:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} has multiple projected {flag} "
                f"subtitle fields: {', '.join(candidates)}."
            )
        return candidates[0] if candidates else None

    defaults = data_contract.DataResourceSubtitleMetadata(
        created=timestamp_path("auto_now_add"),
        updated=timestamp_path("auto_now"),
    )
    active = data_contract.DataResourceSubtitleMetadata(
        created=(declared.created if declared is not None and declared.created is not None else defaults.created),
        updated=(declared.updated if declared is not None and declared.updated is not None else defaults.updated),
        word_count=declared.word_count if declared is not None else None,
    )
    for field_def in dataclasses.fields(data_contract.DataResourceSubtitleMetadata):
        path = getattr(active, field_def.name)
        if path is not None and _SELECTION_PATH.fullmatch(path) is None:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares invalid subtitle.{field_def.name} "
                f"selection path {path!r}."
            )
        if path is not None:
            if node_name is not None:
                require_final_selection_path(
                    graphql_schema,
                    node_name,
                    path,
                    model_label=model_label,
                    fact=f"subtitle.{field_def.name}",
                )
    has_fact = any(getattr(active, field_def.name) is not None for field_def in dataclasses.fields(active))
    return active if has_fact else None


def _record_representation_field(fields: tuple[data_contract.DataResourceFieldMetadata, ...]) -> str | None:
    """Return the backend-owned display field for a resource record."""

    return next(iter(_record_representation_fields(fields)), None)


#: Backend-owned display-field precedence, shared by record representation and
#: the relation group-label fallback.
_PREFERRED_DISPLAY_FIELDS: tuple[str, ...] = (
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


def _record_representation_fields(
    fields: tuple[data_contract.DataResourceFieldMetadata, ...],
) -> tuple[str, ...]:
    """Return display-scalar candidates in backend-owned precedence order."""

    preferred = _PREFERRED_DISPLAY_FIELDS
    by_name = {field.name: field for field in fields}
    candidates = [candidate for candidate in preferred if _is_display_scalar(by_name.get(candidate))]
    candidates.extend(field.name for field in fields if field.name not in preferred and _is_display_scalar(field))
    return tuple(candidates)


def _is_display_scalar(field: data_contract.DataResourceFieldMetadata | None) -> bool:
    """Return whether ``field`` is suitable as a compact record label."""

    return field is not None and field.kind == "scalar" and field.scalar == "String"


def relation_group_by_fields(
    node_type: type,
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
) -> tuple[str, ...]:
    """Add one ORM-backed display axis for each direct relation group axis.

    The Hasura group-key type is built before resource metadata is attached, so
    relation labels must enter the group declaration at this seam. Explicit
    scalar relation-leaf axes keep ownership. Otherwise the related Strawberry
    object supplies the record-representation precedence, while Django confirms
    the selected label is a concrete string column that aggregation may group
    by. Computed labels therefore fall through to the next model-backed display
    field (for example, ``UserType.display_name`` falls back to ``username``).
    """

    expanded = list(group_by_fields)
    explicit_labels = _relation_label_axes(model, group_by_fields)
    for path in group_by_fields:
        if "__" in path or path in explicit_labels:
            continue
        try:
            relation = model._meta.get_field(path)
        except FieldDoesNotExist:
            continue
        if not is_to_one_relation(relation):
            continue
        related_model = getattr(relation, "related_model", None)
        if not isinstance(related_model, type) or not issubclass(related_model, models.Model):
            continue
        related_model = cast(type[models.Model], related_model)
        related_surface = resource_relation_surface(node_type, path)
        if related_surface is not None:
            projected_names = resource_string_field_names(related_surface)
            candidates = tuple(
                candidate
                for candidate in _PREFERRED_DISPLAY_FIELDS
                if candidate in projected_names
            ) + tuple(
                candidate
                for candidate in projected_names
                if candidate not in _PREFERRED_DISPLAY_FIELDS
            )
        else:
            # Donor-contributed and scalar-id relation axes carry no node
            # surface; fall back to the preferred display names over the
            # related Django model. The concrete-String and gated-read checks
            # below still bound what may enter the group key.
            candidates = _PREFERRED_DISPLAY_FIELDS
        for candidate in candidates:
            try:
                model_field = related_model._meta.get_field(candidate)
            except FieldDoesNotExist:
                continue
            if (
                isinstance(model_field, models.Field)
                and model_field.concrete
                and not model_field.is_relation
                and model_field_scalar(model_field) == "String"
            ):
                label_axis = f"{path}__{model_field.name}"
                if is_gated_read_axis(model, label_axis):
                    continue
                expanded.append(label_axis)
                break
    return tuple(expanded)


def _impl_fields(
    model: type[models.Model] | None,
    fields: tuple[data_contract.DataResourceFieldMetadata, ...],
) -> tuple[str, ...]:
    """Return this resource's readable ``ImplClassField`` column names, sorted.

    The impl key a row stores is the declared fact a frontend contribution varies
    on per row, so naming the columns that carry one keeps the console from
    hardcoding a model's impl column. Only columns this resource projects are
    named — an impl column the node surface does not expose cannot be read off a
    row. A model may carry several (an MTI parent's and its child's), so this is
    a set, sorted for a deterministic artifact.
    """

    if model is None:
        return ()
    impl_names = {
        field.name for field in model._meta.get_fields() if isinstance(field, ImplClassField)
    }
    return tuple(
        sorted(
            field.name
            for field in fields
            if field.readable and field.model_field_name in impl_names
        )
    )


def _default_sort(
    model: type[models.Model],
    order_fields: tuple[str, ...],
) -> tuple[data_contract.DataDefaultSortMetadata, ...]:
    """Return model default ordering terms exposed by the order input."""

    orderable = set(order_fields)
    sorts: list[data_contract.DataDefaultSortMetadata] = []
    for term in model._meta.ordering:
        if isinstance(term, models.expressions.OrderBy):
            # An expression ordering (F(...).desc(nulls_last=True), say) carries a
            # DB detail — NULL placement — the metadata does not need; expose the
            # axis name and direction it wraps.
            expression = term.expression
            name = getattr(expression, "name", None)
            if not isinstance(name, str):
                raise ImproperlyConfigured(
                    f"resource metadata for {model._meta.label} cannot expose computed default ordering {term!r}."
                )
            term = f"-{name}" if term.descending else name
        if not isinstance(term, str):
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} cannot expose non-string default ordering {term!r}."
            )
        if term == "?":
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} cannot expose random default ordering."
            )
        field = term[1:] if term.startswith("-") else term
        if field not in orderable:
            continue
        _require_model_field_for_path(model, field, purpose="default ordering")
        sorts.append(
            data_contract.DataDefaultSortMetadata(
                field=field,
                direction="DESC" if term.startswith("-") else "ASC",
            )
        )
    return tuple(sorts)


def _relation_axes(
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
) -> tuple[data_contract.DataRelationAxisMetadata, ...]:
    """Return direct FK group axes with their related model and optional label axis."""

    label_axes = _relation_label_axes(model, group_by_fields)
    relation_axes: list[data_contract.DataRelationAxisMetadata] = []
    for path in group_by_fields:
        if "__" in path:
            continue
        try:
            field = model._meta.get_field(path)
        except FieldDoesNotExist:
            continue
        if not is_to_one_relation(field):
            continue
        remote_field = getattr(field, "remote_field", None)
        related_model = getattr(remote_field, "model", None)
        if related_model is None:
            continue
        relation_axes.append(
            data_contract.DataRelationAxisMetadata(
                field=path,
                model_label=related_model._meta.label,
                public_id_field=PUBLIC_ID_FIELD_NAME,
                label_axis=label_axes.get(path),
            )
        )
    return tuple(relation_axes)


def _relation_label_axes(
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
) -> dict[str, str]:
    """Return relation label axes keyed by their direct relation axis."""

    direct_axes = {path for path in group_by_fields if "__" not in path}
    label_axes: dict[str, str] = {}
    for path in group_by_fields:
        if "__" not in path:
            continue
        try:
            terminal_field = require_field_for_path(model, path)
        except FieldPathError:
            continue
        if is_to_one_relation(terminal_field):
            # A nested relation is its own identity dimension, not a scalar
            # label for the first relation in the path. Advertising it as a
            # label axis makes clients select the object as a bare leaf.
            continue
        relation, _leaf = path.split("__", 1)
        try:
            field = model._meta.get_field(relation)
        except FieldDoesNotExist:
            continue
        if not is_to_one_relation(field):
            continue
        if relation not in direct_axes:
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} relation label axis '{path}' "
                f"requires matching direct relation group axis '{relation}'."
            )
        existing = label_axes.get(relation)
        if existing is not None and existing != path:
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} relation group axis '{relation}' "
                f"declares multiple label axes: '{existing}' and '{path}'."
            )
        label_axes[relation] = path
    return label_axes


def _require_model_field_for_path(
    model: type[models.Model],
    path: str,
    *,
    purpose: str,
) -> models.Field[Any, Any]:
    """Return a concrete model field for ``path`` or fail at metadata emission."""

    try:
        return require_field_for_path(model, path)
    except FieldPathError as error:
        if error.to_many:
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} declares unsupported to-many {purpose} field path '{path}'."
            ) from None
        raise ImproperlyConfigured(
            f"resource metadata for {model._meta.label} declares unknown {purpose} field path '{path}'."
        ) from None


def _model_label_parts(
    model_label: str,
    model: type[models.Model] | None,
) -> tuple[str, str]:
    """Return metadata app/model names for a public model label.

    A computed resource has no model; its dotted ``app.model`` label is split
    directly. A model-backed resource whose label equals ``model._meta.label``
    reuses the model's own app/model names.
    """

    if model is not None and model_label == model._meta.label:
        return model._meta.app_label, model._meta.model_name
    app_label, object_name = model_label.split(".", 1)
    return app_label, object_name.lower()
