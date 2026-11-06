"""Count a record's related edges, each through the read that lists its rows.

A related group lists rows that link one record to others (its files, pages,
source conversations, the runs that worked on it). The addon owning that group
declares its rows as a :class:`RecordEdge` and appends a provider returning its
edges to ``ANGEE_GRAPHQL_RECORD_EDGES`` from its autoconfig. The
``record_edge_counts`` root resolves the record under the request actor and
counts every requested edge in one statement, so a record's badge needs no rows.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import strawberry
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.signals import setting_changed
from django.db import models
from django.dispatch import receiver
from rebac.resources import model_resource_type

from angee.base.hooks import resolve_hooks
from angee.graphql.data.hasura import aggregate_queryset
from angee.graphql.ids import PublicID, instance_for_id

HOOK_SETTING = "ANGEE_GRAPHQL_RECORD_EDGES"


@dataclass(frozen=True)
class RecordEdge:
    """The rows one related group lists on a record, read through their owner.

    ``id`` names the edge in ``record_edge_counts`` and is the related group's
    id. ``rows`` returns, for one readable record, the same actor-scoped
    queryset the group's row list reads, so the count and the rows authorize
    alike. ``target`` limits the edge to records of one model label (a
    ``<model>#groups`` group); ``None`` applies it to every record.
    """

    id: str
    rows: Callable[[Any], models.QuerySet[Any]]
    target: str | None = None

    def applies_to(self, record: models.Model) -> bool:
        """Whether ``record`` is a record this edge's rows can point at."""

        return self.target is None or isinstance(record, apps.get_model(self.target))


@lru_cache(maxsize=1)
def record_edges() -> dict[str, RecordEdge]:
    """Resolve every contributed edge by id; each hook returns an iterable of them."""

    edges: dict[str, RecordEdge] = {}
    for hook in resolve_hooks(HOOK_SETTING):
        for edge in hook():
            if not isinstance(edge, RecordEdge):
                raise ImproperlyConfigured(f"{HOOK_SETTING}: {edge!r} is not a RecordEdge.")
            if edge.id in edges:
                raise ImproperlyConfigured(f"{HOOK_SETTING}: two edges claim the id {edge.id!r}.")
            edges[edge.id] = edge
    return edges


@receiver(setting_changed)
def _forget_edges(*, setting: str, **_: Any) -> None:
    if setting == HOOK_SETTING:
        record_edges.cache_clear()


def count_record_edges(record: models.Model, edges: Sequence[RecordEdge]) -> dict[str, int]:
    """Count each edge's rows on ``record`` in one statement over the record's own row.

    Every count is a scalar subquery of the edge's read under REBAC's eager
    aggregate scope, the row authorization the aggregate roots use. An edge
    whose read evaluates queries while being built (a run's ancestors) runs
    those first. An edge that does not apply to the record counts zero.
    """

    counts = dict.fromkeys((edge.id for edge in edges), 0)
    applicable = [edge for edge in edges if edge.applies_to(record)]
    if not applicable:
        return counts
    expressions = {f"edge_{index}": _count(edge.rows(record)) for index, edge in enumerate(applicable)}
    # The record was just read under the actor; its row only hosts the subqueries.
    host = models.QuerySet(model=type(record), using=record._state.db).filter(pk=record.pk)
    row = host.values(**expressions).get()
    counts.update({edge.id: int(row[f"edge_{index}"] or 0) for index, edge in enumerate(applicable)})
    return counts


def _count(rows: models.QuerySet[Any]) -> models.Subquery:
    total = models.Func(
        models.F("pk"), function="COUNT", template="%(function)s(DISTINCT %(expressions)s)",
        output_field=models.IntegerField(),
    )
    scoped = aggregate_queryset(rows).order_by()
    return models.Subquery(scoped.annotate(_edge_count=total).values("_edge_count"), output_field=models.IntegerField())


@strawberry.type(name="RecordEdgeCount")
class RecordEdgeCountType:
    """One requested edge's readable row count on a record."""

    edge: str
    count: int


@strawberry.type
class RecordEdgeQuery:
    """Related-edge counts shared by every record view."""

    @strawberry.field(name="record_edge_counts")
    def record_edge_counts(self, model: str, id: PublicID, edges: list[str]) -> list[RecordEdgeCountType]:
        """Count each named edge's rows on one readable record, through each edge's own read.

        An unknown edge or model is a validation refusal. A hidden, absent or
        malformed record, or a model without a REBAC type, has no counts.
        """

        declared = record_edges()
        if unknown := sorted(set(edges) - declared.keys()):
            raise ValidationError({"edges": f"Unknown record edges: {', '.join(unknown)}."})
        try:
            record_model = apps.get_model(model)
        except (LookupError, ValueError) as error:
            raise ValidationError({"model": f"Unknown model {model!r}."}) from error
        if model_resource_type(record_model) is None:
            return []
        record = instance_for_id(record_model, id)
        if record is None:
            return []
        requested = [declared[name] for name in dict.fromkeys(edges)]
        counts = count_record_edges(record, requested)
        return [RecordEdgeCountType(edge=edge.id, count=counts[edge.id]) for edge in requested]
