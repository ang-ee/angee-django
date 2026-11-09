"""Commit dispatch, bounded backfill, provider retry and snapshot fencing."""

from unittest.mock import Mock

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.utils import timezone
from rebac import actor_context, system_context

from angee.agents.backends import TransientInferenceError
from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector import tasks
from angee.knowledge_graph_pgvector.constants import EMBED_PAGE_TASK, RECONCILE_BATCH, RECONCILE_TASK
from angee.knowledge_graph_pgvector.signals import enqueue_embedding
from angee.knowledge_graph_pgvector.testing.models import Passage, PassageEmbedding
from tests.conftest import MarkdownPage, Page, Vault
from tests.pgvector_fixtures import passage_records as passage_records

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL index failure state")


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
    assert tasks.reconcile_index() == RECONCILE_BATCH
    assert send.call_count == RECONCILE_BATCH
    assert all(call.args == (EMBED_PAGE_TASK,) for call in send.call_args_list)


def _embedding_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend.require_database", lambda: None,
    )
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
    send = Mock()
    monkeypatch.setattr("angee.knowledge_graph_pgvector.managers.enqueue_task", send)
    assert tasks.embed_page(records.page.pk) == 0
    assert send.call_args.args == (EMBED_PAGE_TASK,)
    assert send.call_args.kwargs["kwargs"] == {"pk": records.page.pk}
    with system_context(reason="test.pgvector.fenced"):
        assert not PassageEmbedding.objects.exists()


def test_provider_transient_classification_controls_retry(passage_records, monkeypatch):
    _embedding_calls(monkeypatch)
    monkeypatch.setattr(InferenceModel, "embed", Mock(side_effect=TransientInferenceError("provider timeout")))
    with pytest.raises(TransientInferenceError):
        PassageEmbedding.objects.embed_page(passage_records.page.pk)
    with system_context(reason="test.pgvector.retry_due"):
        Page.objects.filter(pk=passage_records.page.pk).update(index_retry_at=timezone.now())
    monkeypatch.setattr(InferenceModel, "embed", Mock(side_effect=ValueError("bad request")))
    with pytest.raises(ValueError, match="bad request"):
        PassageEmbedding.objects.embed_page(passage_records.page.pk)



def test_rebuild_precedes_admission_and_failure_marker_reopens_for_new_source(passage_records, settings, monkeypatch):
    records = passage_records
    _embedding_calls(monkeypatch)
    # Exercise the real model admission, while the request itself remains unused.
    def denied(self, texts, *, input_type, role):
        self.require_approved(role)
        raise AssertionError("An unapproved request must never run")

    monkeypatch.setattr(InferenceModel, "embed", denied)
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"knowledge_embedding": []}
    with system_context(reason="test.index.admission"):
        Passage.objects.filter(page=records.page).delete()
    with pytest.raises(PermissionDenied):
        PassageEmbedding.objects.embed_page(records.page.pk)
    with system_context(reason="test.index.failure"):
        assert Passage.objects.filter(page=records.page).count() == 2
        page = Page.objects.get(pk=records.page.pk)
        assert page.index_attempts == 1 and page.index_retry_at is not None
        assert not Passage.objects.stale_pages().filter(pk=page.pk).exists()
        MarkdownPage.objects.filter(pk=page.pk).update(body="A new source")
        assert Passage.objects.stale_pages().filter(pk=page.pk).exists()


def test_non_pgvector_saves_do_not_enqueue(passage_records, monkeypatch):
    with system_context(reason="test.index.other_backend"):
        Vault.objects.filter(pk=passage_records.vault.pk).update(retrieval_class="lexical")
    send = Mock()
    monkeypatch.setattr("angee.knowledge_graph_pgvector.signals.enqueue_task", send)
    enqueue_embedding(MarkdownPage, passage_records.markdown, update_fields={"body"})
    send.assert_not_called()


def test_reconciler_collects_only_unselected_model_vectors(passage_records):
    records = passage_records
    with system_context(reason="test.index.vector_gc"):
        other = InferenceModel.objects.create(provider=records.model.provider, name="unused", model_use="embedding")
        kept = PassageEmbedding.objects.create(passage=records.passages[0], model=records.model, embedding=[1, 0])
        dead = PassageEmbedding.objects.create(passage=records.passages[0], model=other, embedding=[0, 1])
        assert PassageEmbedding.objects.collect_unused() == 1
        assert PassageEmbedding.objects.filter(pk=kept.pk).exists()
        assert not PassageEmbedding.objects.filter(pk=dead.pk).exists()
