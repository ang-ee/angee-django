"""Contracts for metadata-generated MCP resource readers."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from django.apps import apps
from django.db import models
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from graphql import (
    GraphQLArgument,
    GraphQLField,
    GraphQLID,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLInt,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
)

from angee.addons import addon_contract
from angee.graphql.data.metadata import DataResourceMetadata, DataResourceRoots, DataResourceTypeNames
from angee.graphql.data.resource_fields import DataResourceFieldMetadata
from angee.graphql.schema import GraphQLSchemas
from angee.mcp.graphql import _CompiledTool, register_graphql_tools
from angee.mcp.resource_tools import (
    DEFAULT_QUERY_LIMIT,
    MAX_QUERY_LIMIT,
    RESOURCE_READER_TOOL_TAG,
    check_resource_tool_specs,
    register_resource_tools,
    resource_tool_specs,
)


class ResourceToolProbe(models.Model):
    """Field shapes used to test bounded projections without a database table."""

    title = models.CharField(max_length=100)
    body = models.TextField()
    secret = models.CharField(max_length=100)
    optional_secret = models.CharField(max_length=100, null=True)

    class Meta:
        app_label = "agents"
        managed = False
        verbose_name_plural = "probe records"


class _FakeSchemas:
    """Small GraphQLSchemas duck type for registration-time generation."""

    def __init__(self, resources: tuple[DataResourceMetadata, ...]) -> None:
        self._resources = resources
        self._schema = _probe_schema(resources)

    def graphql_schema(self, name: str) -> GraphQLSchema:
        assert name == "console"
        return self._schema

    def resources(self, name: str) -> tuple[DataResourceMetadata, ...]:
        assert name == "console"
        return self._resources


def _resource(name: str) -> DataResourceMetadata:
    """Return one resource declaration whose roots are named by ``name``."""

    fields = (
        DataResourceFieldMetadata(name="id", kind="scalar", scalar="ID", readable=True),
        DataResourceFieldMetadata(
            name="title",
            kind="scalar",
            scalar="String",
            readable=True,
            filterable=True,
        ),
        DataResourceFieldMetadata(
            name="body",
            kind="scalar",
            scalar="String",
            readable=True,
            filterable=True,
        ),
        DataResourceFieldMetadata(name="secret", kind="scalar", scalar="String", readable=True),
        DataResourceFieldMetadata(name="optional_secret", kind="scalar", scalar="String", readable=True),
    )
    return DataResourceMetadata(
        model=ResourceToolProbe,
        model_label=f"agents.{name.title()}",
        resource_type=f"tests/{name}",
        app_label="agents",
        model_name=name,
        public_id_field="id",
        roots=DataResourceRoots(list_name=name, detail_name=f"{name}_by_pk"),
        type_names=DataResourceTypeNames(node="ResourceToolProbe"),
        capabilities=("list", "detail"),
        fields=fields,
        filter_fields=("title", "body"),
    )


def _probe_schema(resources: tuple[DataResourceMetadata, ...]) -> GraphQLSchema:
    """Return an introspectable query schema matching the resource declarations."""

    node = GraphQLObjectType(
        "ResourceToolProbe",
        {
            "id": GraphQLField(GraphQLNonNull(GraphQLID)),
            "title": GraphQLField(GraphQLNonNull(GraphQLString)),
            "body": GraphQLField(GraphQLNonNull(GraphQLString)),
            "secret": GraphQLField(GraphQLNonNull(GraphQLString)),
            "optional_secret": GraphQLField(GraphQLString),
        },
    )
    string_filter = GraphQLInputObjectType(
        "ProbeStringFilter",
        {"_ilike": GraphQLInputField(GraphQLString)},
    )
    where = GraphQLInputObjectType(
        "ProbeWhere",
        {
            "title": GraphQLInputField(string_filter),
            "body": GraphQLInputField(string_filter),
        },
    )
    fields: dict[str, GraphQLField] = {}
    for resource in resources:
        if resource.roots.list_name:
            fields[resource.roots.list_name] = GraphQLField(
                GraphQLNonNull(GraphQLList(GraphQLNonNull(node))),
                args={
                    "limit": GraphQLArgument(GraphQLInt),
                    "offset": GraphQLArgument(GraphQLInt),
                    "where": GraphQLArgument(where),
                },
            )
        if resource.roots.detail_name:
            fields[resource.roots.detail_name] = GraphQLField(
                node,
                args={"id": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            )
    return GraphQLSchema(query=GraphQLObjectType("Query", fields))


def _tool_map(server: FastMCP) -> dict[str, Any]:
    """Return registered FastMCP tools through its public async listing API."""

    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_generated_resource_names_projections_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generation sorts resources and omits gated non-null/redaction-breaking leaves."""

    schemas = _FakeSchemas((_resource("zeta"), _resource("alpha")))
    monkeypatch.setattr(
        "angee.mcp.resource_tools.gated_read_fields",
        lambda model: frozenset({"secret", "optional_secret"}),
    )

    specs, catalogue = resource_tool_specs(schemas)  # type: ignore[arg-type]

    assert [spec.name for spec in specs] == ["query_alpha", "read_alpha", "query_zeta", "read_zeta"]
    assert [entry.name for entry in catalogue] == ["alpha", "zeta"]
    assert specs[0].fields == ("sqid", "title", "optional_secret")
    assert specs[1].fields == ("sqid", "title", "body", "optional_secret")
    assert specs[0].search_fields == ("title", "body")
    assert all("secret" not in spec.fields for spec in specs)
    assert all(spec.tags == frozenset({RESOURCE_READER_TOOL_TAG}) for spec in specs)


def test_generated_query_limits_and_search_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The compiler advertises/enforces <=50 and maps search to a Hasura where expression."""

    schemas = _FakeSchemas((_resource("probes"),))
    monkeypatch.setattr("angee.mcp.resource_tools.gated_read_fields", lambda model: frozenset())
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    specs, _catalogue = resource_tool_specs(schemas)  # type: ignore[arg-type]
    server = FastMCP(name="resource-limit-test")
    register_graphql_tools(server, list(specs))
    query = _tool_map(server)["query_probes"]
    assert isinstance(query, _CompiledTool)
    assert query.op_type == "query"
    assert query.tags == {RESOURCE_READER_TOOL_TAG}

    assert query.parameters["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_QUERY_LIMIT,
        "default": DEFAULT_QUERY_LIMIT,
        "description": "Maximum rows to return.",
    }
    assert query._variables({"offset": 4}) == {"limit": DEFAULT_QUERY_LIMIT, "offset": 4}
    assert query._variables({"limit": 5, "offset": 1, "search": "needle"}) == {
        "limit": 5,
        "where": {
            "_or": [
                {"title": {"_ilike": "%needle%"}},
                {"body": {"_ilike": "%needle%"}},
            ]
        },
        "offset": 1,
    }
    with pytest.raises(ToolError, match="must not exceed 50"):
        query._variables({"limit": 51, "offset": 0})


def test_resource_registrar_adds_honest_catalogue_and_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP addon's explicit registrar contributes catalogue plus generated tools."""

    schemas = _FakeSchemas((_resource("probes"),))
    monkeypatch.setattr("angee.mcp.resource_tools.gated_read_fields", lambda model: frozenset())
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    server = FastMCP(name="resource-registration-test")

    register_resource_tools(server)

    tools = _tool_map(server)
    assert set(tools) == {"list_resources", "query_probes", "read_probes"}
    assert tools["list_resources"].annotations.readOnlyHint is True
    assert tools["list_resources"].tags == {RESOURCE_READER_TOOL_TAG}
    result = asyncio.run(tools["list_resources"].run({}))
    assert result.structured_content == {
        "resources": [
            {
                "name": "probes",
                "description": "probe records",
                "query_tool": "query_probes",
                "read_tool": "read_probes",
            }
        ]
    }


def test_mcp_manifest_owns_its_resource_tool_registrar() -> None:
    """The generated reader entrypoint is an explicit addon-contract fact."""

    contract = addon_contract(apps.get_app_config("mcp"))
    assert contract is not None
    assert contract.mcp_tools == "mcp_tools.register"


def test_resource_without_detail_is_not_advertised(monkeypatch: pytest.MonkeyPatch) -> None:
    """A metadata contribution without both read roots cannot promise a reader pair."""

    resource = replace(_resource("partial"), roots=DataResourceRoots(list_name="partial"))
    schemas = _FakeSchemas((resource,))
    monkeypatch.setattr("angee.mcp.resource_tools.gated_read_fields", lambda model: frozenset())

    specs, catalogue = resource_tool_specs(schemas)  # type: ignore[arg-type]

    assert specs == ()
    assert catalogue == ()


def test_graphql_wire_root_is_normalized_only_for_tool_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Camel-case schema roots keep their operation but expose snake-safe grant ids."""

    resource = _resource("feedFollows")
    schemas = _FakeSchemas((resource,))
    monkeypatch.setattr("angee.mcp.resource_tools.gated_read_fields", lambda model: frozenset())

    specs, catalogue = resource_tool_specs(schemas)  # type: ignore[arg-type]

    assert [spec.operation for spec in specs] == ["feedFollows", "feedFollows_by_pk"]
    assert [spec.name for spec in specs] == ["query_feed_follows", "read_feed_follows"]
    assert catalogue[0].name == "feed_follows"


def test_resource_tool_system_check_reports_console_schema_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Django check surface converts compiler drift into a stable check error."""

    def drift() -> Any:
        raise ValueError("missing console root")

    monkeypatch.setattr("angee.mcp.resource_tools.resource_tool_specs", drift)
    errors = check_resource_tool_specs()

    assert len(errors) == 1
    assert errors[0].id == "angee.mcp.E001"
    assert "missing console root" in errors[0].msg
