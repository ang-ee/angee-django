"""Exact, permission-scoped vector/full-text retrieval and evidence graph fusion."""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any

from django.apps import apps
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection
from django.db.models import Avg, F, Q
from pgvector.django import CosineDistance
from rebac import system_context

from angee.agents.models import InferenceModelUse
from angee.base.search import SEARCH_CONFIG
from angee.knowledge.retrieval import (
    MAX_SEARCH_PAGE_SIZE,
    RetrievalBackend,
    RetrievalFailure,
    RetrievalHit,
    RetrievalResult,
)
from angee.knowledge_graph_pgvector.constants import CANDIDATE_MULTIPLIER, EMBEDDING_ROLE, QUERY_CACHE_SECONDS
from angee.knowledge_graph_pgvector.graph import local_passages

RRF_K = 60
RETRIEVAL_ARMS = ("vector", "lexical", "hybrid", "hybrid+G0", "hybrid+G1")


def reciprocal_rank_fusion(*arms: Sequence[RetrievalHit], first: int) -> list[RetrievalHit]:
    """Fuse page ranks, retaining the best-ranked passage and stable page-identity ties."""

    scores: dict[int, float] = defaultdict(float)
    evidence: dict[int, tuple[int, RetrievalHit]] = {}
    for arm in arms:
        seen = set()
        for rank, hit in enumerate(arm, 1):
            pk = hit.page.pk
            if pk in seen:
                continue
            seen.add(pk)
            scores[pk] += 1.0 / (RRF_K + rank)
            previous = evidence.get(pk)
            if previous is None or (hit.text is not None and (previous[1].text is None or rank < previous[0])):
                evidence[pk] = (rank, hit)
    ordered = sorted(scores, key=lambda pk: (-scores[pk], evidence[pk][1].page.title, evidence[pk][1].page.pk))
    return [replace(evidence[pk][1], rank=rank, score=scores[pk]) for rank, pk in enumerate(ordered[:first], 1)]


class PgvectorRetrievalBackend(RetrievalBackend):
    """Fuse top passages, visible wikilinks and opt-in local entity evidence.

    No distance is compared across models. Each model contributes a ranking;
    Full-text, structural links and entities contribute rankings through the same RRF.
    Providers fail visibly. A vault without a model still contributes full-text.
    """

    key = "pgvector"
    label = "Pgvector (hybrid)"

    @staticmethod
    def require_database() -> None:
        """Refuse unsupported databases and missing extensions before retrieval."""

        if connection.vendor != "postgresql":
            raise ImproperlyConfigured("The pgvector retrieval backend requires PostgreSQL with the vector extension.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            if not cursor.fetchone()[0]:
                raise ImproperlyConfigured(
                    "The vector extension is missing; ask an administrator to CREATE EXTENSION vector before migrate."
                )

    @classmethod
    def validate_selection(cls, vault: Any) -> None:
        try:
            cls.require_database()
        except ImproperlyConfigured as error:
            raise ValidationError({"retrieval_class": str(error)}) from error

    @staticmethod
    def query_embedding(model: Any, query: str) -> list[float]:
        """Cache a validated query vector by model and query; always re-admit egress."""

        model.require_approved(EMBEDDING_ROLE)
        model.require_capability({InferenceModelUse.EMBEDDING})
        key = f"knowledge.query_embedding:{model.pk}:{hashlib.sha256(query.encode()).hexdigest()}"
        vector = cache.get(key)
        if vector is None:
            if connection.in_atomic_block:
                raise RuntimeError("Query embedding must execute outside a database transaction.")
            vector = model.embed([query], input_type="query", role=EMBEDDING_ROLE)[0]
            cache.set(key, vector, timeout=QUERY_CACHE_SECONDS)
        return vector

    @staticmethod
    def pages(vaults: Iterable[Any]) -> Any:
        return (
            apps.get_model("knowledge", "Page")
            .objects.filter(
                vault__in=vaults,
                vault__is_trashed=False,
            )
            .untrashed()
            .scoped()
        )

    @staticmethod
    def _passages(pages: Any) -> Any:
        return (
            apps.get_model("knowledge_graph_pgvector", "Passage").objects.filter(page__in=pages)
            .indexed().current().scoped()
        )

    @staticmethod
    def _hits(passages: Iterable[Any]) -> list[RetrievalHit]:
        """Batch source reads under the caller; never join guarded objects eagerly."""

        rows = list(passages)
        ids = {row.page_id for row in rows}
        pages = apps.get_model("knowledge", "Page").objects.untrashed().filter(pk__in=ids).in_bulk()
        bodies = apps.get_model("knowledge", "MarkdownPage").objects.untrashed().filter(pk__in=ids).in_bulk()
        hits = []
        for row in rows:
            page, markdown = pages.get(row.page_id), bodies.get(row.page_id)
            if page is None or markdown is None or row.matches_source(markdown) is False:
                continue
            hits.append(row.to_hit(page, markdown, rank=len(hits) + 1))
        return hits

    @classmethod
    def lexical_arm(cls, pages: Any, query: str, *, first: int) -> list[RetrievalHit]:
        """Postgres websearch with the same simple configuration as the stored vector."""

        terms = SearchQuery(query, search_type="websearch", config=SEARCH_CONFIG)
        rows = (
            cls._passages(pages)
            .filter(search=terms)
            .annotate(
                relevance=SearchRank(F("search"), terms, cover_density=True),
            )
            .first_per_group("page_id", order_by=("-relevance", "ordinal", "pk"), limit=1)
            .order_by(
                "-relevance",
                "page__title",
                "page_id",
                "ordinal",
                "pk",
            )[:first]
        )
        return cls._hits(rows)

    @classmethod
    def vector_arm(cls, pages: Any, model_id: int, vector: list[float], *, first: int) -> list[RetrievalHit]:
        """Pre-scope before exact cosine ranking; keep each page's best passage."""

        embedding_model = apps.get_model("knowledge_graph_pgvector", "PassageEmbedding")
        rows = list(
            embedding_model.objects.filter(
                passage__in=cls._passages(pages),
                model_id=model_id,
            )
            .annotate(distance=CosineDistance("embedding", vector))
            .first_per_group(
                "passage__page_id",
                order_by=("distance", "passage__ordinal", "pk"),
                limit=1,
            )
            .order_by("distance", "passage__page__title", "passage__page_id", "pk")
            .values_list("passage_id", flat=True)[:first]
        )
        passages = cls._passages(pages).filter(pk__in=rows).in_bulk()
        return cls._hits([passages[pk] for pk in rows if pk in passages])

    @classmethod
    def link_arm(cls, pages: Any, seeds: Sequence[RetrievalHit], *, first: int) -> list[RetrievalHit]:
        """One hop only, with both endpoints scoped; outgoing links weigh twice incoming."""

        if not seeds:
            return []
        contributions = {hit.page.pk: hit.score if hit.score is not None else 1 / (RRF_K + hit.rank) for hit in seeds}
        links = (
            apps.get_model("knowledge", "Link")
            .objects.filter(
                Q(source_page_id__in=contributions) | Q(target_page_id__in=contributions),
                is_resolved=True,
                source_page__in=pages,
                target_page__in=pages,
            )
            .scoped()
            .order_by("source_page_id", "target_page_id", "pk")
        )
        scores: dict[int, float] = defaultdict(float)
        for source, target in links.values_list("source_page_id", "target_page_id"):
            if source in contributions and target not in contributions:
                scores[target] += contributions[source]
            if target in contributions and source not in contributions:
                scores[source] += contributions[target] * 0.5
        neighbours = list(pages.filter(pk__in=scores))
        neighbours.sort(key=lambda page: (-scores[page.pk], page.title, page.pk))
        neighbours = neighbours[:first]
        evidence = cls._passages(pages.filter(pk__in=[page.pk for page in neighbours])).first_per_group(
            "page_id",
            order_by=("ordinal", "pk"),
            limit=1,
        )
        hits = {hit.page.pk: hit for hit in cls._hits(evidence)}
        return [
            replace(hits.get(page.pk, RetrievalHit(page=page, rank=rank)), rank=rank, score=scores[page.pk])
            for rank, page in enumerate(neighbours, 1)
        ]

    @classmethod
    def graph_arm(cls, pages: Any, seeds: Sequence[RetrievalHit], query: str, *, first: int) -> list[RetrievalHit]:
        """Rank current evidence through the opt-in local entity graph."""

        if not pages.filter(vault__extraction_model__isnull=False).exists():
            return []
        ranked = local_passages(cls._passages(pages), seeds, query, first=first)
        scores = {row.page_id: score for row, score in ranked}
        return [replace(hit, score=scores[hit.page.pk]) for hit in cls._hits([row for row, _ in ranked])]

    @classmethod
    def rankings(cls, vaults: Iterable[Any], query: str, *, first: int = 20) -> dict[str, RetrievalResult]:
        """Production arms and cumulative graph ablations, sharing each provider call.

        ``hybrid+G1`` includes G0 and the opt-in entity arm. These are the exact
        rankings used by search and the evaluation command, not a second pipeline.
        """

        limit = max(0, min(first, MAX_SEARCH_PAGE_SIZE))
        if not limit or not query.strip():
            return {arm: RetrievalResult() for arm in RETRIEVAL_ARMS}
        cls.require_database()
        vaults = tuple(vaults)
        pages = cls.pages(vaults)
        # Candidate overfetch lets fusion recover pages outside either arm's top N.
        candidates = min(MAX_SEARCH_PAGE_SIZE, limit * CANDIDATE_MULTIPLIER)
        lexical = cls.lexical_arm(pages, query, first=candidates)
        vector_arms = []
        failures = []
        groups: dict[int, list[Any]] = defaultdict(list)
        for vault in vaults:
            if vault.embedding_model_id is not None:
                groups[vault.embedding_model_id].append(vault)
        for model_id, group in sorted(groups.items()):
            group_pages = pages.filter(vault__in=group)
            if not group_pages.exists():
                continue
            # Readers need page access, not catalogue grants. Only the approved
            # provider call is elevated; every evidence query remains actor-scoped.
            try:
                with system_context(reason="knowledge_graph_pgvector.query_embedding"):
                    model = apps.get_model("agents", "InferenceModel").objects.get(pk=model_id)
                    vector = cls.query_embedding(model, query)
                vector_arms.append(cls.vector_arm(group_pages, model_id, vector, first=candidates))
            except Exception:
                logging.getLogger(__name__).exception("Dropping failed query vector arm", extra={"model_id": model_id})
                failures.append(RetrievalFailure(
                    backend=cls.key, arm="vector", vaults=tuple(str(vault.sqid) for vault in group),
                    message="Query embedding or vector ranking failed; other retrieval arms remain available.",
                ))
        arms = [lexical, *vector_arms]
        seeds = reciprocal_rank_fusion(*arms, first=candidates)
        links = cls.link_arm(pages, seeds, first=candidates)
        graph = cls.graph_arm(pages, seeds, query, first=candidates)
        rankings = {
            "vector": reciprocal_rank_fusion(*vector_arms, first=limit),
            "lexical": lexical[:limit],
            "hybrid": seeds[:limit],
            "hybrid+G0": reciprocal_rank_fusion(*arms, links, first=limit),
            "hybrid+G1": reciprocal_rank_fusion(*arms, links, graph, first=limit),
        }
        return {
            arm: RetrievalResult(hits=hits, failures=[] if arm == "lexical" else failures)
            for arm, hits in rankings.items()
        }

    @classmethod
    def search_many(cls, vaults: Iterable[Any], query: str, *, first: int = 20) -> RetrievalResult:
        return cls.rankings(vaults, query, first=first)["hybrid+G1"]

    @classmethod
    def related_to(cls, page: Any, *, first: int = 5) -> list[RetrievalHit]:
        """Fuse current-model centroid neighbours and readable structural links."""

        limit = max(0, min(first, MAX_SEARCH_PAGE_SIZE))
        if not limit:
            return []
        page_model = apps.get_model("knowledge", "Page")
        source = page_model.objects.untrashed().filter(pk=page.pk).first()
        if source is None:
            return []
        vault = apps.get_model("knowledge", "Vault").objects.untrashed().filter(pk=source.vault_id).first()
        if vault is None or vault.retrieval_class != cls.key:
            return []
        cls.require_database()
        pages = cls.pages((vault,))
        candidates = min(MAX_SEARCH_PAGE_SIZE, limit * CANDIDATE_MULTIPLIER)
        neighbours = pages.exclude(pk=source.pk)
        vectors = []
        if vault.embedding_model_id is not None:
            embeddings = apps.get_model("knowledge_graph_pgvector", "PassageEmbedding")
            centroid = embeddings.objects.filter(
                passage__in=cls._passages(pages.filter(pk=source.pk)),
                model_id=vault.embedding_model_id,
            ).aggregate(value=Avg("embedding"))["value"]
            if centroid is not None and any(centroid):
                vectors = cls.vector_arm(
                    neighbours, vault.embedding_model_id, [float(value) for value in centroid], first=candidates,
                )
        links = cls.link_arm(pages, [RetrievalHit(page=source, rank=1)], first=candidates)
        return reciprocal_rank_fusion(vectors, links, first=limit)
