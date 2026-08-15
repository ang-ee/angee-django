"""Tests for the knowledge addon's MCP tool surface — the spec→schema contract.

The knowledge tools are declared in :mod:`angee.knowledge.mcp_tools` and registered
through the same compiler the notes example uses. These tests compile every spec against
the real knowledge ``public`` schema bucket (built standalone, like
:mod:`tests.test_knowledge_graphql`), so a drift between a tool spec and the schema —
an unknown field, a missing argument, a renamed operation, an over-deep projection —
fails here at compile time rather than at the agent's first call. This is the only CI
check that the knowledge MCP specs stay in sync with the schema.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest
from django.apps import apps
from fastmcp import FastMCP

from angee.addons import addon_contract, resolve_addon_reference
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from tests.conftest import SchemaAddon

knowledge_schema = importlib.import_module("angee.knowledge.schema")
knowledge_mcp_tools = importlib.import_module("angee.knowledge.mcp_tools")

_EXPECTED_TOOLS = {
    "read_page",
    "search_pages",
    "patch_page_section",
    "replace_page_text",
    "append_to_page",
    "page_backlinks",
}


@pytest.fixture
def knowledge_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``GraphQLSchemas.from_discovery`` at the knowledge-only ``public`` bucket.

    The compiler resolves each spec against ``from_discovery().graphql_schema(...)``;
    pinning it to the knowledge bucket (built the way ``addon_schema`` builds it) compiles
    the real operations without standing up the whole discovery schema.
    """

    parts = {key: tuple(knowledge_schema.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}
    schemas = GraphQLSchemas([SchemaAddon({"public": parts})])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))


def _registered_tools() -> dict[str, Any]:
    """Compile and register every knowledge spec, returning the tool-name → tool map.

    Calling ``register`` runs the compiler's ``_compile``/``_validate`` for every spec, so
    a bad spec raises here (the contract check); the returned tools carry the derived
    document, input schema, and output projection for the per-tool assertions below.
    """

    server = FastMCP(name="test-knowledge")
    knowledge_mcp_tools.register(server)
    # fastmcp 3.x dropped the private _tool_manager; list_tools() (async) returns
    # the registered _CompiledTool objects, which still carry .document/.parameters.
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_all_knowledge_tools_compile_and_register(knowledge_discovery: None) -> None:
    """Every declared knowledge spec compiles and registers under its expected name."""

    assert set(_registered_tools()) == _EXPECTED_TOOLS


def test_read_page_projects_outline_and_backlinks(knowledge_discovery: None) -> None:
    """read_page reads one page by sqid with a nested markdown/outline + backlinks projection."""

    tool = _registered_tools()["read_page"]

    assert tool.annotations is not None and tool.annotations.readOnlyHint is True
    assert tool.parameters["required"] == ["sqid"]
    assert tool.document == (
        "query ($id: String!) { pages_by_pk(id: $id) { "
        "id title kind "
        "markdown { body body_hash word_count outline { level text slug } } "
        "backlinks { page title display_text } "
        "} }"
    )
    markdown = tool.output_schema["properties"]["markdown"]
    assert set(markdown["properties"]) == {"body", "body_hash", "word_count", "outline"}
    assert markdown["properties"]["outline"]["items"]["properties"] == {
        "level": {"type": "integer"},
        "text": {"type": "string"},
        "slug": {"type": "string"},
    }
    backlinks = tool.output_schema["properties"]["backlinks"]
    assert set(backlinks["items"]["properties"]) == {"page", "title", "display_text"}


def test_search_pages_passes_named_arguments(knowledge_discovery: None) -> None:
    """search_pages threads vault/query/first through as top-level tool arguments."""

    tool = _registered_tools()["search_pages"]

    assert set(tool.parameters["properties"]) == {"vault", "query", "first"}
    assert tool.parameters["required"] == ["vault", "query"]  # first has a schema default
    assert tool.document == (
        "query ($vault: ID!, $query: String!, $first: Int!) "
        "{ search_pages(vault: $vault, query: $query, first: $first) { id title kind } }"
    )
    # A list operation projects its rows under ``result``.
    assert set(tool.output_schema["properties"]) == {"result"}


@pytest.mark.parametrize(
    ("name", "args", "required"),
    [
        (
            "patch_page_section",
            {"page", "heading_path", "op", "content", "expected_hash"},
            ["page", "heading_path", "op", "content"],
        ),
        ("replace_page_text", {"page", "old", "new", "expected_hash"}, ["page", "old", "new"]),
        ("append_to_page", {"page", "content", "expected_hash"}, ["page", "content"]),
    ],
)
def test_body_write_tools_project_the_payload(
    knowledge_discovery: None, name: str, args: set[str], required: list[str]
) -> None:
    """Each body-write tool takes bare arguments and projects the shared PageBodyPayload."""

    tool = _registered_tools()[name]

    assert tool.annotations is not None and tool.annotations.readOnlyHint is False
    assert set(tool.parameters["properties"]) == args
    assert tool.parameters["required"] == required
    assert set(tool.output_schema["properties"]) == {"ok", "error", "error_code", "markdown"}
    assert tool.output_schema["properties"]["markdown"]["properties"] == {"body_hash": {"type": "string"}}


def test_patch_page_section_advertises_enum_and_list_inputs(knowledge_discovery: None) -> None:
    """The section op is advertised as an enum and the heading path as a string array."""

    tool = _registered_tools()["patch_page_section"]

    assert tool.parameters["properties"]["op"] == {"type": "string", "enum": ["REPLACE", "APPEND", "PREPEND"]}
    assert tool.parameters["properties"]["heading_path"] == {"type": "array", "items": {"type": "string"}}


def test_page_backlinks_projects_only_backlinks(knowledge_discovery: None) -> None:
    """page_backlinks is a read-only convenience that projects the backlink list."""

    tool = _registered_tools()["page_backlinks"]

    assert tool.annotations is not None and tool.annotations.readOnlyHint is True
    assert tool.document == (
        "query ($id: String!) { pages_by_pk(id: $id) { id title backlinks { page title display_text } } }"
    )


def test_appconfig_wires_the_registrar() -> None:
    """The knowledge manifest declares ``mcp_tools`` and it resolves to ``register``.

    Proves the discovery seam (``mcp/server.py`` reads ``contract.mcp_tools``) finds the
    knowledge registrar without building the whole server.
    """

    app_config = apps.get_app_config("knowledge")
    mcp_tools = addon_contract(app_config).mcp_tools
    assert mcp_tools == "mcp_tools.register"
    assert resolve_addon_reference(app_config, mcp_tools, attr="mcp_tools") is knowledge_mcp_tools.register
