"""REBAC-aware aggregate seam built on strawberry-django-aggregates."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from django.db import models
from strawberry_django_aggregates import AggregateBuilder

from angee.base.models import AngeeQuerySet, public_id_for
from angee.graphql.access import assert_no_gated_read_fields
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

    def _echo_bucket_filter(
        self,
        key_kwargs: dict[str, Any],
        spec: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Drop label-only relation-leaf axes before echoing the bucket filter.

        A relation-leaf axis (e.g. ``party__display_name``) is carried only to
        label the bucket with the related record's name — Odoo's
        ``(id, display_name)`` pair. When the same relation is also grouped by its
        id (``party``), that FK axis owns the drill-down filter and the label axis
        contributes no clause, so it is dropped here: echoing it would fail (a
        to-one relation leaf has no flat filter field) and would be redundant.
        """

        direct_relations = {
            fp
            for fp, _ in spec
            if "__" not in fp and getattr(self.model._meta.get_field(fp), "many_to_one", False)
        }
        echo_spec = [
            (fp, grain)
            for fp, grain in spec
            if "__" not in fp or fp.split("__", 1)[0] not in direct_relations
        ]
        return super()._echo_bucket_filter(key_kwargs, echo_spec)


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

    assert_no_gated_read_fields(model, group_by_fields, "aggregate group_by axes", "bucket keys leak gated values")
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
