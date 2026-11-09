"""Commit dispatch, bounded backfill, provider retry and snapshot fencing."""

from unittest.mock import Mock

import pytest
from django.db import connection, transaction
from rebac import actor_context, system_context

from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector import tasks
from angee.knowledge_graph_pgvector.constants import EMBED_PAGE_TASK, RECONCILE_BATCH, RECONCILE_TASK
from angee.knowledge_graph_pgvector.managers import TransientEmbeddingError
from angee.knowledge_graph_pgvector.signals import enqueue_embedding
from angee.knowledge_graph_pgvector.testing.models import Passage, PassageEmbedding
from tests.conftest import MarkdownPage, Page, Vault
from tests.pgvector_fixtures import passage_records as passage_records


def test_save_dispatch_waits_for_commit_and_raw_saves_do_not_enqueue(passage_records, monkeypatch):
    send = Mock()
    monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", send)
    with transaction.atomic():
        enqueue_embedding(MarkdownPage, passage_records.markdown, update_fields={"body"})
        assert not send.called
    assert send.call_args.args == (EMBED_PAGE_TASK,)
    assert send.call_args.kwargs["kwargs"] == {"pk": passage_records.page.pk}
    send.reset_mock()
    enqueue_embedding(MarkdownPage, passage_records.markdown, raw=True)
    enqueue_embedding(Page, passage_records.page, update_fields={"icon"})
    assert not send.called
    with transaction.atomic():
        enqueue_embedding(Vault, passage_records.vault, update_fields={"embedding_model"})
        transaction.set_rollback(True)
    assert not send.called
    enqueue_embedding(Vault, passage_records.vault, update_fields={"retrieval_class"})
    assert send.call_args.args == (RECONCILE_TASK,)


def test_reconciler_bounds_a_backfill_and_recovers_unindexed_pages(passage_records, monkeypatch):
    records = passage_records
    with actor_context(records.owner):
        for index in range(RECONCILE_BATCH + 3):
            Page.objects.create_in(records.vault, title=f"Unindexed {index}", body="New body")
    send = Mock()
    monkeypatch.setattr("angee.knowledge_graph_pgvector.managers.enqueue_task", send)
    assert tasks.reconcile_embeddings() == RECONCILE_BATCH
    assert send.call_count == RECONCILE_BATCH
    assert all(call.args == (EMBED_PAGE_TASK,) for call in send.call_args_list)


def _embedding_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("angee.knowledge_graph_pgvector.managers.require_pgvector", lambda: None)
    monkeypatch.setattr(InferenceModel, "max_input_tokens", lambda self: 2000)
    monkeypatch.setattr(InferenceModel, "embedding_dimensions", property(lambda self: 2))

    def embed(self, texts, *, input_type, role):
        assert not connection.in_atomic_block
        assert (input_type, role) == ("document", "knowledge_embedding")
        calls.append(texts)
        return [[1.0, 0.0] for text in texts]

    monkeypatch.setattr(InferenceModel, "embed", embed)
    return calls


def test_embed_task_batches_and_is_idempotent(passage_records, monkeypatch):
    calls = _embedding_calls(monkeypatch)
    assert tasks.embed_page(passage_records.page.pk) == 2
    assert len(calls) == 1 and len(calls[0]) == 2
    assert tasks.embed_page(passage_records.page.pk) == 0
    assert len(calls) == 1
    with system_context(reason="test.pgvector.embedded"):
        assert PassageEmbedding.objects.count() == 2
        assert not Passage.objects.stale_pages().exists()


def test_model_change_reembeds_without_rechunking(passage_records, monkeypatch):
    _embedding_calls(monkeypatch)
    records = passage_records
    assert tasks.embed_page(records.page.pk) == 2
    original = [row.pk for row in records.passages]
    with system_context(reason="test.pgvector.change_model"):
        next_model = InferenceModel.objects.create(provider=records.model.provider, name="next", model_use="embedding")
        Vault.objects.filter(pk=records.vault.pk).update(embedding_model=next_model)
    assert tasks.embed_page(records.page.pk) == 2
    with system_context(reason="test.pgvector.model_spaces"):
        assert list(Passage.objects.order_by("ordinal").values_list("pk", flat=True)) == original
        assert PassageEmbedding.objects.filter(model=records.model).count() == 2
        assert PassageEmbedding.objects.filter(model=next_model).count() == 2


def test_late_provider_response_cannot_publish_over_changed_body(passage_records, monkeypatch):
    _embedding_calls(monkeypatch)
    records = passage_records

    def embed(self, texts, **kwargs):
        with system_context(reason="test.pgvector.concurrent_edit"):
            MarkdownPage.objects.filter(pk=records.page.pk).update(body="Changed while provider worked")
        return [[1.0, 0.0] for text in texts]

    monkeypatch.setattr(InferenceModel, "embed", embed)
    assert tasks.embed_page(records.page.pk) == 0
    with system_context(reason="test.pgvector.fenced"):
        assert not PassageEmbedding.objects.exists()


def test_provider_transient_classification_controls_retry(passage_records, monkeypatch):
    _embedding_calls(monkeypatch)
    monkeypatch.setattr(InferenceModel, "embed", Mock(side_effect=TimeoutError("provider timeout")))
    monkeypatch.setattr(InferenceModel, "is_transient_error", lambda self, error: isinstance(error, TimeoutError))
    with pytest.raises(TransientEmbeddingError):
        PassageEmbedding.objects.embed_page(passage_records.page.pk)
    monkeypatch.setattr(InferenceModel, "embed", Mock(side_effect=ValueError("bad request")))
    with pytest.raises(ValueError, match="bad request"):
        PassageEmbedding.objects.embed_page(passage_records.page.pk)


@pytest.mark.parametrize(
    "vectors,count,dimensions",
    [
        ([], 1, 2),
        ([[1.0]], 1, 2),
        ([[1.0, float("nan")]], 1, 2),
        ([[0.0, 0.0]], 1, 2),
        ([[1.0, 0.0], [1.0, 0.0, 0.0]], 2, None),
    ],
)
def test_embedding_batch_refuses_invalid_vectors(vectors, count, dimensions):
    with pytest.raises(ValueError):
        PassageEmbedding.objects.validate_vectors(vectors, count=count, dimensions=dimensions)
