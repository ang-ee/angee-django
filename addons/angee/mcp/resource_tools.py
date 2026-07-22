"""Generate bounded read tools from the GraphQL data-resource registry.

The resource registry owns which models, roots, and fields a schema exposes. This
module translates that declaration once, when the process-wide MCP server is built,
then delegates document compilation and actor-scoped execution to
:class:`~angee.mcp.graphql.GraphQLTool`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from fastmcp import FastMCP
from fastmcp.tools import Tool
from graphql import GraphQLInputObjectType, GraphQLList, GraphQLNonNull
from rebac.field_visibility import gated_read_fields
from strawberry.utils.str_converters import to_snake_case

from angee.graphql.data.metadata import DataResourceMetadata
from angee.graphql.data.resource_fields import DataResourceFieldMetadata
from angee.graphql.schema import GraphQLSchemas
from angee.mcp.graphql import GraphQLTool, register_graphql_tools
from mcp.types import ToolAnnotations

RESOURCE_SCHEMA = "console"
"""Schema bucket used by the session UI for messaging, knowledge, and notes."""

DEFAULT_QUERY_LIMIT = 25
MAX_QUERY_LIMIT = 50
MAX_SUMMARY_FIELDS = 8

RESOURCE_READER_TOOL_TAG = "angee:resource_reader"
"""Registration marker consumed by the built-in grant-catalogue sync."""


@dataclass(frozen=True, slots=True)
class ResourceToolCatalogueEntry:
    """One generated resource and the two tools that expose it."""

    name: str
    description: str
    query_tool: str
    read_tool: str


def register_resource_tools(server: FastMCP) -> None:
    """Register the honest resource catalogue and deterministic generated readers."""

    specs, catalogue = resource_tool_specs()
    server.add_tool(
        Tool.from_function(
            _catalogue_reader(catalogue),
            name="list_resources",
            description="List the data resources available through generated read tools.",
            annotations=ToolAnnotations(readOnlyHint=True),
            tags={RESOURCE_READER_TOOL_TAG},
        )
    )
    register_graphql_tools(server, list(specs))


def resource_tool_specs(
    schemas: GraphQLSchemas | None = None,
) -> tuple[tuple[GraphQLTool, ...], tuple[ResourceToolCatalogueEntry, ...]]:
    """Return generated GraphQL declarations and catalogue entries in stable order."""

    schemas = schemas or GraphQLSchemas.from_discovery()
    graphql_schema = schemas.graphql_schema(RESOURCE_SCHEMA)
    query_root = graphql_schema.query_type
    if query_root is None:
        raise ImproperlyConfigured(f"GraphQL schema {RESOURCE_SCHEMA!r} has no query root.")

    specs: list[GraphQLTool] = []
    catalogue: list[ResourceToolCatalogueEntry] = []
    resources = sorted(
        schemas.resources(RESOURCE_SCHEMA),
        key=lambda resource: (resource.roots.list_name or "", resource.model_label),
    )
    for resource in resources:
        list_name = resource.roots.list_name
        detail_name = resource.roots.detail_name
        if not list_name or not detail_name:
            continue
        if list_name not in query_root.fields or detail_name not in query_root.fields:
            raise ImproperlyConfigured(
                f"Resource {resource.model_label!r} declares missing console query roots {list_name!r}/{detail_name!r}."
            )

        list_field = query_root.fields[list_name]
        detail_field = query_root.fields[detail_name]
        _require_collection_arguments(resource, list_field)
        id_arg = _detail_id_arg(resource, detail_field)
        summary_fields, detail_fields, search_fields = _resource_projections(resource)
        resource_name = to_snake_case(list_name)
        query_name = f"query_{resource_name}"
        read_name = f"read_{resource_name}"
        description = _resource_description(resource)
        specs.extend(
            (
                GraphQLTool(
                    operation=list_name,
                    name=query_name,
                    fields=summary_fields,
                    description=f"Query a bounded summary list of {description}.",
                    schema=RESOURCE_SCHEMA,
                    limit_arg="limit",
                    args=("offset",),
                    search_fields=search_fields,
                    default_limit=DEFAULT_QUERY_LIMIT,
                    max_limit=MAX_QUERY_LIMIT,
                    tags=frozenset({RESOURCE_READER_TOOL_TAG}),
                ),
                GraphQLTool(
                    operation=detail_name,
                    name=read_name,
                    fields=detail_fields,
                    description=f"Read one {description} record by public id.",
                    schema=RESOURCE_SCHEMA,
                    id_arg=id_arg,
                    tags=frozenset({RESOURCE_READER_TOOL_TAG}),
                ),
            )
        )
        catalogue.append(
            ResourceToolCatalogueEntry(
                name=resource_name,
                description=description,
                query_tool=query_name,
                read_tool=read_name,
            )
        )
    return tuple(specs), tuple(catalogue)


def check_resource_tool_specs(
    app_configs: Any = None,
    **kwargs: Any,
) -> list[checks.CheckMessage]:
    """Fail Django's system check when console resources cannot compile to tools."""

    del app_configs, kwargs
    try:
        resource_tool_specs()
    except Exception as error:  # noqa: BLE001 - system checks must report drift, not abort registration
        return [
            checks.Error(
                f"Generated MCP resource tools do not match the console schema: {error}",
                hint="Rebuild the runtime/schema or fix the owning resource metadata/root contract.",
                id="angee.mcp.E001",
            )
        ]
    return []


def _catalogue_reader(
    catalogue: tuple[ResourceToolCatalogueEntry, ...],
) -> Callable[[], dict[str, list[dict[str, str]]]]:
    """Bind the immutable generated catalogue into a request-independent callable."""

    def list_resources() -> dict[str, list[dict[str, str]]]:
        """Return generated resources, independent of the caller's row visibility."""

        return {
            "resources": [
                {
                    "name": entry.name,
                    "description": entry.description,
                    "query_tool": entry.query_tool,
                    "read_tool": entry.read_tool,
                }
                for entry in catalogue
            ]
        }

    return list_resources


def _resource_projections(
    resource: DataResourceMetadata,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return summary, detail, and search projections from resource-owned metadata."""

    gated = gated_read_fields(resource.model) if resource.model is not None else frozenset()
    readable = tuple(field for field in resource.fields if _is_safe_scalar(resource, field, gated))
    if not readable:
        raise ImproperlyConfigured(f"Resource {resource.model_label!r} exposes no safe scalar fields.")

    detail = _with_public_id(resource, tuple(field.name for field in readable))
    summary_candidates = tuple(field for field in readable if _is_summary_field(resource, field))
    summary = _with_public_id(
        resource,
        tuple(field.name for field in summary_candidates[:MAX_SUMMARY_FIELDS]),
    )
    search = tuple(
        field.name
        for field in readable
        if field.filterable and field.scalar == "String" and field.name != resource.public_id_field
    )
    return summary, detail, search


def _is_safe_scalar(
    resource: DataResourceMetadata,
    field: DataResourceFieldMetadata,
    gated: frozenset[str],
) -> bool:
    """Return whether a metadata field can be projected without a nested selection."""

    if not field.readable or field.kind not in {"scalar", "enum"} or field.scalar == "JSON":
        return False
    model = resource.model
    if model is None:
        return True
    model_field = _model_field(model, field.name)
    if model_field is None:
        return True
    return not (model_field.name in gated and not model_field.null)


def _is_summary_field(resource: DataResourceMetadata, field: DataResourceFieldMetadata) -> bool:
    """Keep collection rows narrow by omitting unbounded text and JSON bodies."""

    model = resource.model
    if model is None:
        return field.scalar != "JSON"
    model_field = _model_field(model, field.name)
    return not isinstance(model_field, models.TextField)


def _with_public_id(resource: DataResourceMetadata, fields: tuple[str, ...]) -> tuple[str, ...]:
    """Expose the resource public id as the compiler-owned ``sqid`` name exactly once."""

    projected = tuple("sqid" if name == resource.public_id_field else name for name in fields)
    return projected if "sqid" in projected else ("sqid", *projected)


def _model_field(model: type[models.Model], name: str) -> models.Field[Any, Any] | None:
    """Resolve metadata's wire field to its owning Django field when one exists."""

    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None


def _resource_description(resource: DataResourceMetadata) -> str:
    """Return the model-owned human name used by the generated catalogue."""

    if resource.model is None:
        return resource.model_label
    return str(resource.model._meta.verbose_name_plural)


def _require_collection_arguments(resource: DataResourceMetadata, field: Any) -> None:
    """Fail at registration if a console collection cannot honor the bounded contract."""

    missing = sorted({"limit", "offset"}.difference(field.args))
    if missing:
        raise ImproperlyConfigured(
            f"Resource {resource.model_label!r} collection {resource.roots.list_name!r} "
            f"lacks generated-tool argument(s) {missing}."
        )


def _detail_id_arg(resource: DataResourceMetadata, field: Any) -> str:
    """Return the root argument carrying the public id for one detail operation."""

    if "id" in field.args:
        return "id"
    for name, argument in field.args.items():
        if isinstance(_unwrap(argument.type), GraphQLInputObjectType):
            input_fields = _unwrap(argument.type).fields
            if "id" in input_fields:
                return str(name)
    raise ImproperlyConfigured(
        f"Resource {resource.model_label!r} detail {resource.roots.detail_name!r} has no public-id argument."
    )


def _unwrap(graphql_type: Any) -> Any:
    """Strip GraphQL list/non-null wrappers for registration-time inspection."""

    while isinstance(graphql_type, GraphQLNonNull | GraphQLList):
        graphql_type = graphql_type.of_type
    return graphql_type
