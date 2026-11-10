"""PostgreSQL exact distance, full-text ranking, graph fusion and read boundaries."""

from unittest.mock import Mock

import pytest
from django.db import connection
from rebac import actor_context, system_context

from angee.agents.testing.models import InferenceModel, InferenceProvider
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend
from angee.knowledge_graph_pgvector.testing.models import Passage, PassageEmbedding
from angee.mcp.graphql import _execute_sync
from tests.conftest import MarkdownPage, Page, Vault, create_user, make_integration, vault_for
from tests.pgvector_fixtures import NativeEmbeddingBackend
from tests.pgvector_fixtures import passage_records as passage_records
from tests.test_knowledge_plugin import _merged_schemas

pytestmark = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="Requires PostgreSQL pgvector and full-text search"
)


def _indexed_page(records, *, title, body, vector, vault=None, model=None):
    with actor_context(records.owner):
        page = Page.objects.create_in(vault or records.vault, title=title, body=body)
        markdown = MarkdownPage.objects.get(pk=page.pk)
    passages = Passage.objects.rebuild_for(markdown)
    with system_context(reason="test.pgvector.vector"):
        for passage in passages:
            PassageEmbedding.objects.create(passage=passage, model=model or records.model, embedding=vector)
    return page


def test_exact_vector_rank_is_scoped_and_never_mixes_dimensions(passage_records):
    records = passage_records
    near = _indexed_page(records, title="Near", body="Near evidence", vector=[1, 0])
    far = _indexed_page(records, title="Far", body="Far evidence", vector=[0, 1])
    private = vault_for(records.owner, name="Private")
    hidden = _indexed_page(records, title="Hidden", body="Private evidence", vector=[1, 0], vault=private)
    with system_context(reason="test.pgvector.other_space"):
        other = InferenceModel.objects.create(provider=records.model.provider, name="three", model_use="embedding")
        Vault.objects.filter(pk=private.pk).update(retrieval_class="pgvector", embedding_model=other)
        PassageEmbedding.objects.create(passage=records.passages[0], model=other, embedding=[1, 0, 0])
    reader = create_user("vector-reader")
    with actor_context(records.owner):
        records.vault.grant_record_access("viewer", reader)
    with actor_context(reader):
        pages = PgvectorRetrievalBackend.pages((records.vault, private))
        hits = PgvectorRetrievalBackend.vector_arm(pages, records.model.pk, [1, 0], first=10)
        assert [hit.page.pk for hit in hits] == [near.pk, far.pk]
        assert all(hit.page.pk != hidden.pk for hit in hits)
        assert hits[0].text == "Near evidence"


def test_full_text_uses_simple_websearch_and_returns_best_passage(passage_records):
    records = passage_records
    page = _indexed_page(
        records, title="Instructions", body="# Other\nUnrelated\n# Motion\nRunning daily", vector=[1, 0]
    )
    with actor_context(records.owner):
        pages = PgvectorRetrievalBackend.pages((records.vault,))
        hits = PgvectorRetrievalBackend.lexical_arm(pages, '"running daily"', first=10)
        assert [hit.page.pk for hit in hits] == [page.pk]
        assert hits[0].heading_path == ("Motion",)
        assert "Running daily" in hits[0].text
        assert PgvectorRetrievalBackend.lexical_arm(pages, "run", first=10) == []


def test_query_embeds_once_per_model_for_reader_without_catalogue_grants(passage_records, monkeypatch):
    records = passage_records
    _indexed_page(records, title="Vector match", body="Evidence", vector=[1, 0])
    second = vault_for(records.owner, name="Another vault")
    with system_context(reason="test.pgvector.same_model"):
        Vault.objects.filter(pk=second.pk).update(retrieval_class="pgvector", embedding_model=records.model)
    second.refresh_from_db()
    _indexed_page(records, title="Second vector", body="Evidence", vector=[0, 1], vault=second)
    reader = create_user("query-reader")
    with actor_context(records.owner):
        records.vault.grant_record_access("viewer", reader)
        second.grant_record_access("viewer", reader)
    embed = Mock(return_value=[[1.0, 0.0]])
    monkeypatch.setattr(InferenceModel, "embed", embed)
    monkeypatch.setattr(InferenceModel, "embedding_dimensions", property(lambda self: 2))
    with actor_context(reader):
        assert not records.model.with_actor(reader).has_access("read")
        assert len(Vault.objects.search_passages("query", first=10).hits) == 2
    embed.assert_called_once_with(["query"], input_type="query", role="knowledge_embedding")


def test_provider_failure_is_visible_even_when_full_text_matches(passage_records, monkeypatch):
    monkeypatch.setattr(InferenceModel, "embed", Mock(side_effect=RuntimeError("provider unavailable")))
    with actor_context(passage_records.owner):
        result = Vault.objects.search_passages("Alpha")
    assert result.hits and result.hits[0].page.pk == passage_records.page.pk
    assert len(result.failures) == 1
    assert result.failures[0].arm == "vector"
    assert "provider unavailable" not in result.failures[0].message


def test_query_rechecks_deployment_approval(passage_records, settings, monkeypatch):
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"knowledge_embedding": []}
    embed = Mock()
    monkeypatch.setattr(InferenceModel, "embed", embed)
    with actor_context(passage_records.owner):
        result = Vault.objects.search_passages("Alpha")
        assert result.failures and result.hits
    embed.assert_not_called()


def test_related_pages_fuses_centroid_and_links_without_provider_call(passage_records, monkeypatch):
    records = passage_records
    with system_context(reason="test.pgvector.source_vectors"):
        for passage in records.passages:
            PassageEmbedding.objects.create(passage=passage, model=records.model, embedding=[1, 0])
    vector = _indexed_page(records, title="Vector neighbour", body="Unlinked", vector=[1, 0])
    link = _indexed_page(records, title="Linked neighbour", body="[[Guide]]", vector=[0, 1])
    embed = Mock(side_effect=AssertionError("related_pages needs no provider"))
    monkeypatch.setattr(InferenceModel, "embed", embed)
    with actor_context(records.owner):
        hits = PgvectorRetrievalBackend.related_to(records.page, first=5)
        assert {hit.page.pk for hit in hits} == {vector.pk, link.pk}
        assert hits[0].page.pk == link.pk  # Appears in both arms.
        link.trash()
        assert [hit.page.pk for hit in PgvectorRetrievalBackend.related_to(records.page)] == [vector.pk]
    embed.assert_not_called()



def test_native_embedding_backend_indexes_and_searches_without_embed_monkeypatch(passage_records):
    records = passage_records
    NativeEmbeddingBackend.calls.clear()
    provider = make_integration(
        "native-embedding",
        model=InferenceProvider,
        backend_class=NativeEmbeddingBackend.key,
    )
    with system_context(reason="test.pgvector.native_backend"):
        records.model.provider = provider
        records.model.save(update_fields=["provider"])
    assert PassageEmbedding.objects.embed_page(records.page.pk) == 2
    with actor_context(records.owner):
        first = _execute_sync(_merged_schemas().build("public"), """query {
          search_passages(query: "Alpha") { hits { page { id title } } failures { message } }
        }""", {})
        second = Vault.objects.search_passages("Alpha")
    assert not first.errors
    assert first.data["search_passages"] == {
        "hits": [{"page": {"id": str(records.page.sqid), "title": records.page.title}}], "failures": [],
    }
    assert not second.failures
    assert [hit.page.pk for hit in second.hits] == [records.page.pk]
    assert len(NativeEmbeddingBackend.calls) == 2  # Document batch, then one cached query.



def test_one_failed_model_group_does_not_drop_other_vector_groups(passage_records, monkeypatch):
    records = passage_records
    other_vault = vault_for(records.owner, name="Healthy provider")
    with system_context(reason="test.vector.healthy_group"):
        other_model = InferenceModel.objects.create(
            provider=records.model.provider, name="healthy", model_use="embedding",
        )
        Vault.objects.filter(pk=other_vault.pk).update(retrieval_class="pgvector", embedding_model=other_model)
    other_vault.refresh_from_db()
    healthy = _indexed_page(records, title="Healthy vector", body="Different evidence", vector=[1, 0],
                            vault=other_vault, model=other_model)

    def embed(self, texts, **kwargs):
        if self.pk == records.model.pk:
            raise RuntimeError("unavailable")
        return [[1.0, 0.0]]

    monkeypatch.setattr(InferenceModel, "embed", embed)
    with actor_context(records.owner):
        result = Vault.objects.search_passages("no lexical match")
    assert [hit.page.pk for hit in result.hits] == [healthy.pk]
    assert [failure.vaults for failure in result.failures] == [(str(records.vault.sqid),)]


def test_query_failure_is_reported_even_without_hits(passage_records, monkeypatch):
    monkeypatch.setattr(InferenceModel, "embed", Mock(side_effect=RuntimeError("unavailable")))
    with actor_context(passage_records.owner):
        result = Vault.objects.search_passages("absent term")
    assert result.hits == []
    assert len(result.failures) == 1
