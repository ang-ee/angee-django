"""GraphQL contributions for the knowledge pgvector plugin.

Two seams onto the knowledge schema, neither editing it:

- ``type_extensions`` — :class:`PageGraphPgvectorExtension` adds a
  ``related_pages`` projection onto knowledge's ``PageType`` (mirrors
  ``iam_integrate_oidc``'s ``OAuthClientOidcExtension``). The composer has already
  folded the type by name, so the extra field reads as native.
- ``query`` — :class:`GraphPgvectorQuery` adds a ``semantic_search`` root field
  that forces this plugin's ``pgvector`` retrieval backend, resolved through the
  vault's public ``retrieval_for`` seam (so the strategy stays swappable, not
  hard-wired, and the plugin never touches knowledge's model internals).

Both buckets carry the same parts into ``public`` and ``console``, the schema
names knowledge exposes ``PageType`` on. The provider is a lexical stub today
(see :mod:`~angee.knowledge_graph_pgvector.retrieval`); the GraphQL shape is what
a real semantic backend drops into.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps

from angee.graphql.ids import PublicID, require_instance_for_id
from angee.knowledge.schema import PageType

Page = apps.get_model("knowledge", "Page")
Vault = apps.get_model("knowledge", "Vault")

# Cap the placeholder neighbour set; a real plugin ranks by vector distance.
_RELATED_LIMIT = 5


@strawberry_django.type(Page, name="PageType", extend=True)
class PageGraphPgvectorExtension:
    """Contributes ``related_pages`` onto knowledge's ``PageType``.

    The projection seam a graph-RAG plugin uses to surface neighbours on the page
    read. The skeleton returns actor-visible sibling pages in the same vault as a
    structural placeholder — REBAC row scope is applied (``apply_ambient_scope``,
    like ``PageType.backlinks``); a real plugin ranks by embedding distance over
    the column it adds to ``knowledge.Page``.
    """

    @strawberry_django.field(only=["vault_id"])
    def related_pages(self) -> list[PageType]:
        """Return up to a handful of actor-visible related pages in the same vault."""

        rows = (
            Page._default_manager.filter(vault_id=cast(Any, self).vault_id)
            .exclude(pk=cast(Any, self).pk)
            .order_by("title", "sqid")
            .apply_ambient_scope()
        )
        return cast("list[PageType]", list(rows[:_RELATED_LIMIT]))


@strawberry.type
class GraphPgvectorQuery:
    """Semantic content queries this plugin adds to the knowledge surface."""

    @strawberry.field
    def semantic_search(self, vault: PublicID, query: str, first: int = 10) -> list[PageType]:
        """Return actor-visible pages in ``vault`` semantically matching ``query``.

        Unlike knowledge's ``search_pages`` (which honours the vault's configured
        default), this forces the plugin's ``pgvector`` backend — resolved through
        the vault's public :meth:`~angee.knowledge.models.Vault.retrieval_for` seam,
        so the plugin selects its strategy by key without reaching into knowledge's
        model internals. Row scope is the backend's responsibility
        (``apply_ambient_scope``). The backend is a lexical stub until embeddings exist.
        """

        target = require_instance_for_id(Vault, vault)
        backend = target.retrieval_for("pgvector")
        return cast("list[PageType]", list(backend.search(query, first=first)))


_PGVECTOR_BUCKET = {
    "query": [GraphPgvectorQuery],
    "type_extensions": [PageGraphPgvectorExtension],
}


schemas = {
    "public": {**_PGVECTOR_BUCKET},
    "console": {**_PGVECTOR_BUCKET},
}
"""GraphQL contributions installed by the knowledge pgvector plugin."""
