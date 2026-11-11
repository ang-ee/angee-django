"""Local graph ranking uses only readable current evidence and bounded hops."""

from unittest.mock import Mock

import networkx as nx
import pytest
from django.db import connection
from rebac import actor_context, system_context

from angee.agents.testing.models import InferenceModel
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend
from angee.knowledge_graph_pgvector.testing.models import EntityEdge, EntityMention
from tests.conftest import MarkdownPage, Vault, create_user, vault_for
from tests.pgvector_fixtures import passage_records as passage_records
from tests.pgvector_graph_fixtures import graph_page
from tests.pgvector_graph_fixtures import graph_records as graph_records
from tests.test_knowledge import _grant

pytestmark = [
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL phrase search"),
    pytest.mark.django_db(transaction=True),
]


def test_local_mode_reaches_two_hops_without_query_time_inference(graph_records, monkeypatch, settings):
    records = graph_records
    seed, passage, _ = graph_page(records, "Seed", names=["Orion"])
    first, _, _ = graph_page(records, "First hop", edges=[("Orion", "Lyra")])
    second, _, _ = graph_page(records, "Second hop", edges=[("Lyra", "Atlas")])
    third, third_passage, third_entities = graph_page(records, "Third hop", edges=[("Atlas", "Quito")])
    infer = Mock(side_effect=AssertionError("Local queries do not call inference"))
    monkeypatch.setattr(InferenceModel, "infer", infer)
    captured = []
    pagerank = nx.pagerank

    def rank(graph, **kwargs):
        captured.append(graph.copy())
        return pagerank(graph, **kwargs)

    monkeypatch.setattr("angee.knowledge_graph_pgvector.graph.nx.pagerank", rank)
    settings.ANGEE_KNOWLEDGE_GRAPH = {"hops": 2}
    with actor_context(records.owner):
        pages = PgvectorRetrievalBackend.pages((records.vault,))
        hit = passage.to_hit(seed, MarkdownPage.objects.get(pk=seed.pk), rank=1)
        result = PgvectorRetrievalBackend.graph_arm(pages, [hit], "Orion", first=10)
        assert {first.pk, second.pk} <= {row.page.pk for row in result}
        assert len(captured[-1]) == 3  # Orion, Lyra, Atlas; no third-hop Quito.
        assert all(row.text is not None for row in result)
        assert [row.page.pk for row in result] == [
            row.page.pk for row in PgvectorRetrievalBackend.graph_arm(pages, [hit], "Orion", first=10)
        ]
    # Third-hop page may be evidence for reached Atlas, but its edge is never traversed.
    assert third_entities["Quito"].pk not in captured[-1]
    with system_context(reason="test.graph.hop_evidence"):
        third_edge = EntityEdge.objects.get(passage=third_passage)
    assert third_edge.pk not in {key for _, _, key in captured[-1].edges(keys=True)}
    assert third.pk in {row.page.pk for row in result}  # A mention of reached Atlas is still evidence.
    infer.assert_not_called()


def test_name_seeds_and_each_hop_exclude_unreadable_trashed_or_stale_evidence(graph_records):
    records = graph_records
    reader = create_user("graph-local-reader")
    private = vault_for(records.owner, name="Private graph")
    visible, _, _ = graph_page(records, "Visible local", edges=[("Orion", "Lyra")])
    hidden, _, _ = graph_page(records, "Secret local", edges=[("Lyra", "Secret")], vault=private)
    trash, _, _ = graph_page(records, "Trashed local", edges=[("Lyra", "Trash")])
    stale, _, _ = graph_page(records, "Stale local", edges=[("Lyra", "Old")])
    with actor_context(records.owner):
        records.vault.grant_record_access("viewer", reader)
        trash.trash()
    with system_context(reason="test.pgvector.raw_graph_edit"):
        Vault.objects.filter(pk=private.pk).update(
            retrieval_class="pgvector", extraction_model=records.extraction_model
        )
        MarkdownPage.objects.filter(pk=stale.pk).update(body="New source without its old claims")
    with actor_context(reader):
        pages = PgvectorRetrievalBackend.pages((records.vault, private))
        hits = PgvectorRetrievalBackend.graph_arm(pages, [], "Orion", first=20)
        assert [hit.page.pk for hit in hits] == [visible.pk]
        assert {hidden.pk, trash.pk, stale.pk}.isdisjoint(hit.page.pk for hit in hits)
        assert PgvectorRetrievalBackend.graph_arm(pages, [], "Secret", first=20) == []


def test_disabled_or_changed_extraction_selection_withholds_local_arm(graph_records):
    records = graph_records
    graph_page(records, "Selected graph", edges=[("Orion", "Lyra")])
    with system_context(reason="test.pgvector.graph_disable"):
        Vault.objects.filter(pk=records.vault.pk).update(extraction_model=None)
    with actor_context(records.owner):
        assert (
            PgvectorRetrievalBackend.graph_arm(PgvectorRetrievalBackend.pages((records.vault,)), [], "Orion", first=10)
            == []
        )
    with system_context(reason="test.pgvector.graph_changed_vocabulary"):
        Vault.objects.filter(pk=records.vault.pk).update(
            extraction_model=records.extraction_model, graph_entity_kinds=["concept"]
        )
    with actor_context(records.owner):
        assert (
            PgvectorRetrievalBackend.graph_arm(PgvectorRetrievalBackend.pages((records.vault,)), [], "Orion", first=10)
            == []
        )


def test_degree_and_node_budgets_bound_high_degree_hops(graph_records, monkeypatch, settings):
    records = graph_records
    for index in range(7):
        graph_page(records, f"Neighbour {index}", edges=[("Orion", f"Target {index}")])
    settings.ANGEE_KNOWLEDGE_GRAPH = {"hops": 1, "degree_cap": 2, "seed_limit": 1, "node_limit": 50}
    pagerank = nx.pagerank
    sizes = []

    def rank(graph, **kwargs):
        sizes.append((len(graph), graph.number_of_edges()))
        return pagerank(graph, **kwargs)

    monkeypatch.setattr("angee.knowledge_graph_pgvector.graph.nx.pagerank", rank)
    with actor_context(records.owner):
        PgvectorRetrievalBackend.graph_arm(PgvectorRetrievalBackend.pages((records.vault,)), [], "Orion", first=10)
    assert sizes == [(3, 2)]


def test_search_fuses_graph_arm_and_retains_its_passage_evidence(graph_records, monkeypatch):
    records = graph_records
    seed, passage, _ = graph_page(records, "Seed evidence", names=["Orion"])
    neighbour, _, _ = graph_page(records, "Graph discovery", edges=[("Orion", "Lyra")])
    monkeypatch.setattr(
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend.require_database", lambda: None,
    )
    with actor_context(records.owner):
        hit = passage.to_hit(seed, MarkdownPage.objects.get(pk=seed.pk), rank=1)
        monkeypatch.setattr(PgvectorRetrievalBackend, "lexical_arm", classmethod(lambda cls, *args, **kwargs: [hit]))
        rankings = PgvectorRetrievalBackend.rankings((records.vault,), "Orion", first=10)
        assert [row.page.pk for row in rankings["hybrid"].hits] == [seed.pk]
        assert neighbour.pk in {row.page.pk for row in rankings["hybrid+G1"].hits}
        assert PgvectorRetrievalBackend.search_many((records.vault,), "Orion", first=10) == rankings["hybrid+G1"]
        assert all(row.text for row in rankings["hybrid+G1"].hits)



def test_name_seeds_match_words_and_only_readable_mention_spelling(graph_records):
    records = graph_records
    graph_page(records, "A person", names=["Arthur"])
    with actor_context(records.owner):
        pages = PgvectorRetrievalBackend.pages((records.vault,))
        assert PgvectorRetrievalBackend.graph_arm(pages, [], "art", first=10) == []
        assert PgvectorRetrievalBackend.graph_arm(pages, [], "Who is Arthur?", first=10)


def test_fuzzy_identity_never_projects_a_private_mention_name(graph_records):
    records = graph_records
    private, _, entities = graph_page(records, "Private spelling", names=["Secret Corporation"])
    visible, passage, _ = graph_page(records, "Public spelling", names=[])
    reader = create_user("mention-name-reader")
    _grant(visible, "viewer", reader)
    with system_context(reason="test.graph.shared_identity"):
        EntityMention.objects.create(passage=passage, entity=entities["Secret Corporation"], name="Public Company")
    with actor_context(reader):
        mentions = EntityMention.objects.all()
        assert list(mentions.values_list("name", flat=True)) == ["Public Company"]
        assert not mentions.filter(passage__page=private).exists()
