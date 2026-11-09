"""Passages and vectors inherit page access; model selection admits the writer."""

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import connection
from rebac import actor_context, system_context

from angee.agents.models import InferenceModelUse
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend
from angee.knowledge_graph_pgvector.testing.models import Passage, PassageEmbedding
from tests.conftest import Page, Vault, create_platform_admin, create_user
from tests.pgvector_fixtures import passage_records as passage_records


def test_passage_and_embedding_read_follow_page_for_each_actor(passage_records):
    records = passage_records
    reader, outsider = create_user("passage-reader"), create_user("passage-outsider")
    admin = create_platform_admin("passage-admin")
    with actor_context(records.owner):
        records.vault.grant_record_access("viewer", reader)
    with system_context(reason="test.pgvector.permissions"):
        embedding = PassageEmbedding.objects.create(passage=records.passages[0], model=records.model, embedding=[1, 0])
    for actor, readable, writable in (
        (records.owner, True, True),
        (reader, True, False),
        (outsider, False, False),
        (admin, True, True),
    ):
        with actor_context(actor):
            assert Passage.objects.filter(page=records.page).exists() is readable
            assert PassageEmbedding.objects.filter(pk=embedding.pk).exists() is readable
            assert records.passages[0].with_actor(actor).has_access("read") is readable
            assert embedding.with_actor(actor).has_access("read") is readable
            assert records.passages[0].with_actor(actor).has_access("write") is writable
            assert embedding.with_actor(actor).has_access("write") is writable
    with actor_context(outsider), pytest.raises(PermissionDenied):
        records.passages[0].with_actor(outsider).delete()


def test_trashed_evidence_disappears_for_readers_and_restores_without_reembedding(passage_records):
    records = passage_records
    reader = create_user("trash-passage-reader")
    with actor_context(records.owner):
        records.vault.grant_record_access("viewer", reader)
        records.page.trash()
    with actor_context(reader):
        assert not Passage.objects.filter(page=records.page).exists()
    with system_context(reason="test.pgvector.retained"):
        assert Passage.objects.filter(page=records.page).count() == 2
    with actor_context(records.owner):
        records.page.restore()
    with actor_context(reader):
        assert Passage.objects.filter(page=records.page).count() == 2


def test_vault_selection_checks_writer_model_access_and_capability(passage_records, monkeypatch):
    records = passage_records
    monkeypatch.setattr(
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend.require_database", lambda: None,
    )
    with system_context(reason="test.selection_change"):
        Vault.objects.filter(pk=records.vault.pk).update(embedding_model=None)
    with actor_context(records.owner), pytest.raises(PermissionDenied, match="cannot read"):
        records.vault.clean()
    with actor_context(records.model.provider.owner):
        records.vault.clean()
    with system_context(reason="test.pgvector.wrong_use"):
        type(records.model).objects.filter(pk=records.model.pk).update(model_use=InferenceModelUse.CHAT)
    records.model.refresh_from_db()
    with actor_context(records.model.provider.owner), pytest.raises(ValueError, match="uses"):
        records.vault.clean()


def test_sqlite_refuses_pgvector_selection_and_dispatch(passage_records, monkeypatch):
    monkeypatch.setattr(connection, "vendor", "sqlite")
    with pytest.raises(ValidationError, match="PostgreSQL"):
        passage_records.vault.clean()
    with pytest.raises(ImproperlyConfigured, match="PostgreSQL"):
        PgvectorRetrievalBackend.search_many([passage_records.vault], "query")


def test_page_deletion_cascades_index_rows(passage_records):
    with system_context(reason="test.pgvector.page_delete"):
        Page.objects.filter(pk=passage_records.page.pk).delete()
        assert not Passage.objects.filter(page_id=passage_records.page.pk).exists()
