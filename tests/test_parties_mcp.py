"""The parties addon's MCP tool surface: every spec compiles against the parties ``public`` bucket.

Compiling against the real bucket (built standalone, as :mod:`tests.test_parties_graphql`
builds it) makes a drift between a tool spec and the schema fail here rather than at an
agent's first call.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest
from fastmcp import FastMCP

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before the schema imports
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from tests.conftest import SchemaAddon

parties_schema = importlib.import_module("angee.parties.schema")
parties_mcp_tools = importlib.import_module("angee.parties.mcp_tools")


@pytest.fixture
def parties_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``GraphQLSchemas.from_discovery`` at the parties-only ``public`` bucket."""

    parts = {key: tuple(parties_schema.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}
    schemas = GraphQLSchemas([SchemaAddon({"public": parts})])
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))


def _registered_tools() -> dict[str, Any]:
    server = FastMCP(name="test-parties")
    parties_mcp_tools.register(server)
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


@pytest.mark.usefixtures("parties_discovery")
def test_the_bulk_merge_tool_is_a_bounded_write_over_its_mutation() -> None:
    """Agents merge certain pairs through the same mutation the Review page calls, as themselves."""

    tool = _registered_tools()["merge_certain_same_as_pairs"]

    assert tool.annotations.readOnlyHint is False
    assert "merge_certain_same_as_pairs(" in tool.document
    assert set(tool.parameters["properties"]) == {"limit"}
