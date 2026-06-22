"""Tests for the REBAC-aware aggregate seam."""

from __future__ import annotations

import pytest
import strawberry
import strawberry_django
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from strawberry import auto

import angee.graphql.access as access
from angee.graphql.aggregates import rebac_aggregate_builder
from angee.graphql.data import data_query
from angee.graphql.data.metadata import data_query_metadata
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import SchemaAddon


def test_rebac_aggregate_builder_rejects_gated_group_by_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A field-gated read column may not be an aggregate group-by axis.

    Group-by axes become dict-row bucket keys that field-read redaction cannot
    touch, so exposing a gated column would leak owner-only values. The builder
    refuses it at construction time rather than relying on author discipline.
    """

    monkeypatch.setattr(access, "gated_read_fields", lambda model: {"secret"})

    with pytest.raises(ImproperlyConfigured, match="field-gated"):
        rebac_aggregate_builder(model=Group, group_by_fields=["name", "secret"])


def test_rebac_aggregate_builder_gate_walks_relation_leaf_axes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relation-leaf axis (``content_type__model``) is gate-checked at its leaf.

    A dotted axis is never a field on the base model, so a same-model check is
    blind to it — yet its value comes from the joined model's row, which row scope
    never touches. The gate must walk the relation to the leaf model so a gated
    read reached through a relation is refused exactly as a direct one is.
    """

    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    monkeypatch.setattr(
        access,
        "gated_read_fields",
        lambda model: {"model"} if model is ContentType else set(),
    )

    with pytest.raises(ImproperlyConfigured, match="field-gated"):
        rebac_aggregate_builder(model=Permission, group_by_fields=["content_type__model"])

    # A non-gated leaf on the same relation passes (the leaf, not the path, is gated).
    rebac_aggregate_builder(model=Permission, group_by_fields=["content_type__app_label"])


def test_data_query_builds_native_model_data_roots() -> None:
    """The data-query helper emits a normal Strawberry query surface."""

    @strawberry_django.type(Group)
    class GroupDataType:
        name: auto

    @strawberry_django.filter_type(Group, lookups=True)
    class GroupDataFilter:
        name: auto

    @strawberry_django.order_type(Group)
    class GroupDataOrder:
        name: auto

    query, generated_types = data_query(
        GroupDataType,
        type_name="GroupDataQuery",
        filters=GroupDataFilter,
        order=GroupDataOrder,
        list_name="groups",
        aggregate_fields=["id"],
        group_by_fields=["name"],
    )

    schema = strawberry.Schema(query=query, types=list(generated_types))
    sdl = schema.as_str()

    assert "groups(" in sdl
    assert "group(id: ID!" in sdl
    assert "groupAggregate(" in sdl
    assert "groupGroups(" in sdl
    assert "input GroupGroupBySpec" in sdl

    metadata = data_query_metadata(query)[0]
    assert metadata.model_label == "auth.Group"
    assert metadata.roots.list_name == "groups"
    assert metadata.roots.detail_name == "group"
    assert metadata.roots.aggregate_name == "group_aggregate"
    assert metadata.roots.group_name == "group_groups"
    assert metadata.capabilities == ("list", "detail", "aggregate", "groups")
    assert metadata.filter_fields == ("name",)
    assert metadata.order_fields == ("name",)
    assert metadata.aggregate_fields == ("id",)
    assert metadata.group_by_fields == ("name",)
    assert metadata.type_names.group_by_spec == "GroupGroupBySpec"


def test_data_query_requires_explicit_list_name() -> None:
    """Model list root names are public schema, so they must be declared."""

    @strawberry_django.type(Group)
    class GroupExplicitListType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="list_name"):
        data_query(
            GroupExplicitListType,
            type_name="GroupExplicitListQuery",
            aggregate_fields=["id"],
            group_by_fields=["name"],
        )


def test_schema_owner_collects_data_query_metadata() -> None:
    """GraphQLSchemas exposes data-query metadata by composed schema bucket."""

    @strawberry_django.type(Group)
    class GroupQueryType:
        name: auto

    query, generated_types = data_query(
        GroupQueryType,
        type_name="GroupOwnerQuery",
        list_name="groups",
        aggregate_fields=["id"],
        group_by_fields=["name"],
    )

    schemas = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": (query,),
                        "types": tuple(generated_types),
                    }
                }
            )
        ]
    )

    assert tuple(item.model_label for item in schemas.data_queries("public")) == ("auth.Group",)
