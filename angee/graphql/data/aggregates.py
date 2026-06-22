"""Angee policy seam over ``strawberry-django-aggregates``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from django.db import models
from strawberry_django_aggregates import AggregateBuilder

from angee.base.models import AngeeQuerySet, public_id_for
from angee.graphql.access import assert_no_gated_read_fields
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME


class AngeeAggregateBuilder(AggregateBuilder):
    """Aggregate builder with Angee's relation-label echo policy."""

    def _echo_bucket_filter(
        self,
        key_kwargs: dict[str, Any],
        spec: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Drop label-only relation-leaf axes before echoing the bucket filter.

        A relation-leaf axis such as ``party__display_name`` is carried only to
        label the bucket with the related record's name. When the same relation
        is also grouped by its direct FK axis, that FK axis owns the drill-down
        filter and the label contributes no clause.
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


def data_aggregate_builder(
    *,
    model: type[models.Model],
    group_by_fields: Sequence[str] = (),
    queryset: AngeeQuerySet[Any] | None = None,
    **kwargs: Any,
) -> AggregateBuilder:
    """Return an aggregate builder wired for Angee row scope and public ids."""

    assert_no_gated_read_fields(
        model,
        group_by_fields,
        "aggregate group_by axes",
        "bucket keys leak gated values",
    )
    source = cast(
        AngeeQuerySet[Any],
        model._default_manager.all() if queryset is None else queryset,
    )
    if kwargs.get("enable_filter_echo"):
        kwargs.setdefault(
            "filter_echo_relation_identity",
            _relation_public_identity,
        )

    def get_queryset(info: Any) -> models.QuerySet[Any]:
        del info
        return source.all().scoped_for_aggregate()

    return AngeeAggregateBuilder(
        model=model,
        group_by_fields=list(group_by_fields),
        get_queryset=get_queryset,
        **kwargs,
    )


def rebac_aggregate_builder(
    *,
    model: type[models.Model],
    group_by_fields: Sequence[str] = (),
    queryset: AngeeQuerySet[Any] | None = None,
    **kwargs: Any,
) -> AggregateBuilder:
    """Compatibility alias for Angee's data aggregate builder."""

    return data_aggregate_builder(
        model=model,
        group_by_fields=group_by_fields,
        queryset=queryset,
        **kwargs,
    )


def _relation_public_identity(
    field: models.Field[Any, Any],
    value: Any,
) -> Mapping[str, Any]:
    """Return the filter lookup for a grouped FK bucket's public id."""

    related_model = field.remote_field.model
    return {
        PUBLIC_ID_FIELD_NAME: public_id_for(related_model, value),
    }
