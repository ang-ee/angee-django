"""RRF is deterministic; one-hop links cannot lend unreadable evidence."""

from types import SimpleNamespace

import pytest
from rebac import actor_context

from angee.knowledge.retrieval import RetrievalHit
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend, reciprocal_rank_fusion
from tests.conftest import Page, create_user
from tests.pgvector_fixtures import passage_records as passage_records
from tests.test_knowledge import _grant


def _hit(pk, title, *, text=None):
    return RetrievalHit(page=SimpleNamespace(pk=pk, title=title, sqid=f"page-{pk}"), rank=1, text=text)


def test_rrf_uses_k60_and_stable_title_and_page_id_ties():
    a, b, c = _hit(2, "Same"), _hit(1, "Same"), _hit(3, "C")
    hits = reciprocal_rank_fusion([a, b, c], [b, a], first=3)
    assert [hit.page.pk for hit in hits] == [1, 2, 3]
    assert hits[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert [hit.rank for hit in hits] == [1, 2, 3]


def test_rrf_keeps_evidence_and_deduplicates_each_arm():
    page, evidence = _hit(1, "Page"), _hit(1, "Page", text="Evidence")
    hits = reciprocal_rank_fusion([page, page], [evidence], first=1)
    assert hits[0].text == "Evidence"
    assert hits[0].score == pytest.approx(2 / 61)


def test_link_arm_scopes_both_ends_and_stops_after_one_hop(passage_records):
    records = passage_records
    reader = create_user("link-reader")
    with actor_context(records.owner):
        visible = Page.objects.create_in(records.vault, title="Visible", body="[[Guide]]")
        hidden = Page.objects.create_in(records.vault, title="Hidden", body="[[Guide]]")
        second_hop = Page.objects.create_in(records.vault, title="Two hops", body="[[Visible]]")
    _grant(records.page, "viewer", reader)
    _grant(visible, "viewer", reader)
    _grant(second_hop, "viewer", reader)
    with actor_context(reader):
        pages = PgvectorRetrievalBackend.pages((records.vault,))
        hits = PgvectorRetrievalBackend.link_arm(pages, [RetrievalHit(page=records.page, rank=1)], first=10)
        assert [hit.page.pk for hit in hits] == [visible.pk]
        assert hidden.pk not in [hit.page.pk for hit in hits]
        assert second_hop.pk not in [hit.page.pk for hit in hits]
    with actor_context(records.owner):
        visible.trash()
    with actor_context(reader):
        assert PgvectorRetrievalBackend.link_arm(pages, [RetrievalHit(page=records.page, rank=1)], first=10) == []
