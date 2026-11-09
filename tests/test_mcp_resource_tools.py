"""Contracts for metadata-generated MCP resource readers."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
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
from rebac.schema import parse_zed

from angee.graphql.data.contracts import (
    DataQueryField,
    DataQueryFilter,
    DataQueryIdentity,
    DataResourceFieldMetadata,
    DataResourceMetadata,
    DataResourceQuery,
    DataResourceRoots,
    DataResourceTypeNames,
)
from angee.graphql.schema import GraphQLSchemas
from angee.mcp.graphql import (
    ACTOR_EXECUTION_META,
    DEFAULT_QUERY_LIMIT,
    MAX_QUERY_LIMIT,
    _CompiledTool,
    register_graphql_tools,
    require_actor_tool,
)
from angee.mcp.resource_tools import (
    _resource_projections,
    check_resource_tool_specs,
    register_resource_tools,
    resource_tool_specs,
)
from angee.mcp.tool_tags import RESOURCE_READER_TOOL_TAG
from tests.composed_host import COMPOSED_TEST_TIMEOUT


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


PROBE_DECLARATION = parse_zed(
    "definition tests/probe {\n"
    "    relation owner: auth/user\n"
    "    permission read = owner\n"
    "    permission read__secret = owner\n"
    "    permission read__optional_secret = owner\n"
    "}\n"
).get_definition("tests/probe")
"""The probe's declared policy: both secrets are owner-gated reads."""


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

    fields = tuple(
        DataResourceFieldMetadata(
            name=column,
            model_field_name=column,
            kind="scalar",
            scalar="ID" if column == "id" else "String",
            readable=True,
        )
        for column in ("id", "title", "body", "secret", "optional_secret")
    )
    return DataResourceMetadata(
        model=ResourceToolProbe,
        model_label=f"agents.{name.title()}",
        resource_type=f"tests/{name}",
        app_label="agents",
        model_name=name,
        query=DataResourceQuery(
            identity=DataQueryIdentity("id"),
            fields={
                name: DataQueryField(
                    kind="scalar", scalar="String", filter=DataQueryFilter(name, ("iContains",), "String")
                )
                for name in ("title", "body")
            },
        ),
        roots=DataResourceRoots(list_name=name, detail_name=f"{name}_by_pk"),
        type_names=DataResourceTypeNames(node="ResourceToolProbe"),
        capabilities=("list", "detail"),
        fields=fields,
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
    """Generation sorts resources and omits declared gated non-null/redaction-breaking leaves."""

    schemas = _FakeSchemas((_resource("zeta"), _resource("alpha")))
    monkeypatch.setattr("angee.mcp.resource_tools.declared_rebac_definition", lambda model: PROBE_DECLARATION)

    specs, catalogue = resource_tool_specs(schemas)  # type: ignore[arg-type]

    assert [spec.name for spec in specs] == ["query_alpha", "read_alpha", "query_zeta", "read_zeta"]
    assert [entry.name for entry in catalogue] == ["alpha", "zeta"]
    assert specs[0].fields == ("sqid", "title", "optional_secret")
    assert specs[1].fields == ("sqid", "title", "body", "optional_secret")
    assert specs[0].search_fields == ("title", "body")
    assert all("secret" not in spec.fields for spec in specs)
    assert all(spec.tags == frozenset({RESOURCE_READER_TOOL_TAG}) for spec in specs)


def test_projection_reads_the_backing_field_of_an_aliased_wire_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wire alias keeps its model field's summary and redaction policy; an unbacked field is a plain leaf."""

    aliases = {"body": "content", "secret": "credential", "optional_secret": "optional_credential"}
    resource = _resource("probes")
    resource = replace(
        resource,
        fields=(
            *(replace(field, name=aliases.get(field.name, field.name)) for field in resource.fields),
            DataResourceFieldMetadata(name="computed", kind="scalar", scalar="String", readable=True),
        ),
    )
    monkeypatch.setattr("angee.mcp.resource_tools.declared_rebac_definition", lambda model: PROBE_DECLARATION)

    summary, detail, _search = _resource_projections(resource)

    assert summary == ("sqid", "title", "optional_credential", "computed")
    assert detail == ("sqid", "title", "content", "optional_credential", "computed")


def test_generated_query_limits_and_search_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The compiler advertises/enforces <=50 and maps search to a Hasura where expression."""

    schemas = _FakeSchemas((_resource("probes"),))
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
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    server = FastMCP(name="resource-registration-test")

    register_resource_tools(server)

    tools = _tool_map(server)
    assert set(tools) == {"list_resources", "query_probes", "read_probes"}
    assert tools["list_resources"].annotations.readOnlyHint is True
    assert all(tool.meta == ACTOR_EXECUTION_META and require_actor_tool(tool) for tool in tools.values())
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


def test_mcp_conventional_module_owns_its_resource_tool_registrar() -> None:
    """The conventional MCP module owns the metadata-generated resource registrar."""

    from angee.mcp.mcp_tools import register
    from angee.mcp.server import tool_registrar

    assert tool_registrar(apps.get_app_config("mcp")) is register


def test_resource_without_detail_is_not_advertised() -> None:
    """A metadata contribution without both read roots cannot promise a reader pair."""

    resource = replace(_resource("partial"), roots=DataResourceRoots(list_name="partial"))
    schemas = _FakeSchemas((resource,))

    specs, catalogue = resource_tool_specs(schemas)  # type: ignore[arg-type]

    assert specs == ()
    assert catalogue == ()


def test_graphql_wire_root_is_normalized_only_for_tool_names() -> None:
    """Camel-case schema roots keep their operation but expose snake-safe grant ids."""

    resource = _resource("feedFollows")
    schemas = _FakeSchemas((resource,))

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


def test_generated_tools_and_check_follow_the_declared_policy_without_a_database(django_db_blocker: Any) -> None:
    """Tool shapes follow each model's composed declaration; neither generation nor E001 reads the database.

    ``parties/party`` declares ``read__notes``, ``read__first_met_note`` and
    ``read__handle_count``; the columns are non-null, so no generated projection names them.
    """

    with django_db_blocker.block():
        specs, _catalogue = resource_tool_specs()
        errors = check_resource_tool_specs()

    assert errors == []
    fields = {spec.name: spec.fields for spec in specs}
    assert "display_name" in fields["read_parties"]
    for name in ("query_parties", "read_parties"):
        assert {"notes", "first_met_note", "handle_count"}.isdisjoint(fields[name])


def test_resource_tool_check_passes_on_a_fresh_database(tmp_path: Path) -> None:
    """Django's check passes on a composed host whose empty database holds no synced policy."""

    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("DATABASE_URL", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests/composed_host.py"),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--app",
            "angee.mcp",
            "--app",
            "angee.parties",
            "--no-examples",
            "--action",
            "state",
            "--output",
            str(tmp_path / "state.py"),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=COMPOSED_TEST_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, f"check failed on a fresh database:\n{result.stdout}\n{result.stderr}"
    assert "angee.mcp.E001" not in result.stdout + result.stderr
