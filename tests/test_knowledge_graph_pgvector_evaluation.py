"""Golden-set metrics, actor-scoped validation and the native evaluation command."""

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from rebac import actor_context

from angee.knowledge.retrieval import MAX_SEARCH_PAGE_SIZE, RetrievalHit, RetrievalResult
from angee.knowledge_graph_pgvector.evaluation import GoldenSet, RetrievalMetrics, evaluate
from angee.knowledge_graph_pgvector.retrieval import RETRIEVAL_ARMS, PgvectorRetrievalBackend
from tests.conftest import create_user, vault_for
from tests.pgvector_fixtures import passage_records as passage_records
from tests.pgvector_graph_fixtures import graph_page
from tests.pgvector_graph_fixtures import graph_records as graph_records

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL graph retrieval")


def test_metrics_deduplicate_pages_and_separate_recall_at_ten_from_reciprocal_rank():
    def hit(pk):
        return RetrievalHit(page=SimpleNamespace(pk=pk), rank=1)

    assert RetrievalMetrics.measure([hit(8), hit(8), hit(1)], {1, 2}) == RetrievalMetrics(0.5, 0.5)
    assert RetrievalMetrics.measure([hit(index) for index in range(11)], {10}) == RetrievalMetrics(0, 1 / 11)
    assert RetrievalMetrics.measure([], {10}) == RetrievalMetrics(0, 0)


def test_tiny_golden_fixture_reports_each_arm_and_multi_hop_category(graph_records, monkeypatch):
    records = graph_records
    first, _, _ = graph_page(records, "First expected", names=["Alpha"])
    second, _, _ = graph_page(records, "Second expected", edges=[("Alpha", "Beta")])
    first_hit, second_hit = RetrievalHit(page=first, rank=1), RetrievalHit(page=second, rank=1)
    golden = GoldenSet.model_validate(
        {
            "cases": [
                {"query": "alpha", "expected_pages": [first.title]},
                {"query": "chain", "expected_pages": [first.title, second.title], "category": "multi_hop"},
            ]
        }
    )

    def rankings(cls, vaults, query, *, first):
        assert first == MAX_SEARCH_PAGE_SIZE
        if query == "alpha":
            return {
                arm: RetrievalResult(hits=([second_hit, first_hit] if arm == "vector" else [first_hit]))
                for arm in RETRIEVAL_ARMS
            }
        return {arm: RetrievalResult(hits=hits) for arm, hits in {
            "vector": [first_hit],
            "lexical": [],
            "hybrid": [first_hit],
            "hybrid+G0": [second_hit, first_hit],
            "hybrid+G1": [second_hit, first_hit],
        }.items()}

    monkeypatch.setattr(PgvectorRetrievalBackend, "rankings", classmethod(rankings))
    with actor_context(records.owner):
        result = evaluate(golden, (records.vault,))
    assert result.queries == 2
    assert result.overall == {
        "vector": RetrievalMetrics(0.75, 0.75),
        "lexical": RetrievalMetrics(0.5, 0.5),
        "hybrid": RetrievalMetrics(0.75, 1),
        "hybrid+G0": RetrievalMetrics(1, 1),
        "hybrid+G1": RetrievalMetrics(1, 1),
    }
    assert result.by_category["multi_hop"]["vector"].recall_at_10 == 0.5


@pytest.mark.parametrize(
    "extension,contents",
    [
        ("json", '{"cases": [{"query": "Orion", "expected_pages": ["Orion design"], "category": "multi_hop"}]}'),
        ("yaml", "cases:\n  - query: Orion\n    expected_pages: [Orion design]\n    category: multi_hop\n"),
    ],
)
def test_command_reads_json_or_yaml_and_reports_production_graph_rankings(
    graph_records, monkeypatch, tmp_path, extension, contents
):
    records = graph_records
    graph_page(records, "Orion design", edges=[("Orion", "Lyra")])
    graph_page(records, "Lyra sensor", edges=[("Lyra", "Atlas")])
    monkeypatch.setattr(
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend.require_database", lambda: None,
    )
    monkeypatch.setattr(PgvectorRetrievalBackend, "lexical_arm", classmethod(lambda cls, *args, **kwargs: []))
    path = tmp_path / f"golden.{extension}"
    path.write_text(contents)
    output = StringIO()
    call_command(
        "knowledge_retrieval_eval",
        str(path),
        actor=records.owner.get_username(),
        vault=[str(records.vault.sqid)],
        stdout=output,
        skip_checks=True,
    )
    result = json.loads(output.getvalue())
    assert result["queries"] == 1
    assert set(result["overall"]) == set(RETRIEVAL_ARMS)
    assert result["overall"]["hybrid"]["recall_at_10"] == 0
    assert result["overall"]["hybrid+G1"]["recall_at_10"] == 1
    assert result["by_category"]["multi_hop"]["hybrid+G1"]["mrr"] > 0


def test_golden_validation_rejects_unreadable_or_ambiguous_expectations_before_provider_calls(
    graph_records, monkeypatch, tmp_path
):
    records = graph_records
    graph_page(records, "Expected private", names=["Private"])
    golden = GoldenSet.model_validate({"cases": [{"query": "private", "expected_pages": ["Expected private"]}]})
    rankings = Mock(side_effect=AssertionError("Validate expectations before querying providers"))
    monkeypatch.setattr(PgvectorRetrievalBackend, "rankings", rankings)
    outsider = create_user("evaluation-outsider")
    with actor_context(outsider), pytest.raises(ValueError, match="readable"):
        evaluate(golden, [records.vault])
    path = tmp_path / "private.json"
    path.write_text(golden.model_dump_json())
    with pytest.raises(CommandError, match="readable"):
        call_command(
            "knowledge_retrieval_eval",
            str(path),
            actor=outsider.get_username(),
            vault=[str(records.vault.sqid)],
            skip_checks=True,
        )
    other = vault_for(records.owner, name="Duplicate golden titles")
    graph_page(records, "Expected private", names=["Private"], vault=other)
    with actor_context(records.owner), pytest.raises(ValueError, match="ambiguous"):
        evaluate(golden, [records.vault, other])
    rankings.assert_not_called()


def test_shipped_golden_set_has_factoid_and_multi_hop_examples():
    path = Path(__file__).parents[1] / "src/angee/knowledge_graph_pgvector/fixtures/golden.json"
    golden = GoldenSet.read(path)
    assert {case.category for case in golden.cases} == {"factoid", "multi_hop"}
    assert any(len(case.expected_pages) > 1 for case in golden.cases)
