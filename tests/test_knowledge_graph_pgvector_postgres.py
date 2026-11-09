"""PostgreSQL exact distance, full-text ranking, graph fusion and read boundaries."""

from unittest.mock import Mock

import pytest
from django.db import connection
from rebac import PermissionDenied, actor_context, system_context

from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend
from angee.knowledge_graph_pgvector.testing.models import Passage, PassageEmbedding
from tests.conftest import MarkdownPage, Page, Vault, create_user, vault_for
from tests.pgvector_fixtures import passage_records as passage_records

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
        pages = PgvectorRetrievalBackend._pages((records.vault, private))
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
        pages = PgvectorRetrievalBackend._pages((records.vault,))
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
        assert len(Vault.objects.search_passages("query", first=10)) == 2
    embed.assert_called_once_with(["query"], input_type="query", role="knowledge_embedding")


def test_provider_failure_is_visible_even_when_full_text_matches(passage_records, monkeypatch):
    monkeypatch.setattr(InferenceModel, "embed", Mock(side_effect=RuntimeError("provider unavailable")))
    with actor_context(passage_records.owner), pytest.raises(RuntimeError, match="provider unavailable"):
        Vault.objects.search_passages("Alpha")


def test_query_rechecks_deployment_approval(passage_records, settings, monkeypatch):
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"knowledge_embedding": []}
    embed = Mock()
    monkeypatch.setattr(InferenceModel, "embed", embed)
    with actor_context(passage_records.owner), pytest.raises(PermissionDenied, match="not approved"):
        Vault.objects.search_passages("Alpha")
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
