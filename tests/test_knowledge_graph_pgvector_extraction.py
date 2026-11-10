"""Idle extraction, structured output, incremental evidence and fenced publication."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.utils import timezone
from pydantic import ValidationError
from rebac import actor_context, system_context

from angee.agents.backends import TransientInferenceError
from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector import tasks
from angee.knowledge_graph_pgvector.constants import EXTRACT_PASSAGE_TASK, RECONCILE_BATCH
from angee.knowledge_graph_pgvector.extraction import PassageGraph, entity_key
from angee.knowledge_graph_pgvector.options import GraphOptions
from angee.knowledge_graph_pgvector.testing.models import Entity, EntityEdge, EntityMention, Passage
from tests.conftest import MarkdownPage, Page, Vault, vault_for
from tests.pgvector_fixtures import passage_records as passage_records
from tests.pgvector_graph_fixtures import graph_records as graph_records

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL index failure state")

GRAPH = {
    "entities": [
        {"name": "Alpha", "kind": "concept", "description": "Alpha evidence."},
        {"name": "Beta", "kind": "concept", "description": "Beta evidence."},
    ],
    "edges": [
        {"subject": 0, "predicate": "relates_to", "object": 1, "description": "Passage evidence.", "strength": 0.8}
    ],
}


def provider(monkeypatch, *, output=GRAPH, during=None):
    calls = []
    monkeypatch.setattr(
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend.require_database", lambda: None,
    )

    def infer(self, messages, *, output_schema):
        assert not connection.in_atomic_block
        assert output_schema["title"] == "PassageGraph"
        kind = output_schema["$defs"]["GraphEntity"]["properties"]["kind"]
        assert "enum" in kind or "const" in kind
        calls.append(messages)
        if during:
            during()
        return SimpleNamespace(output=output)

    monkeypatch.setattr(InferenceModel, "infer", infer)
    return calls


def test_extract_task_is_schema_guided_and_idempotent_including_empty_results(graph_records, monkeypatch):
    records = graph_records
    calls = provider(monkeypatch)
    assert tasks.extract_passage(records.passages[0].pk) == 1
    assert tasks.extract_passage(records.passages[0].pk) == 0
    assert len(calls) == 1
    with system_context(reason="test.pgvector.graph_result"):
        assert Entity.objects.count() == 2
        assert EntityMention.objects.filter(passage=records.passages[0]).count() == 2
        assert EntityEdge.objects.filter(passage=records.passages[0]).get().weight == 0.8
    calls = provider(monkeypatch, output={"entities": [], "edges": []})
    assert tasks.extract_passage(records.passages[1].pk) == 1
    assert tasks.extract_passage(records.passages[1].pk) == 0
    assert len(calls) == 1
    with system_context(reason="test.pgvector.graph_empty_complete"):
        assert not Passage.objects.graph_stale().exists()


def test_graph_stale_waits_for_idle_and_opt_in(graph_records, monkeypatch):
    records = graph_records
    calls = provider(monkeypatch)
    with system_context(reason="test.pgvector.autosave"):
        Page.objects.filter(pk=records.page.pk).update(updated_at=timezone.now())
        assert not Passage.objects.graph_stale().exists()
    assert tasks.extract_passage(records.passages[0].pk) == 0
    with system_context(reason="test.pgvector.disable_extraction"):
        Page.objects.filter(pk=records.page.pk).update(updated_at=timezone.now() - timedelta(minutes=10))
        Vault.objects.filter(pk=records.vault.pk).update(extraction_model=None)
        assert not Passage.objects.graph_stale().exists()
    assert tasks.extract_passage(records.passages[0].pk) == 0
    assert not calls


def test_unchanged_hash_keeps_graph_and_new_text_waits_for_idle(graph_records, monkeypatch):
    records = graph_records
    calls = provider(monkeypatch)
    original = records.passages[0]
    assert tasks.extract_passage(original.pk) == 1
    with actor_context(records.owner):
        markdown = MarkdownPage.objects.get(pk=records.page.pk)
        markdown.body = markdown.body.replace("Beta evidence.", "A different second section.")
        markdown.save()
    rows = Passage.objects.rebuild_for(markdown)
    assert rows[0].pk == original.pk
    assert rows[1].pk != records.passages[1].pk
    assert tasks.extract_passage(original.pk) == 0
    assert tasks.extract_passage(rows[1].pk) == 0
    assert len(calls) == 1
    with system_context(reason="test.pgvector.retained_graph"):
        assert EntityEdge.objects.filter(passage_id=original.pk).exists()
        Page.objects.filter(pk=records.page.pk).update(updated_at=timezone.now() - timedelta(minutes=10))
        assert list(Passage.objects.graph_stale().values_list("pk", flat=True)) == [rows[1].pk]


def test_model_and_kind_changes_reextract_in_bounded_background(graph_records, monkeypatch):
    records = graph_records
    provider(monkeypatch, output={"entities": [], "edges": []})
    assert tasks.extract_passage(records.passages[0].pk) == 1
    with system_context(reason="test.pgvector.graph_new_model"):
        other = InferenceModel.objects.create(provider=records.model.provider, name="graph-next", model_use="chat")
        Vault.objects.filter(pk=records.vault.pk).update(extraction_model=other)
        assert Passage.objects.graph_stale().filter(pk=records.passages[0].pk).exists()
        assert not Passage.objects.graph_current().filter(pk=records.passages[0].pk).exists()
    assert tasks.extract_passage(records.passages[0].pk) == 1
    with system_context(reason="test.pgvector.graph_new_kinds"):
        Vault.objects.filter(pk=records.vault.pk).update(graph_entity_kinds=["concept"])
        assert Passage.objects.graph_stale().filter(pk=records.passages[0].pk).exists()
    assert tasks.extract_passage(records.passages[0].pk) == 1


@pytest.mark.parametrize("change", ["body", "model", "trash", "idle", "revoke"])
def test_late_extraction_cannot_publish_after_source_selection_or_approval_changes(
    graph_records, monkeypatch, settings, change
):
    records = graph_records

    def concurrent_change():
        with system_context(reason="test.pgvector.concurrent_graph_change"):
            if change == "body":
                MarkdownPage.objects.filter(pk=records.page.pk).update(body="Edited during inference")
            elif change == "model":
                Vault.objects.filter(pk=records.vault.pk).update(extraction_model=None)
            elif change == "trash":
                Page.objects.filter(pk=records.page.pk).update(is_trashed=True)
            elif change == "idle":
                Page.objects.filter(pk=records.page.pk).update(updated_at=timezone.now())
            else:
                settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"knowledge_graph_extraction": []}

    provider(monkeypatch, during=concurrent_change)
    if change == "revoke":
        with pytest.raises(PermissionDenied, match="not approved"):
            Passage.objects.extract_passage(records.passages[0].pk)
    else:
        assert tasks.extract_passage(records.passages[0].pk) == 0
    with system_context(reason="test.pgvector.graph_fenced"):
        assert not EntityMention.objects.exists()
        assert not EntityEdge.objects.exists()
        assert not Entity.objects.exists()


def test_gleaning_is_one_optional_pass_and_rechecks_role(graph_records, monkeypatch, settings):
    settings.ANGEE_KNOWLEDGE_GRAPH = {"gleaning_passes": 1}
    calls = provider(monkeypatch)
    approved = Mock()
    monkeypatch.setattr(InferenceModel, "require_approved", approved)
    assert tasks.extract_passage(graph_records.passages[0].pk) == 1
    assert len(calls) == 2
    assert all(call.args == ("knowledge_graph_extraction",) for call in approved.call_args_list)
    assert approved.call_count == 3  # Initial, gleaning, publication.
    assert "previous_extraction" in calls[1][0].parts[1].content
    with pytest.raises(ValidationError):
        GraphOptions(gleaning_passes=2)


def test_invalid_structured_output_leaves_no_partial_evidence(graph_records, monkeypatch):
    provider(monkeypatch, output={"entities": [], "edges": GRAPH["edges"]})
    with pytest.raises(ValidationError, match="distinct entities"):
        Passage.objects.extract_passage(graph_records.passages[0].pk)
    with system_context(reason="test.pgvector.invalid_graph"):
        assert not Entity.objects.exists()
        assert not Passage.objects.graph_stale().filter(pk=graph_records.passages[0].pk).exists()
        assert Passage.objects.get(pk=graph_records.passages[0].pk).index_error == "ValidationError"


def test_extraction_requires_current_deployment_approval(graph_records, monkeypatch, settings):
    calls = provider(monkeypatch)
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"knowledge_graph_extraction": []}
    with pytest.raises(PermissionDenied, match="not approved"):
        Passage.objects.extract_passage(graph_records.passages[0].pk)
    assert not calls


def test_only_provider_classified_transient_errors_get_task_retry(graph_records, monkeypatch):
    provider(monkeypatch)
    monkeypatch.setattr(InferenceModel, "infer", Mock(side_effect=TransientInferenceError("temporary")))
    with pytest.raises(TransientInferenceError):
        Passage.objects.extract_passage(graph_records.passages[0].pk)
    with system_context(reason="test.pgvector.retry_due"):
        Passage.objects.filter(pk=graph_records.passages[0].pk).update(index_retry_at=timezone.now())
    monkeypatch.setattr(InferenceModel, "infer", Mock(side_effect=ValueError("invalid schema")))
    with pytest.raises(ValueError, match="invalid schema"):
        Passage.objects.extract_passage(graph_records.passages[0].pk)


def test_reconciler_bounds_graph_work_and_orphan_collection(graph_records, monkeypatch):
    records = graph_records
    with system_context(reason="test.pgvector.graph_backfill"):
        for index in range(RECONCILE_BATCH + 3):
            Passage.objects.create(
                page=records.page,
                ordinal=index + 2,
                heading_path=[],
                start=0,
                end=1,
                text_hash=f"{index:064x}",
                source_body_hash=records.markdown.body_hash, source_title=records.markdown.title,
                source_vault_id=records.vault.pk,
            )
            Entity.objects.create(vault=records.vault, key=f"orphan {index}", kind="concept")
    send = Mock()
    monkeypatch.setattr("angee.knowledge_graph_pgvector.managers.enqueue_task", send)
    assert tasks.reconcile_index() == RECONCILE_BATCH
    assert send.call_count == RECONCILE_BATCH
    assert all(call.args == (EXTRACT_PASSAGE_TASK,) for call in send.call_args_list)
    with system_context(reason="test.pgvector.graph_gc_bound"):
        assert Entity.objects.count() == 3


def test_passage_graph_rejects_duplicate_entities_and_invalid_weights():
    assert entity_key("  ＡLPHA\tBeta  ") == "alpha beta"
    with pytest.raises(ValidationError, match="distinct normalized"):
        PassageGraph.model_validate({"entities": [GRAPH["entities"][0]] * 2, "edges": []})
    with pytest.raises(ValidationError):
        PassageGraph.model_validate({**GRAPH, "edges": [{**GRAPH["edges"][0], "strength": float("nan")}]})



def test_101_poison_passages_complete_with_error_and_do_not_starve_new_work(graph_records, monkeypatch):
    records = graph_records
    calls = provider(monkeypatch, output={"entities": [], "edges": GRAPH["edges"]})
    with system_context(reason="test.graph.poison_batch"):
        template = records.passages[0]
        Passage.objects.bulk_create([
            Passage(page=records.page, ordinal=index + 2, start=0, end=1,
                    text_hash=f"{index + 100:064x}", source_body_hash=template.source_body_hash,
                    source_title=template.source_title, source_vault_id=template.source_vault_id)
            for index in range(99)
        ])
        ids = list(Passage.objects.graph_stale().values_list("pk", flat=True))
    assert len(ids) == 101
    for pk in ids:
        with pytest.raises(ValidationError):
            Passage.objects.extract_passage(pk)
    assert len(calls) == 101
    with system_context(reason="test.graph.poison_results"):
        assert not Passage.objects.graph_stale().exists()
        assert Passage.objects.filter(index_attempts=1, index_retry_at=None).count() == 101
        fresh = Passage.objects.create(
            page=records.page, ordinal=101, start=0, end=1, text_hash="f" * 64,
            source_body_hash=template.source_body_hash, source_title=template.source_title,
            source_vault_id=template.source_vault_id,
        )
    send = Mock()
    monkeypatch.setattr("angee.knowledge_graph_pgvector.managers.enqueue_task", send)
    assert Passage.objects.reconcile() == 1
    assert send.call_args.kwargs["kwargs"] == {"pk": fresh.pk}


def test_graph_transient_failure_waits_with_doubling_backoff(graph_records, monkeypatch):
    records = graph_records
    provider(monkeypatch)
    infer = Mock(side_effect=TransientInferenceError("retry later"))
    monkeypatch.setattr(InferenceModel, "infer", infer)
    now = timezone.now()
    monkeypatch.setattr("angee.knowledge_graph_pgvector.models.timezone.now", lambda: now)
    pk = records.passages[0].pk
    with pytest.raises(TransientInferenceError):
        Passage.objects.extract_passage(pk)
    assert Passage.objects.extract_passage(pk) == 0
    with system_context(reason="test.graph.retry_clock"):
        first = Passage.objects.get(pk=pk)
        assert first.index_attempts == 1
        assert first.index_retry_at == now + timedelta(minutes=1)
        Passage.objects.filter(pk=pk).update(index_retry_at=now)
    with pytest.raises(TransientInferenceError):
        Passage.objects.extract_passage(pk)
    with system_context(reason="test.graph.second_failure"):
        second = Passage.objects.get(pk=pk)
        assert second.index_attempts == 2
        assert second.index_retry_at == now + timedelta(minutes=2)
    assert infer.call_count == 2


def test_vault_move_replaces_graph_evidence_even_when_text_is_unchanged(graph_records, monkeypatch):
    records = graph_records
    provider(monkeypatch)
    assert Passage.objects.extract_passage(records.passages[0].pk) == 1
    other = vault_for(records.owner, name="Moved graph")
    with system_context(reason="test.graph.move"):
        Vault.objects.filter(pk=other.pk).update(retrieval_class="pgvector", extraction_model=records.extraction_model)
        records.page.vault = other
        records.page.save(update_fields=["vault"])
        markdown = MarkdownPage.objects.get(pk=records.page.pk)
    rows = Passage.objects.rebuild_for(markdown)
    assert {row.pk for row in rows}.isdisjoint(row.pk for row in records.passages)
    with system_context(reason="test.graph.move_cleanup"):
        assert not EntityMention.objects.filter(passage__page=records.page).exists()
        assert not EntityEdge.objects.filter(passage__page=records.page).exists()
