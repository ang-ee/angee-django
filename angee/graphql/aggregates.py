"""REBAC-aware aggregate seam built on strawberry-django-aggregates."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac.field_visibility import gated_read_fields
from strawberry_django_aggregates import AggregateBuilder

from angee.base.models import AngeeQuerySet


def rebac_aggregate_builder(
    *,
    model: type[models.Model],
    group_by_fields: Sequence[str] = (),
    queryset: AngeeQuerySet[Any] | None = None,
    **kwargs: Any,
) -> AggregateBuilder:
    """Return an ``AggregateBuilder`` wired for REBAC row scope and safe axes.

    Aggregate compilers emit ``.values()``/``.aggregate()`` shapes whose dict
    rows field-read redaction cannot touch, so a field-gated read column used as
    a ``group_by`` axis would leak owner-only values through bucket keys. Gated
    axes are rejected here at construction time, and the builder's
    ``get_queryset`` hook is the actor-scoped, fail-closed one below.
    """

    gated_axes = sorted(gated_read_fields(model) & set(group_by_fields))
    if gated_axes:
        raise ImproperlyConfigured(
            f"{model._meta.label}: aggregate group_by axes {gated_axes} are field-gated "
            f"reads; exposing them as bucket keys would leak gated values"
        )
    source = cast(
        AngeeQuerySet[Any],
        model._default_manager.all() if queryset is None else queryset,
    )
    return AggregateBuilder(
        model=model,
        group_by_fields=list(group_by_fields),
        get_queryset=rebac_aggregate_get_queryset(source),
        **kwargs,
    )


def rebac_aggregate_get_queryset(
    queryset: AngeeQuerySet[Any],
) -> Callable[[Any], models.QuerySet[Any]]:
    """Return an ``AggregateBuilder.get_queryset`` hook for ``queryset``.

    The hook re-derives row scope per call so each aggregate request scopes to
    the current actor; see ``AngeeQuerySet.scoped_for_aggregate``.
    """

    def get_queryset(info: Any) -> models.QuerySet[Any]:
        del info
        return queryset.all().scoped_for_aggregate()

    return get_queryset
