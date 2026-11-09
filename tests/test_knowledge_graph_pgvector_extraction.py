"""Idle extraction, structured output, incremental evidence and fenced publication."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connection
from django.utils import timezone
from pydantic import ValidationError
from rebac import PermissionDenied, actor_context, system_context

from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector import tasks
from angee.knowledge_graph_pgvector.constants import EXTRACT_PASSAGE_TASK, RECONCILE_BATCH
from angee.knowledge_graph_pgvector.extraction import PassageGraph, TransientGraphError, entity_key
from angee.knowledge_graph_pgvector.graph_config import GraphOptions
from angee.knowledge_graph_pgvector.testing.models import Entity, EntityEdge, EntityMention, Passage
from tests.conftest import MarkdownPage, Page, Vault
from tests.pgvector_fixtures import passage_records as passage_records
from tests.pgvector_graph_fixtures import graph_records as graph_records

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
    monkeypatch.setattr("angee.knowledge_graph_pgvector.managers.require_pgvector", lambda: None)

    def infer(self, messages, *, output_schema):
        assert not connection.in_atomic_block
        assert output_schema["title"] == "PassageGraph"
        assert "enum" in output_schema["$defs"]["GraphEntity"]["properties"]["kind"]
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
        assert Passage.objects.graph_stale().filter(pk=graph_records.passages[0].pk).exists()


def test_extraction_requires_current_deployment_approval(graph_records, monkeypatch, settings):
    calls = provider(monkeypatch)
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"knowledge_graph_extraction": []}
    with pytest.raises(PermissionDenied, match="not approved"):
        Passage.objects.extract_passage(graph_records.passages[0].pk)
    assert not calls


def test_only_provider_classified_transient_errors_get_task_retry(graph_records, monkeypatch):
    provider(monkeypatch)
    monkeypatch.setattr(InferenceModel, "request_error_types", lambda self: (TimeoutError,))
    monkeypatch.setattr(InferenceModel, "is_transient_error", lambda self, error: isinstance(error, TimeoutError))
    monkeypatch.setattr(InferenceModel, "infer", Mock(side_effect=TimeoutError("temporary")))
    with pytest.raises(TransientGraphError):
        Passage.objects.extract_passage(graph_records.passages[0].pk)
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
                source_hash=records.passages[0].source_hash,
            )
            Entity.objects.create(vault=records.vault, name=f"Orphan {index}", key=f"orphan {index}", kind="concept")
    send = Mock()
    monkeypatch.setattr("angee.knowledge_graph_pgvector.managers.enqueue_task", send)
    assert tasks.reconcile_embeddings() == RECONCILE_BATCH
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
