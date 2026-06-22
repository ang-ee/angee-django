"""Tests for the REBAC-aware aggregate seam."""

from __future__ import annotations

import pytest
import strawberry
import strawberry_django
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from rebac import system_context
from strawberry import auto

import angee.graphql.access as access
from angee.base.models import AngeeDataModel
from angee.graphql.aggregates import rebac_aggregate_builder
from angee.graphql.data import data_query
from angee.graphql.data.metadata import data_query_metadata
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import SchemaAddon


class DataQueryThing(AngeeDataModel):
    """Concrete test model with the public identity data_query requires."""

    sqid_prefix = "dqt_"

    name = models.CharField(max_length=64)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class DataQueryParent(AngeeDataModel):
    """Concrete parent model used by relation group-axis tests."""

    sqid_prefix = "dqp_"

    name = models.CharField(max_length=64)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class DataQueryChild(AngeeDataModel):
    """Concrete child model used by relation group-axis tests."""

    sqid_prefix = "dqc_"

    name = models.CharField(max_length=64)
    parent = models.ForeignKey(DataQueryParent, on_delete=models.CASCADE, related_name="children")

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


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

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingType:
        name: auto

    @strawberry_django.filter_type(DataQueryThing, lookups=True)
    class DataQueryThingFilter:
        name: auto

    @strawberry_django.order_type(DataQueryThing)
    class DataQueryThingOrder:
        name: auto

    query, generated_types = data_query(
        DataQueryThingType,
        type_name="DataQueryThingQuery",
        filters=DataQueryThingFilter,
        order=DataQueryThingOrder,
        list_name="things",
        detail_name="thing",
        aggregate_name="thing_aggregate",
        group_name="thing_groups",
        aggregate_fields=["id"],
        group_by_fields=["name"],
    )

    schema = strawberry.Schema(query=query, types=list(generated_types))
    sdl = schema.as_str()

    assert "things(" in sdl
    assert "thing(id: ID!" in sdl
    assert "thingAggregate(" in sdl
    assert "thingGroups(" in sdl
    assert "input DataQueryThingGroupBySpec" in sdl

    metadata = data_query_metadata(query)[0]
    assert metadata.model_label == "tests.DataQueryThing"
    assert metadata.roots.list_name == "things"
    assert metadata.roots.detail_name == "thing"
    assert metadata.roots.aggregate_name == "thing_aggregate"
    assert metadata.roots.group_name == "thing_groups"
    assert metadata.capabilities == ("list", "detail", "aggregate", "groups")
    assert metadata.filter_fields == ("name",)
    assert metadata.order_fields == ("name",)
    assert metadata.aggregate_fields == ("id",)
    assert metadata.group_by_fields == ("name",)
    assert metadata.type_names.group_by_spec == "DataQueryThingGroupBySpec"


def test_data_query_metadata_requires_direct_relation_axis_for_relation_label() -> None:
    """A relation label axis only describes a bucket when the relation id axis exists."""

    @strawberry_django.type(DataQueryChild)
    class DataQueryChildInvalidRelationType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="requires matching direct relation"):
        data_query(
            DataQueryChildInvalidRelationType,
            type_name="DataQueryChildInvalidRelationQuery",
            list_name="children",
            aggregate_fields=["id"],
            group_by_fields=["parent__name"],
        )


def test_data_query_metadata_rejects_multiple_relation_label_axes() -> None:
    """One direct relation bucket gets one label axis in metadata."""

    @strawberry_django.type(DataQueryChild)
    class DataQueryChildAmbiguousRelationType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="multiple label axes"):
        data_query(
            DataQueryChildAmbiguousRelationType,
            type_name="DataQueryChildAmbiguousRelationQuery",
            list_name="children",
            aggregate_fields=["id"],
            group_by_fields=["parent", "parent__name", "parent__created_at"],
        )


def test_data_query_metadata_declares_group_aliases() -> None:
    """A display field can group through a declared backend aggregate axis."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingAliasType:
        name: auto

    query, _generated_types = data_query(
        DataQueryThingAliasType,
        type_name="DataQueryThingAliasQuery",
        list_name="things",
        aggregate_fields=["id"],
        group_by_fields=["name"],
        group_aliases={"name": "name"},
    )

    metadata = data_query_metadata(query)[0]
    assert metadata.group_aliases[0].field == "name"
    assert metadata.group_aliases[0].aggregate_field == "name"
    assert metadata.group_aliases[0].aggregate_key == "name"


def test_data_query_metadata_rejects_group_aliases_without_node_field() -> None:
    """A group alias must point at a real GraphQL node field."""

    @strawberry_django.type(DataQueryChild)
    class DataQueryChildMissingAliasType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="not a field"):
        data_query(
            DataQueryChildMissingAliasType,
            type_name="DataQueryChildMissingAliasQuery",
            list_name="children",
            aggregate_fields=["id"],
            group_by_fields=["name"],
            group_aliases={"missing": "name"},
        )


def test_data_query_metadata_rejects_group_aliases_without_group_axis() -> None:
    """A group alias cannot target an axis the backend does not group by."""

    @strawberry_django.type(DataQueryChild)
    class DataQueryChildBadAliasType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="non-groupable aggregate axis"):
        data_query(
            DataQueryChildBadAliasType,
            type_name="DataQueryChildBadAliasQuery",
            list_name="children",
            aggregate_fields=["id"],
            group_by_fields=["parent"],
            group_aliases={"name": "created_at"},
        )


@pytest.mark.django_db(transaction=True)
def test_data_query_groups_echo_public_relation_id() -> None:
    """Grouped relation keys expose public ids while label axes expose display values."""

    @strawberry_django.type(DataQueryChild)
    class DataQueryChildRelationType:
        name: auto

    query, generated_types = data_query(
        DataQueryChildRelationType,
        type_name="DataQueryChildRelationQuery",
        list_name="children",
        detail_name="child",
        aggregate_name="child_aggregate",
        group_name="child_groups",
        aggregate_fields=["id"],
        group_by_fields=["parent", "parent__name"],
    )

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(DataQueryParent)
        schema_editor.create_model(DataQueryChild)

    try:
        parent = DataQueryParent.objects.create(name="Parent")
        DataQueryChild.objects.create(parent=parent, name="Child")

        schema = strawberry.Schema(query=query, types=list(generated_types))
        with system_context(reason="test data query relation grouping"):
            result = schema.execute_sync(
                """
                query($groupBy: [DataQueryChildGroupBySpec!]!) {
                  childGroups(groupBy: $groupBy, pagination: {offset: 0, limit: 10}) {
                    totalCount
                    results {
                      key { parentId parent_Name }
                      count
                    }
                  }
                }
                """,
                variable_values={"groupBy": [{"field": "PARENT"}, {"field": "PARENT__NAME"}]},
            )
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(DataQueryChild)
            schema_editor.delete_model(DataQueryParent)

    assert result.errors is None
    assert result.data == {
        "childGroups": {
            "totalCount": 1,
            "results": [
                {
                    "key": {
                        "parentId": parent.public_id,
                        "parent_Name": "Parent",
                    },
                    "count": 1,
                }
            ],
        }
    }


@pytest.mark.django_db(transaction=True)
def test_data_query_forwards_list_kwargs_resolver() -> None:
    """Generated list roots keep resolver-owned querysets from their caller."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingResolverType:
        name: auto

    def visible_things(info: strawberry.Info) -> object:
        del info
        return DataQueryThing.objects.filter(name="visible")

    query, generated_types = data_query(
        DataQueryThingResolverType,
        type_name="DataQueryThingResolverQuery",
        list_name="things",
        include_detail=False,
        include_aggregate=False,
        include_groups=False,
        list_kwargs={"resolver": visible_things},
    )

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(DataQueryThing)

    try:
        DataQueryThing.objects.create(name="visible")
        DataQueryThing.objects.create(name="hidden")

        schema = strawberry.Schema(query=query, types=list(generated_types))
        with system_context(reason="test data query resolver"):
            result = schema.execute_sync("{ things { totalCount results { name } } }")
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(DataQueryThing)

    assert result.errors is None
    assert result.data == {
        "things": {
            "totalCount": 1,
            "results": [{"name": "visible"}],
        }
    }


def test_data_query_requires_explicit_list_name() -> None:
    """Model list root names are public schema, so they must be declared."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingExplicitListType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="list_name"):
        data_query(
            DataQueryThingExplicitListType,
            type_name="DataQueryThingExplicitListQuery",
            aggregate_fields=["id"],
            group_by_fields=["name"],
        )


def test_data_query_rejects_raw_pk_models() -> None:
    """Public data surfaces must not silently fall back to Django primary keys."""

    @strawberry_django.type(Group)
    class RawPkGroupType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="sqid public id"):
        data_query(
            RawPkGroupType,
            type_name="RawPkGroupQuery",
            list_name="groups",
            include_detail=False,
            include_aggregate=False,
            include_groups=False,
        )


def test_schema_owner_collects_data_query_metadata() -> None:
    """GraphQLSchemas exposes data-query metadata by composed schema bucket."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingOwnerType:
        name: auto

    query, generated_types = data_query(
        DataQueryThingOwnerType,
        type_name="DataQueryThingOwnerQuery",
        list_name="things",
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

    assert tuple(item.model_label for item in schemas.data_queries("public")) == ("tests.DataQueryThing",)
