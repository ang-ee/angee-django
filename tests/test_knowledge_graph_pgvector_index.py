"""Model-independent chunking and text-hash diffs preserve derived evidence."""

import pytest
from django.db import connection
from rebac import actor_context, system_context

from angee.knowledge_graph_pgvector.testing.models import Passage, PassageEmbedding
from tests.conftest import MarkdownPage, Vault
from tests.pgvector_fixtures import passage_records as passage_records


def test_outline_chunking_preserves_unicode_crlf_offsets_and_provider_budget():
    body = (
        "Intro é🙂\u2028still prose\r\n\r\n# First\r\n" + "Unicode é🙂 evidence. " * 60 + "\r\n## Child\r\nChild text."
    )
    markdown = MarkdownPage(pk=17, title="Guide", body=body)
    rows = Passage.objects.passages(markdown, max_input_tokens=160)
    assert len(rows) > 3
    assert {tuple(row.heading_path) for row in rows} == {(), ("First",), ("First", "Child")}
    for row in rows:
        assert row.text(markdown) == body[row.start : row.end]
        assert len(row.embedding_text(markdown).encode("utf-8")) <= 160


def test_empty_body_has_a_title_passage():
    markdown = MarkdownPage(pk=17, title="Empty guide", body="")
    rows = Passage.objects.passages(markdown)
    assert len(rows) == 1
    assert (rows[0].start, rows[0].end, rows[0].text(markdown)) == (0, 0, "Empty guide")


def test_source_fingerprint_distinguishes_title_and_body_boundaries():
    first = MarkdownPage(title="Title\n", body="Body")
    second = MarkdownPage(title="Title", body="\nBody")
    assert Passage.objects.fingerprint(first) != Passage.objects.fingerprint(second)


def test_duplicate_heading_paths_do_not_make_indexing_ambiguous():
    markdown = MarkdownPage(pk=17, title="Guide", body="# Repeated\nOne\n# Repeated\nTwo")
    rows = Passage.objects.passages(markdown)
    assert [row.heading_path for row in rows] == [["Repeated"], ["Repeated"]]
    assert rows[0].text(markdown) != rows[1].text(markdown)


def test_diff_reorders_survivors_and_cascades_vanished_vectors(passage_records):
    records = passage_records
    first, second = records.passages
    with system_context(reason="test.pgvector.seed_vector"):
        vector = PassageEmbedding.objects.create(passage=first, model=records.model, embedding=[1.0, 0.0])
    with actor_context(records.owner):
        current = MarkdownPage.objects.write_body(records.page, "# Second\nBeta evidence.\n\n# First\nAlpha evidence.")
    changed = Passage.objects.rebuild_for(current)
    assert [row.pk for row in changed] == [second.pk, first.pk]
    with system_context(reason="test.pgvector.assert_vector"):
        assert PassageEmbedding.objects.get(pk=vector.pk).passage_id == first.pk
    with actor_context(records.owner):
        current = MarkdownPage.objects.write_body(records.page, "# Second\nBeta evidence.")
    assert [row.pk for row in Passage.objects.rebuild_for(current)] == [second.pk]
    with system_context(reason="test.pgvector.assert_cascade"):
        assert not PassageEmbedding.objects.filter(pk=vector.pk).exists()


def test_unchanged_rebuild_ignores_new_model_chunk_budget(passage_records):
    records = passage_records
    assert [row.pk for row in Passage.objects.rebuild_for(records.markdown, max_input_tokens=40)] == [
        row.pk for row in records.passages
    ]


def test_stale_source_is_excluded_and_late_rebuild_cannot_overwrite_it(passage_records):
    records = passage_records
    with actor_context(records.owner):
        current = MarkdownPage.objects.write_body(records.page, "Changed completely")
    with system_context(reason="test.pgvector.staleness"):
        assert not Passage.objects.current().filter(page=records.page).exists()
        assert Passage.objects.stale_pages().filter(pk=records.page.pk).exists()
    assert Passage.objects.rebuild_for(records.markdown) == []
    Passage.objects.rebuild_for(current)
    with pytest.raises(ValueError, match="stale"):
        records.passages[0].text(current)


def test_missing_current_model_vectors_are_stale_without_rechunking(passage_records):
    records = passage_records
    with system_context(reason="test.pgvector.backfill"):
        assert Passage.objects.stale_pages().filter(pk=records.page.pk).exists()
        for row in records.passages:
            PassageEmbedding.objects.create(passage=row, model=records.model, embedding=[1.0, 0.0])
        assert not Passage.objects.stale_pages().filter(pk=records.page.pk).exists()
        Vault.objects.filter(pk=records.vault.pk).update(embedding_model=None)
        assert not Passage.objects.stale_pages().filter(pk=records.page.pk).exists()


def test_hash_rebuild_catches_bulk_body_writes_without_body_hash_updates(passage_records):
    with system_context(reason="test.pgvector.bulk"):
        MarkdownPage.objects.filter(pk=passage_records.page.pk).update(body="Bulk replacement")
        assert Passage.objects.stale_pages().filter(pk=passage_records.page.pk).exists()


def test_lexical_only_selection_indexes_without_an_embedding_model(passage_records, monkeypatch):
    records = passage_records
    monkeypatch.setattr("angee.knowledge_graph_pgvector.managers.require_pgvector", lambda: None)
    with system_context(reason="test.pgvector.lexical"):
        Vault.objects.filter(pk=records.vault.pk).update(embedding_model=None)
        Passage.objects.filter(page=records.page).delete()
    assert PassageEmbedding.objects.embed_page(records.page.pk) == 0
    with system_context(reason="test.pgvector.lexical.assert"):
        assert Passage.objects.filter(page=records.page).count() == 2
        if connection.vendor == "sqlite":
            assert not Passage.objects.exclude(search=None).exists()
