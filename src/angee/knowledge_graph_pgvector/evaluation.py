"""Small actor-scoped retrieval ablations over an authored YAML or JSON golden set."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from angee.knowledge.retrieval import MAX_SEARCH_PAGE_SIZE, RetrievalHit
from angee.knowledge_graph_pgvector.retrieval import RETRIEVAL_ARMS, PgvectorRetrievalBackend

Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Nonempty
    expected_pages: list[Nonempty] = Field(
        min_length=1, description="Exact page titles, unique across the selected vaults."
    )
    category: Literal["factoid", "multi_hop"] = "factoid"


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[GoldenCase] = Field(min_length=1)

    @classmethod
    def read(cls, path: Path) -> GoldenSet:
        """Safe YAML also reads JSON; schema errors are reported before paid calls."""

        return cls.model_validate(yaml.safe_load(path.read_text()))


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_10: float
    mrr: float

    @classmethod
    def measure(cls, hits: Sequence[RetrievalHit], expected: set[int]) -> RetrievalMetrics:
        """Recall at ten and reciprocal rank within the bounded returned ranking."""

        if not expected:
            raise ValueError("A golden query requires expected pages.")
        ranked = list(dict.fromkeys(hit.page.pk for hit in hits))[:MAX_SEARCH_PAGE_SIZE]
        relevant = [rank for rank, pk in enumerate(ranked, 1) if pk in expected]
        return cls(len(set(ranked[:10]) & expected) / len(expected), 1 / relevant[0] if relevant else 0)


@dataclass(frozen=True)
class EvaluationResult:
    queries: int
    overall: dict[str, RetrievalMetrics]
    by_category: dict[str, dict[str, RetrievalMetrics]]


def evaluate(golden: GoldenSet, vaults: Iterable[Any]) -> EvaluationResult:
    """Validate every expected page under the caller, then measure production arms."""

    vaults = tuple(vaults)
    titles = {title for case in golden.cases for title in case.expected_pages}
    pages = PgvectorRetrievalBackend._pages(vaults).filter(title__in=titles)
    by_title: dict[str, int] = {}
    for page in pages:
        if page.title in by_title:
            raise ValueError(f"Golden page title is ambiguous in the selected vaults: {page.title}")
        by_title[page.title] = page.pk
    missing = sorted(titles - by_title.keys())
    if missing:
        raise ValueError(f"Golden pages must be readable and untrashed: {', '.join(missing)}")
    measured = []
    for case in golden.cases:
        expected = {by_title[title] for title in case.expected_pages}
        rankings = PgvectorRetrievalBackend.rankings(vaults, case.query, first=MAX_SEARCH_PAGE_SIZE)
        measured.append(
            (case.category, {arm: RetrievalMetrics.measure(rankings[arm], expected) for arm in RETRIEVAL_ARMS})
        )

    def aggregate(rows: list[dict[str, RetrievalMetrics]]) -> dict[str, RetrievalMetrics]:
        return {
            arm: RetrievalMetrics(mean(row[arm].recall_at_10 for row in rows), mean(row[arm].mrr for row in rows))
            for arm in RETRIEVAL_ARMS
        }

    return EvaluationResult(
        queries=len(measured),
        overall=aggregate([metrics for _, metrics in measured]),
        by_category={
            category: aggregate([metrics for label, metrics in measured if label == category])
            for category in sorted({category for category, _ in measured})
        },
    )
