"""Strawberry query class factories for model-backed data surfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import SqidPublicIdentity, is_public_data_model
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.aggregates import data_aggregate_builder
from angee.graphql.data.metadata import (
    DataQueryRoots,
    attach_data_query_metadata,
    make_data_query_metadata,
)
from angee.graphql.introspection import django_model, surface_name
from angee.graphql.node import detail


def data_query(
    node: type,
    *,
    name: str | None = None,
    type_name: str | None = None,
    filters: type | None = None,
    order: type | None = None,
    list_name: str | None = None,
    detail_name: str | None = None,
    aggregate_name: str | None = None,
    group_name: str | None = None,
    aggregate_fields: Sequence[str] = (),
    group_by_fields: Sequence[str] = (),
    group_aliases: Mapping[str, str] | None = None,
    enable_filter_echo: bool = False,
    include_list: bool = True,
    include_detail: bool = True,
    include_aggregate: bool = True,
    include_groups: bool = True,
    permission_classes: list[type] | None = None,
    list_kwargs: dict[str, Any] | None = None,
    aggregate_kwargs: dict[str, Any] | None = None,
    allow_raw_pk_compat: bool = False,
    public_identity: SqidPublicIdentity | None = None,
    model_label: str | None = None,
) -> tuple[type, tuple[object, ...]]:
    """Return a Strawberry query type plus generated data helper types.

    The returned query type is an ordinary ``@strawberry.type`` class. This
    helper assembles the repeated Angee model-data surface while delegating list,
    filter, order, aggregate, and group mechanics to the parent libraries.
    """

    model = django_model(node)
    _require_public_data_identity(
        node,
        model,
        allow_raw_pk_compat=allow_raw_pk_compat,
        public_identity=public_identity,
    )
    singular = name or model._meta.model_name
    list_options = dict(list_kwargs or {})
    aggregate_options = dict(aggregate_kwargs or {})
    annotations: dict[str, Any] = {}
    namespace: dict[str, Any] = {
        "__annotations__": annotations,
        "__doc__": f"Data query surface for {model._meta.label}.",
        "__module__": getattr(node, "__module__", __name__),
    }
    generated_types: list[object] = []
    list_attr: str | None = None
    detail_attr: str | None = None
    aggregate_attr: str | None = None
    group_attr: str | None = None
    aggregate_type: type | None = None
    grouped_type: type | None = None
    group_key_type: type | None = None
    group_by_spec_type: type | None = None
    group_order_input_type: type | None = None
    having_type: type | None = None

    if include_list:
        if list_name is None:
            raise ImproperlyConfigured(f"data_query({surface_name(node)}) needs list_name when include_list=True")
        list_attr = list_name
        annotations[list_attr] = cast(Any, OffsetPaginated)[node]
        namespace[list_attr] = strawberry_django.offset_paginated(
            filters=filters,
            order=order,
            permission_classes=permission_classes,
            **list_options,
        )

    if include_detail:
        detail_attr = detail_name or singular
        annotations[detail_attr] = node | None
        namespace[detail_attr] = detail(
            node,
            permission_classes=permission_classes,
            public_identity=public_identity,
        )

    if include_aggregate or include_groups:
        if not aggregate_fields and include_aggregate:
            raise ImproperlyConfigured(
                f"data_query({surface_name(node)}) needs aggregate_fields when include_aggregate=True"
            )
        if not group_by_fields and include_groups:
            raise ImproperlyConfigured(
                f"data_query({surface_name(node)}) needs group_by_fields when include_groups=True"
            )
        built = data_aggregate_builder(
            model=model,
            aggregate_fields=list(aggregate_fields),
            group_by_fields=list(group_by_fields),
            filter_type=filters,
            enable_filter_echo=enable_filter_echo,
            **aggregate_options,
        ).build()
        aggregate_type = built.aggregate_type
        grouped_type = built.grouped_type
        group_key_type = built.group_key_type
        group_by_spec_type = built.group_by_spec
        group_order_input_type = _argument_named_type(built.group_by_field, "order_by")
        having_type = built.having_input
        generated_types.extend(
            [
                built.aggregate_type,
                built.grouped_type,
                built.grouped_result_type,
                built.group_key_type,
            ]
        )
        if include_aggregate:
            aggregate_attr = aggregate_name or f"{singular}_aggregate"
            annotations[aggregate_attr] = built.aggregate_type
            namespace[aggregate_attr] = built.aggregate_field
        if include_groups:
            group_attr = group_name or f"{singular}_groups"
            annotations[group_attr] = built.grouped_result_type
            namespace[group_attr] = built.group_by_field

    if not annotations:
        raise ImproperlyConfigured(f"data_query({surface_name(node)}) needs at least one field")

    query_name = type_name or f"{_type_stem(singular)}DataQuery"
    query = type(query_name, (), namespace)
    query = strawberry.type(query)
    metadata = make_data_query_metadata(
        query_type=query,
        node_type=node,
        model=model,
        roots=DataQueryRoots(
            list_name=list_attr,
            detail_name=detail_attr,
            aggregate_name=aggregate_attr,
            group_name=group_attr,
        ),
        filter_type=filters,
        order_type=order,
        aggregate_fields=tuple(aggregate_fields),
        group_by_fields=tuple(group_by_fields),
        group_aliases=tuple((group_aliases or {}).items()),
        enable_filter_echo=enable_filter_echo,
        aggregate_type=aggregate_type,
        grouped_type=grouped_type,
        group_key_type=group_key_type,
        group_by_spec_type=group_by_spec_type,
        group_order_input_type=group_order_input_type,
        having_type=having_type,
        model_label=model_label or (public_identity.model_label if public_identity is not None else None),
        public_id_field=public_identity.public_id_field if public_identity is not None else PUBLIC_ID_FIELD_NAME,
    )
    return attach_data_query_metadata(query, metadata), tuple(generated_types)


def _type_stem(name: str) -> str:
    """Return a GraphQL type-name stem for a root field name."""

    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def _argument_named_type(field: Any, python_name: str) -> type | None:
    """Return the named Python type backing one generated Strawberry field argument."""

    for argument in getattr(field, "arguments", ()):
        if getattr(argument, "python_name", None) != python_name:
            continue
        return _unwrap_named_type(getattr(argument, "type", None))
    return None


def _unwrap_named_type(value: Any) -> type | None:
    """Return the named type under Strawberry optional/list wrappers."""

    current = value
    while hasattr(current, "of_type"):
        current = current.of_type
    return current if isinstance(current, type) else None


def _require_public_data_identity(
    node: type,
    model: type[models.Model],
    *,
    allow_raw_pk_compat: bool,
    public_identity: SqidPublicIdentity | None,
) -> None:
    """Fail fast when a model-backed public data surface lacks an sqid."""

    if public_identity is not None:
        return
    if is_public_data_model(model):
        return
    if allow_raw_pk_compat:
        return
    raise ImproperlyConfigured(
        f"data_query({surface_name(node)}) requires {model._meta.label} to expose an Angee sqid public id; "
        "inherit AngeeDataModel or SqidMixin, including through a concrete parent, before creating a data surface."
    )
