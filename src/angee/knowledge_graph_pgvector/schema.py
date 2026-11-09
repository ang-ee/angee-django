"""Vault model selection and related-page projections over scoped retrieval."""

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.ids import PublicID, public_id_field, require_instance_for_id
from angee.knowledge.schema import PageType
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend

Page = apps.get_model("knowledge", "Page")
Vault = apps.get_model("knowledge", "Vault")


@strawberry_django.type(Vault, name="VaultType", extend=True)
class VaultEmbeddingExtension:
    embedding_model = public_id_field("embedding_model", nullable=True)


@strawberry_django.type(Vault, name="VaultType", extend=True)
class VaultGraphExtractionExtension:
    extraction_model = public_id_field("extraction_model", nullable=True)
    graph_entity_kinds: auto


@strawberry_django.type(Page, name="PageType", extend=True)
class PageGraphPgvectorExtension:
    @strawberry_django.field(only=["id", "vault_id"])
    def related_pages(self, first: int = 5) -> list[PageType]:
        return cast(
            "list[PageType]", [hit.page for hit in PgvectorRetrievalBackend.related_to(cast(Any, self), first=first)]
        )


@strawberry.type
class GraphPgvectorQuery:
    @strawberry.field
    def related_pages(self, page: PublicID, first: int = 5) -> list[PageType]:
        target = require_instance_for_id(Page, page)
        return cast("list[PageType]", [hit.page for hit in PgvectorRetrievalBackend.related_to(target, first=first)])


_PGVECTOR_BUCKET = {
    "query": [GraphPgvectorQuery],
    "type_extensions": [VaultEmbeddingExtension, VaultGraphExtractionExtension, PageGraphPgvectorExtension],
}
schemas = {"public": {**_PGVECTOR_BUCKET}, "console": {**_PGVECTOR_BUCKET}}
