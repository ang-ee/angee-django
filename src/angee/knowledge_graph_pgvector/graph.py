"""Bounded local graph ranking over the caller's current, readable evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import networkx as nx
from django.apps import apps
from django.contrib.postgres.search import SearchQuery, SearchVector
from django.db.models import Case, Exists, F, OuterRef, Q, Value, When

from angee.base.search import SEARCH_CONFIG
from angee.knowledge.retrieval import RetrievalHit
from angee.knowledge_graph_pgvector.options import GraphOptions


def local_passages(
    passages: Any,
    seeds: Sequence[RetrievalHit],
    query: str,
    *,
    first: int,
) -> list[tuple[Any, float]]:
    """Personalized PageRank on at most two evidence-scoped, degree-capped hops.

    Undirected traversal lets either endpoint seed a relationship. Each supporting
    passage remains a distinct MultiGraph edge; NetworkX sums their weights at
    query time. Names, frontiers, edges and returned evidence all retain actor
    scope. No provider is involved in a graph query.
    """

    if first <= 0:
        return []
    options = GraphOptions.from_settings()
    passages = passages.graph_current().scoped()
    mentions = apps.get_model("knowledge_graph_pgvector", "EntityMention").objects.filter(passage__in=passages).scoped()
    entities = (
        apps.get_model("knowledge_graph_pgvector", "Entity")
        .objects.filter(
            Exists(mentions.filter(entity_id=OuterRef("pk"))),
        )
        .scoped()
    )
    scores: dict[int, float] = defaultdict(float)
    seed_passages = Q(pk__in=[])
    contributions = {}
    for hit in seeds[: options.seed_limit]:
        if hit.start is None or hit.end is None:
            continue
        seed_passages |= Q(page_id=hit.page.pk, start=hit.start, end=hit.end)
        contributions[hit.page.pk] = 1 / hit.rank
    for entity_id, page_id in mentions.filter(passage__in=passages.filter(seed_passages)).values_list(
        "entity_id",
        "passage__page_id",
    ):
        scores[entity_id] += contributions[page_id]
    if query.strip():
        # Phrase tsqueries enforce token boundaries in either direction. Surface
        # names come only from this reader's current mentions, never Entity keys
        # inherited from another passage's spelling during fuzzy resolution.
        names = (
            mentions.annotate(query_search=SearchVector(Value(query), config=SEARCH_CONFIG),
                              name_search=SearchVector("name", config=SEARCH_CONFIG))
            .filter(Q(query_search=SearchQuery(F("name"), search_type="phrase", config=SEARCH_CONFIG))
                    | Q(name_search=SearchQuery(query, search_type="phrase", config=SEARCH_CONFIG)))
            .order_by("entity_id").values_list("entity_id", flat=True).distinct()[:options.seed_limit]
        )
        for pk in names:
            scores[pk] += 1
    identities = entities.filter(pk__in=scores).only("pk", "key", "kind").in_bulk()
    selected = sorted(identities, key=lambda pk: (-scores[pk], identities[pk].key, identities[pk].kind, pk))[
        : options.seed_limit
    ]
    if not selected:
        return []
    personalization = {pk: scores[pk] for pk in selected}
    graph = nx.MultiGraph()
    graph.add_nodes_from(selected)
    frontier, visited = set(selected), set(selected)
    edges = (
        apps.get_model("knowledge_graph_pgvector", "EntityEdge")
        .objects.filter(
            passage__in=passages,
            subject__in=entities,
            object__in=entities,
        )
        .scoped()
    )
    evidence_edges: dict[int, Any] = {}
    for _ in range(options.hops):
        if not frontier:
            break
        # One scoped query per hop. The window bounds evidence rows per frontier
        # entity before materialization; the total node cap also bounds fan-out.
        candidates = (
            edges.filter(Q(subject_id__in=frontier) | Q(object_id__in=frontier))
            .exclude(pk__in=evidence_edges)
            .only("pk", "passage_id", "subject_id", "object_id", "weight")
            .annotate(
                frontier_id=Case(When(subject_id__in=frontier, then=F("subject_id")), default=F("object_id")),
            )
            .first_per_group(
                "frontier_id",
                order_by=("-weight", "subject__key", "object__key", "pk"),
                limit=options.degree_cap,
            )
            .order_by("-weight", "subject__key", "object__key", "pk")
        )
        next_frontier = set()
        degrees: dict[int, int] = defaultdict(int)
        for edge in candidates:
            endpoints = {edge.subject_id, edge.object_id}
            touching = endpoints & frontier
            if any(degrees[pk] >= options.degree_cap for pk in touching):
                continue
            added = endpoints - set(graph)
            if len(graph) + len(added) > options.node_limit:
                continue
            for pk in touching:
                degrees[pk] += 1
            graph.add_edge(edge.subject_id, edge.object_id, key=edge.pk, weight=edge.weight)
            evidence_edges[edge.pk] = edge
            next_frontier.update(endpoints - visited)
        visited.update(next_frontier)
        frontier = next_frontier
    ranks = nx.pagerank(graph, personalization=personalization, alpha=0.85, max_iter=200, tol=1e-8)
    passage_scores: dict[int, float] = defaultdict(float)
    evidence = (
        mentions.filter(entity_id__in=graph)
        .first_per_group(
            "entity_id",
            order_by=("passage__page__title", "passage__page_id", "passage__ordinal", "pk"),
            limit=options.degree_cap,
        )
        .order_by("entity_id", "pk")
    )
    for passage_id, entity_id in evidence.values_list("passage_id", "entity_id"):
        passage_scores[passage_id] += ranks[entity_id]
    for edge in evidence_edges.values():
        passage_scores[edge.passage_id] += edge.weight * (ranks[edge.subject_id] + ranks[edge.object_id]) / 2
    rows = list(passages.filter(pk__in=passage_scores).order_by("page__title", "page_id", "ordinal", "pk"))
    # Stable sort keeps the public page/ordinal order for equal PageRank scores.
    rows.sort(key=lambda row: -passage_scores[row.pk])
    seen = set()
    result = []
    for row in rows:
        if row.page_id in seen:
            continue
        seen.add(row.page_id)
        result.append((row, passage_scores[row.pk]))
        if len(result) == first:
            break
    return result
