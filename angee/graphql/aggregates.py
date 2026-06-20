"""REBAC-aware aggregate seam built on strawberry-django-aggregates."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac.field_visibility import gated_read_fields
from strawberry_django_aggregates import AggregateBuilder

from angee.base.models import AngeeQuerySet, public_id_for
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME


class AngeeAggregateBuilder(AggregateBuilder):
    """Aggregate builder that emits Angee public-id relation drill-down filters."""

    def _echo_axis_filter(
        self,
        fp: str,
        grain: Any,
        key_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a bucket filter using the public-id field for direct FK axes."""

        if self.json_paths and fp in self.json_paths:
            return super()._echo_axis_filter(fp, grain, key_kwargs)
        if "__" in fp:
            return super()._echo_axis_filter(fp, grain, key_kwargs)
        field = self.model._meta.get_field(fp)
        if not getattr(field, "many_to_one", False):
            return super()._echo_axis_filter(fp, grain, key_kwargs)

        from strawberry_django_aggregates.compiler import group_by_alias
        from strawberry_django_aggregates.granularity import NumberGranularity, TimeGranularity

        if isinstance(grain, NumberGranularity | TimeGranularity):
            return super()._echo_axis_filter(fp, grain, key_kwargs)
        alias = group_by_alias(fp, grain, field)
        value = key_kwargs.get(alias)
        if value is None:
            return super()._echo_axis_filter(fp, grain, key_kwargs)
        related_model = field.remote_field.model
        return self._echo_field_filter(fp, {PUBLIC_ID_FIELD_NAME: public_id_for(related_model, value)})


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
    ``get_queryset`` hook re-derives per-actor row scope on each call via
    ``AngeeQuerySet.scoped_for_aggregate`` (``model`` must be a REBAC/Angee model).
    """

    gated_axes = sorted(gated_read_fields(model) & set(group_by_fields))
    if gated_axes:
        raise ImproperlyConfigured(
            f"{model._meta.label}: aggregate group_by axes {gated_axes} are field-gated "
            f"reads; exposing them as bucket keys would leak gated values"
        )
    source = cast(AngeeQuerySet[Any], model._default_manager.all() if queryset is None else queryset)

    def get_queryset(info: Any) -> models.QuerySet[Any]:
        del info
        return source.all().scoped_for_aggregate()

    return AngeeAggregateBuilder(
        model=model,
        group_by_fields=list(group_by_fields),
        get_queryset=get_queryset,
        **kwargs,
    )
