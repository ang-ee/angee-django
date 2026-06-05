"""REBAC adapters for permission-naive aggregate builders."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from django.db.models import QuerySet
from rebac.errors import MissingActorError


def rebac_aggregate_get_queryset(
    queryset: QuerySet[Any],
) -> Callable[[Any], QuerySet[Any]]:
    """Return an ``AggregateBuilder.get_queryset`` hook for ``queryset``."""

    def get_queryset(info: Any) -> QuerySet[Any]:
        del info
        return rebac_aggregate_queryset(queryset.all())

    return get_queryset


def rebac_aggregate_queryset(queryset: QuerySet[Any]) -> QuerySet[Any]:
    """Return ``queryset`` prepared for permission-naive aggregate compilers.

    ``strawberry-django-aggregates`` compiles through ``.aggregate()`` and
    ``.values().annotate()`` shapes, so row scope must be applied before the
    compiler runs. Those shapes return dict rows, not model instances, so field
    read redaction cannot run there; aggregate surfaces must expose only
    non-gated group and measure fields.
    """

    on_field_deny = getattr(queryset, "on_field_deny", None)
    if callable(on_field_deny):
        queryset = cast(QuerySet[Any], on_field_deny("allow"))

    apply_ambient_scope = getattr(queryset, "apply_ambient_scope", None)
    if not callable(apply_ambient_scope):
        return queryset

    try:
        return cast(QuerySet[Any], apply_ambient_scope())
    except MissingActorError:
        return cast(QuerySet[Any], queryset.none())
