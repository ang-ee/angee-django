"""Tests that the ``knowledge_graph_pgvector`` plugin composes the knowledge addon.

The whole point of the plugin skeleton is **extensibility without editing the
knowledge addon**: a semantic backend plugs in through three declared seams — the
retrieval-backend registry, a GraphQL projection/query, and an MCP tool. These
tests compose the plugin's ``public`` schema bucket *on top of* knowledge's (the
way the composer merges per bucket across addons) and assert each seam holds:

- the ``pgvector`` backend resolves from the vault-owned ``ImplClassField``
  registry (declared by the plugin's autoconfig dotted-key);
- ``related_pages`` appears on knowledge's ``PageType``;
- the plugin's ``semantic_search`` MCP tool compiles against the merged schema.

No file under ``addons/angee/knowledge/`` is touched — the plugin imports only the
declared knowledge contracts (the schema types, the model registry, the backend
base). The merged schema is built standalone (no DB needed), like
:mod:`tests.test_knowledge_mcp`.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any, cast

import pytest
from django.apps import apps
from fastmcp import FastMCP
from graphql import GraphQLObjectType

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.knowledge.retrieval import LexicalRetrievalBackend
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend
from tests.conftest import SchemaAddon

knowledge_schema = importlib.import_module("angee.knowledge.schema")
plugin_schema = importlib.import_module("angee.knowledge_graph_pgvector.schema")
plugin_mcp_tools = importlib.import_module("angee.knowledge_graph_pgvector.mcp_tools")
plugin_autoconfig = importlib.import_module("angee.knowledge_graph_pgvector.autoconfig")

_PGVECTOR_PROVIDER_PATH = "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend"


def _bucket_parts(schemas: dict[str, Any]) -> dict[str, tuple[object, ...]]:
    """Return one addon's ``public`` bucket as normalized schema-part tuples."""

    return {key: tuple(schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}


def _merged_schemas() -> GraphQLSchemas:
    """Compose the plugin's ``public`` bucket on top of knowledge's, addon-merged.

    Two ``SchemaAddon`` stand-ins run the real per-bucket ``SchemaParts.merge`` the
    composer uses, so ``PageType`` (knowledge) and the plugin's ``related_pages``
    extension + ``semantic_search`` query land in one schema — proving the merge
    seam without standing up full discovery.
    """

    return GraphQLSchemas(
        [
            SchemaAddon({"public": _bucket_parts(knowledge_schema.schemas)}),
            SchemaAddon({"public": _bucket_parts(plugin_schema.schemas)}),
        ]
    )


# --- Seam 1: retrieval-backend registry ----------------------------------------


def test_autoconfig_declares_namespaced_provider_key() -> None:
    """The plugin contributes its impl via the autoconfig dotted-key deep-merge.

    The key is namespaced to the plugin (``...RETRIEVAL_CLASSES.pgvector``) so the
    composer merges one entry into knowledge's registry dict without a collision or
    an edit to the knowledge addon.
    """

    assert plugin_autoconfig.SETTINGS == {
        "ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES.pgvector": _PGVECTOR_PROVIDER_PATH,
    }


def test_pgvector_provider_resolves_from_registry(settings: Any) -> None:
    """Once merged, the vault's ``ImplClassField`` resolves ``pgvector`` to the impl.

    Mirrors the composer's deep-merge by adding the namespaced key to the registry
    setting, then resolving through the vault-owned field — the same path
    knowledge's ``search_pages`` resolver takes. The stub provider subclasses the
    lexical default, so it inherits a working search until embeddings exist.
    """

    settings.ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES = {
        **settings.ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES,
        "pgvector": _PGVECTOR_PROVIDER_PATH,
    }
    vault_model = apps.get_model("knowledge", "Vault")
    field = vault_model._meta.get_field("retrieval_class")

    assert field.resolve_class("pgvector") is PgvectorRetrievalBackend
    assert issubclass(PgvectorRetrievalBackend, LexicalRetrievalBackend)
    assert PgvectorRetrievalBackend.key == "pgvector"


# --- Seam 2: GraphQL projection + query ------------------------------------------


def test_related_pages_extends_pagetype_and_adds_semantic_search() -> None:
    """The plugin adds ``related_pages`` to ``PageType`` and a ``semantic_search`` query.

    Both land in the merged schema with no edit to knowledge — the projection seam
    (``type_extensions``) and the root-operation seam (``query``) the composer
    merges per bucket.
    """

    gc = _merged_schemas().graphql_schema("public")

    page_type = cast(GraphQLObjectType, gc.type_map["PageType"])
    assert "related_pages" in page_type.fields
    assert "PageType" in str(page_type.fields["related_pages"].type)

    assert gc.query_type is not None
    assert "semantic_search" in gc.query_type.fields
    # The plugin's query coexists with knowledge's own search field (no collision).
    assert "search_pages" in gc.query_type.fields


# --- Seam 3: MCP tool ------------------------------------------------------------


@pytest.fixture
def plugin_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``GraphQLSchemas.from_discovery`` at the merged knowledge+plugin bucket.

    The MCP compiler resolves each spec against ``from_discovery().graphql_schema``;
    pinning it to the merged bucket compiles the plugin's ``semantic_search`` over
    the query it just contributed, without standing up full discovery.
    """

    schemas = _merged_schemas()
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))


def _registered_tools() -> dict[str, Any]:
    """Compile and register the plugin's specs, returning the tool-name → tool map.

    Calling ``register`` runs the compiler's ``_compile``/``_validate``, so a spec
    that drifts from the schema raises here (the contract check).
    """

    server = FastMCP(name="test-knowledge-pgvector")
    plugin_mcp_tools.register(server)
    # fastmcp 3.x dropped the private _tool_manager; list_tools() (async) returns
    # the registered _CompiledTool objects, which still carry .document/.parameters.
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_semantic_search_tool_compiles(plugin_discovery: None) -> None:
    """The plugin's ``semantic_search`` tool compiles against the merged schema."""

    tools = _registered_tools()
    assert set(tools) == {"semantic_search"}

    tool = tools["semantic_search"]
    assert tool.annotations is not None and tool.annotations.readOnlyHint is True
    assert set(tool.parameters["properties"]) == {"vault", "query", "first"}
    assert tool.parameters["required"] == ["vault", "query"]  # first has a schema default
    assert tool.document == (
        "query ($vault: ID!, $query: String!, $first: Int!) "
        "{ semantic_search(vault: $vault, query: $query, first: $first) { id title kind } }"
    )
    # A list operation projects its rows under ``result``.
    assert set(tool.output_schema["properties"]) == {"result"}


# --- The composition contract: AppConfig wires the three seams -------------------


def test_appconfig_wires_the_seams() -> None:
    """The plugin AppConfig declares the seams and their dotted refs point at real objects.

    Proves the manifest is composable without installing the app: the schema/mcp
    dotted refs resolve to the contributions, and the plugin depends on both owners.
    """

    from angee.knowledge_graph_pgvector.apps import KnowledgeGraphPgvectorConfig

    assert KnowledgeGraphPgvectorConfig.depends_on == ("angee.knowledge", "angee.mcp")
    assert KnowledgeGraphPgvectorConfig.schemas == "schema.schemas"
    assert KnowledgeGraphPgvectorConfig.mcp_tools == "mcp_tools.register"
    # The dotted refs resolve to the contributions composed above.
    assert plugin_schema.schemas["public"]["query"]
    assert callable(plugin_mcp_tools.register)
