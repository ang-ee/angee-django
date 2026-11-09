"""Vault model selection and related-page projections over scoped retrieval."""

from typing import cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto
from strawberry_django.optimizer import optimize

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


@strawberry.type
class GraphPgvectorQuery:
    @strawberry.field
    def related_pages(self, info: strawberry.Info, page: PublicID, first: int = 5) -> list[PageType]:
        target = require_instance_for_id(Page, page)
        hits = PgvectorRetrievalBackend.related_to(target, first=first)
        pages = {row.pk: row for row in optimize(Page.objects.filter(pk__in=[hit.page.pk for hit in hits]), info)}
        return cast("list[PageType]", [pages[hit.page.pk] for hit in hits if hit.page.pk in pages])


_PGVECTOR_BUCKET = {
    "query": [GraphPgvectorQuery],
    "type_extensions": [VaultEmbeddingExtension, VaultGraphExtractionExtension],
}
schemas = {"public": {**_PGVECTOR_BUCKET}, "console": {**_PGVECTOR_BUCKET}}
