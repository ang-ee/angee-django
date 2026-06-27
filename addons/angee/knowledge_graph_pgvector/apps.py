"""Django config for the knowledge pgvector (semantic retrieval) plugin."""

from __future__ import annotations

from django.apps import AppConfig


class KnowledgeGraphPgvectorConfig(AppConfig):
    """Source app manifest for the knowledge semantic-retrieval plugin.

    Composes the knowledge and MCP base addons through their declared seams:
    ``schemas`` contributes a ``related_pages`` projection plus a
    ``semantic_search`` query, ``mcp_tools`` exposes that query as an agent tool,
    and the co-located ``autoconfig.SETTINGS`` registers one retrieval-provider
    impl. It never imports or edits ``angee.knowledge``.
    """

    default = True
    angee_addon = True
    name = "angee.knowledge_graph_pgvector"
    label = "knowledge_graph_pgvector"
    depends_on = ("angee.knowledge", "angee.mcp")
    schemas = "schema.schemas"
    # ``mcp_tools`` (never ``mcp``) so the module never shadows the third-party
    # ``mcp`` package; ``register`` adds this plugin's GraphQL-backed tools.
    mcp_tools = "mcp_tools.register"
