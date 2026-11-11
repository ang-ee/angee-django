"""The pgvector addon composes knowledge's registry, schema and MCP contracts."""

from __future__ import annotations

import asyncio
import importlib
from typing import cast

import pytest
from django.apps import apps
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from graphql import GraphQLObjectType
from rebac import actor_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from angee.knowledge.retrieval import RetrievalBackend, RetrievalHit, RetrievalResult
from angee.knowledge_graph_pgvector.constants import RECONCILE_TASK
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend
from angee.mcp.graphql import DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT
from tests.conftest import Page, SchemaAddon, Vault, create_user, vault_for

knowledge_schema = importlib.import_module("angee.knowledge.schema")
plugin_schema = importlib.import_module("angee.knowledge_graph_pgvector.schema")
plugin_mcp_tools = importlib.import_module("angee.knowledge_graph_pgvector.mcp_tools")
plugin_autoconfig = importlib.import_module("angee.knowledge_graph_pgvector.autoconfig")


def _merged_schemas() -> GraphQLSchemas:
    return GraphQLSchemas(
        [
            SchemaAddon({"public": {key: tuple(module.schemas["public"].get(key, ())) for key in SCHEMA_PART_KEYS}})
            for module in (knowledge_schema, plugin_schema)
        ]
    )


def test_pgvector_provider_and_reconciler_resolve_from_declared_settings():
    field = apps.get_model("knowledge", "Vault")._meta.get_field("retrieval_class")
    assert field.resolve_class("pgvector") is PgvectorRetrievalBackend
    assert issubclass(PgvectorRetrievalBackend, RetrievalBackend)
    schedule = plugin_autoconfig.SETTINGS["CELERY_BEAT_SCHEDULE:append"][RECONCILE_TASK]
    assert schedule["task"] == RECONCILE_TASK
    assert schedule["schedule"] > 0


class GroupedSearchPlugin(RetrievalBackend):
    key = "lexical"


def test_grouped_and_bound_search_dispatch_to_hit_contract(db, settings, monkeypatch):
    settings.ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES = {
        **settings.ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES,
        "lexical": "tests.test_knowledge_plugin.GroupedSearchPlugin",
    }
    owner = create_user("plugin-search-owner")
    first, second = vault_for(owner, name="First"), vault_for(owner, name="Second")
    with actor_context(owner):
        page = Page.objects.create_in(first, title="Evidence", kind="folder")
    hit = RetrievalHit(page=page, rank=1)
    calls = []

    def search_many(cls, vaults, query, *, first=20):
        calls.append(([vault.pk for vault in vaults], query, first))
        return RetrievalResult(hits=[hit])

    monkeypatch.setattr(GroupedSearchPlugin, "search_many", classmethod(search_many))
    with actor_context(owner):
        assert first.retrieval.search("query", first=3).hits == [hit]
        assert Vault.objects.search_pages("query", first=7) == [page]
    assert calls == [([first.pk], "query", 3), ([first.pk, second.pk], "query", 7)]


def test_related_pages_and_vault_model_extend_the_knowledge_schema():
    schema = _merged_schemas().graphql_schema("public")
    assert "related_pages" not in cast(GraphQLObjectType, schema.type_map["PageType"]).fields
    assert "embedding_model" in cast(GraphQLObjectType, schema.type_map["VaultType"]).fields
    assert {"extraction_model", "graph_entity_kinds"} <= cast(
        GraphQLObjectType, schema.type_map["VaultType"]
    ).fields.keys()
    assert {"search_pages", "search_passages", "related_pages"} <= schema.query_type.fields.keys()
    assert "semantic_search" not in schema.query_type.fields


@pytest.fixture
def plugin_discovery(monkeypatch):
    schemas = _merged_schemas()
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))


@pytest.mark.usefixtures("plugin_discovery")
def test_related_pages_tool_compiles_with_bounded_inputs():
    server = FastMCP(name="test-knowledge-pgvector")
    plugin_mcp_tools.register(server)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {"related_pages"}
    tool = tools["related_pages"]
    assert tool.annotations.readOnlyHint is True
    assert set(tool.parameters["properties"]) == {"page", "first"}
    assert tool.parameters["required"] == ["page"]
    assert "related_pages(first: $first, page: $page)" in tool.document
    assert tool._variables({"page": "page-id"})["first"] == DEFAULT_QUERY_LIMIT
    with pytest.raises(ToolError, match="must not exceed"):
        tool._variables({"page": "page-id", "first": MAX_QUERY_LIMIT + 1})


def test_related_pages_resolver_refuses_an_unreadable_source(db, monkeypatch):
    owner, outsider = create_user("related-owner"), create_user("related-outsider")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Private source")
    monkeypatch.setattr(PgvectorRetrievalBackend, "related_to", classmethod(lambda cls, *args, **kwargs: []))
    schema = _merged_schemas().build("public")
    document = "query ($page: ID!) { related_pages(page: $page) { id title } }"
    assert execute_schema(schema, document, {"page": str(page.sqid)}, user=outsider).errors
    assert result_data(execute_schema(schema, document, {"page": str(page.sqid)}, user=owner))["related_pages"] == []


def test_manifest_and_native_config_wire_addon_contributions():
    from django_compose.components import enabled_apps

    from angee.graphql.schema import schema_parts_for
    from angee.mcp.server import tool_registrar

    config = apps.get_app_config("knowledge_graph_pgvector")
    dependencies = enabled_apps((config.name,), group="angee.addons").dependencies[config.name]
    assert {"angee.knowledge", "angee.mcp", "angee.agents", "angee.jobs"} <= set(dependencies)
    assert "public" in schema_parts_for(config)
    assert tool_registrar(config) is plugin_mcp_tools.register
