"""Tests for the REBAC-aware aggregate seam."""

from __future__ import annotations

import enum
import warnings
from typing import NewType

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
from angee.graphql.data import data_query, hasura_resource
from angee.graphql.data.metadata import (
    DataAggregateMeasureMetadata,
    DataQueryRoots,
    DataResourceFieldMetadata,
    DataResourceRoots,
    DataResourceTypeNames,
    data_query_metadata,
    data_resource_from_data_query,
    make_data_query_metadata,
    make_data_resource_metadata,
)
from angee.graphql.node import AngeeNode
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
    related_parents = models.ManyToManyField(DataQueryParent, related_name="related_children")

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class DataQueryTimedThing(AngeeDataModel):
    """Concrete model with a field class not supported by resource metadata."""

    sqid_prefix = "dtt_"

    duration = models.DurationField()

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class DataQueryHasuraThing(AngeeDataModel):
    """Concrete model used by Hasura resource metadata bridge tests."""

    sqid_prefix = "dqht_"

    name = models.CharField(max_length=64)
    word_count = models.IntegerField(default=0)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"
        ordering = ("-word_count", "name")


class DataQueryThingMood(enum.Enum):
    """Synthetic computed enum used by resource field metadata tests."""

    HAPPY = "happy"


DataQueryThingMoodType = strawberry.enum(DataQueryThingMood)
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Passing a class to strawberry.scalar")
    DataQueryThingUnsupportedScalar = strawberry.scalar(
        NewType("DataQueryThingUnsupportedScalar", str),
        serialize=str,
        parse_value=str,
    )


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
    assert metadata.group_dimensions[0].field == "name"
    assert metadata.group_dimensions[0].input == "NAME"
    assert metadata.group_dimensions[0].key == "name"
    assert metadata.default_measures[0].op == "count"
    assert metadata.aggregate_measures == ()
    assert metadata.default_sort == ()
    assert metadata.type_names.group_by_spec == "DataQueryThingGroupBySpec"
    assert metadata.type_names.group_order == "DataQueryThingGroupOrder"


def test_hasura_resource_attaches_angee_resource_metadata() -> None:
    """The Hasura builder remains external while Angee owns resource metadata."""

    @strawberry_django.type(DataQueryHasuraThing)
    class DataQueryHasuraThingType(AngeeNode):
        name: auto
        word_count: auto

    write_backend = type(
        "NoopWriteBackend",
        (),
        {
            "create": lambda self, info, data: None,
            "update": lambda self, info, pk, data: None,
            "delete": lambda self, info, pk: None,
        },
    )()
    resource = hasura_resource(
        DataQueryHasuraThingType,
        model=DataQueryHasuraThing,
        name="things",
        filterable=["id", "name", "word_count"],
        sortable=["word_count", "name"],
        aggregatable=["id", "word_count"],
        groupable=["name"],
        get_queryset=lambda info: DataQueryHasuraThing.objects.all(),
        write_backend=write_backend,
        id_decode=lambda value: value,
    )
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": [resource.query],
                        "mutation": [resource.mutation],
                        "types": [DataQueryHasuraThingType, *resource.types],
                    }
                }
            )
        ]
    ).build("public")
    metadata = schema.angee_resources[0]
    fields = {field.name: field for field in metadata.fields}

    assert metadata.roots == DataResourceRoots(
        list_name="things",
        detail_name="things_by_pk",
        aggregate_name="things_aggregate",
        group_name="things_groups",
        create_name="insert_things_one",
        update_name="update_things_by_pk",
        delete_name="delete_things_by_pk",
    )
    assert metadata.type_names == DataResourceTypeNames(
        query="things_Query",
        node="DataQueryHasuraThingType",
        filter="things_bool_exp",
        order="things_order_by",
        aggregate="things_aggregate",
        grouped="things_group",
        group_key="DataQueryHasuraThingTypeGroupKey",
        group_by_spec="DataQueryHasuraThingTypeGroupBySpec",
        group_order="DataQueryHasuraThingTypeGroupOrder",
        having="DataQueryHasuraThingTypeHaving",
        create_input="things_insert_input",
        update_input="things_set_input",
    )
    assert metadata.capabilities == (
        "list",
        "detail",
        "aggregate",
        "groups",
        "create",
        "update",
        "delete",
    )
    assert metadata.filter_fields == ("id", "name", "word_count")
    assert metadata.order_fields == ("word_count", "name")
    assert metadata.aggregate_fields == ("id", "word_count")
    assert metadata.group_by_fields == ("name",)
    assert metadata.group_dimensions[0].field == "name"
    assert metadata.group_dimensions[0].input == "NAME"
    assert metadata.group_dimensions[0].key == "name"
    assert metadata.aggregate_measures == (
        DataAggregateMeasureMetadata(op="sum", field="word_count", input="word_count"),
        DataAggregateMeasureMetadata(op="avg", field="word_count", input="word_count"),
        DataAggregateMeasureMetadata(op="min", field="word_count", input="word_count"),
        DataAggregateMeasureMetadata(op="max", field="word_count", input="word_count"),
    )
    assert metadata.default_measures[0].op == "count"
    assert [(sort.field, sort.direction) for sort in metadata.default_sort] == [
        ("word_count", "DESC"),
        ("name", "ASC"),
    ]
    assert metadata.create_fields == ("name", "word_count")
    assert metadata.update_fields == ("name", "word_count")
    assert metadata.required_create_fields == ("name",)
    assert fields["word_count"].filterable is True
    assert fields["word_count"].sortable is True
    assert fields["word_count"].aggregatable is True
    assert fields["word_count"].creatable is True
    assert fields["word_count"].updatable is True
    sdl = schema.as_str()
    assert "word_count" in sdl
    assert "wordCount" not in sdl


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
    resource = schemas.resources("public")[0]
    assert resource.model_label == "tests.DataQueryThing"
    assert resource.roots.list_name == "things"
    assert resource.roots.aggregate_name == "dataquerythingAggregate"
    assert resource.roots.group_name == "dataquerythingGroups"
    assert resource.capabilities == ("list", "detail", "aggregate", "groups")
    assert resource.fields[0].name == "name"
    assert resource.fields[0].readable is True
    assert resource.fields[0].filterable is False
    assert resource.fields[0].groupable is True
    assert resource.type_names.group_order == "DataQueryThingGroupOrder"
    assert resource.group_dimensions[0].field == "name"
    assert resource.group_dimensions[0].input == "NAME"
    assert resource.group_dimensions[0].key == "name"
    assert resource.default_measures[0].op == "count"
    assert resource.default_sort == ()


def test_data_query_metadata_rejects_unknown_group_axis_path() -> None:
    """Group axes must resolve to a concrete model field path."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingUnknownGroupType:
        name: auto

    class DataQueryThingUnknownGroupQuery:
        """Synthetic query surface for metadata validation."""

    with pytest.raises(ImproperlyConfigured, match="unknown group axis field path 'name__missing'"):
        make_data_query_metadata(
            query_type=DataQueryThingUnknownGroupQuery,
            node_type=DataQueryThingUnknownGroupType,
            model=DataQueryThing,
            roots=DataQueryRoots(list_name="things"),
            filter_type=None,
            order_type=None,
            aggregate_fields=(),
            group_by_fields=("name__missing",),
            group_aliases=(),
            enable_filter_echo=False,
        )


def test_data_query_metadata_rejects_duplicate_group_axes() -> None:
    """Duplicate backend group declarations must fail before artifact emission."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingDuplicateGroupType:
        name: auto

    class DataQueryThingDuplicateGroupQuery:
        """Synthetic query surface for metadata validation."""

    with pytest.raises(ImproperlyConfigured, match="duplicate group axis 'name'"):
        make_data_query_metadata(
            query_type=DataQueryThingDuplicateGroupQuery,
            node_type=DataQueryThingDuplicateGroupType,
            model=DataQueryThing,
            roots=DataQueryRoots(list_name="things"),
            filter_type=None,
            order_type=None,
            aggregate_fields=(),
            group_by_fields=("name", "name"),
            group_aliases=(),
            enable_filter_echo=False,
        )


@pytest.mark.parametrize("path", ["related_parents", "related_parents__name"])
def test_data_query_metadata_rejects_to_many_group_axis_path(path: str) -> None:
    """Grouped bucket axes cannot point at to-many relations."""

    @strawberry_django.type(DataQueryChild)
    class DataQueryChildToManyType:
        name: auto

    class DataQueryChildToManyQuery:
        """Synthetic query surface for metadata validation."""

    with pytest.raises(ImproperlyConfigured, match=f"unsupported to-many group axis field path '{path}'"):
        make_data_query_metadata(
            query_type=DataQueryChildToManyQuery,
            node_type=DataQueryChildToManyType,
            model=DataQueryChild,
            roots=DataQueryRoots(list_name="children"),
            filter_type=None,
            order_type=None,
            aggregate_fields=(),
            group_by_fields=(path,),
            group_aliases=(),
            enable_filter_echo=False,
        )


def test_data_resource_metadata_rejects_duplicate_field_metadata() -> None:
    """Resource field metadata names are authoritative and must be unique."""

    with pytest.raises(ImproperlyConfigured, match="duplicate resource field 'name'"):
        make_data_resource_metadata(
            model=DataQueryThing,
            roots=DataResourceRoots(list_name="things"),
            type_names=DataResourceTypeNames(node="DataQueryThingType"),
            capabilities=("list",),
            fields=(
                DataResourceFieldMetadata(
                    name="name",
                    kind="scalar",
                    readable=True,
                    filterable=False,
                    sortable=False,
                    aggregatable=False,
                    groupable=False,
                    creatable=False,
                    updatable=False,
                    required_on_create=False,
                ),
                DataResourceFieldMetadata(
                    name="name",
                    kind="scalar",
                    readable=True,
                    filterable=False,
                    sortable=False,
                    aggregatable=False,
                    groupable=False,
                    creatable=False,
                    updatable=False,
                    required_on_create=False,
                ),
            ),
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        (
            DataResourceFieldMetadata(name="name", kind="unknown"),
            "unsupported kind 'unknown'",
        ),
        (
            DataResourceFieldMetadata(name="name", kind="scalar", scalar="Magic"),
            "unsupported scalar 'Magic'",
        ),
        (
            DataResourceFieldMetadata(name="name", kind="scalar", widget="slider"),
            "unsupported widget 'slider'",
        ),
        (
            DataResourceFieldMetadata(name="name", kind="relation", scalar="String"),
            "cannot declare scalar 'String' for relation fields",
        ),
    ],
)
def test_data_resource_metadata_rejects_unsupported_explicit_field_metadata(
    field: DataResourceFieldMetadata,
    message: str,
) -> None:
    """Explicit field metadata must stay inside the generated artifact vocabulary."""

    with pytest.raises(ImproperlyConfigured, match=message):
        make_data_resource_metadata(
            model=DataQueryThing,
            roots=DataResourceRoots(list_name="things"),
            type_names=DataResourceTypeNames(node="DataQueryThingType"),
            capabilities=("list",),
            fields=(field,),
        )


def test_data_resource_metadata_rejects_generated_duplicate_field_names() -> None:
    """Generated resource field metadata must not emit duplicate wire names."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingDuplicateFieldType:
        name: auto

        @strawberry.field(name="name")
        def name_copy(self) -> str:
            return self.name

    with pytest.raises(ImproperlyConfigured, match="duplicate resource field 'name'"):
        make_data_resource_metadata(
            model=DataQueryThing,
            roots=DataResourceRoots(list_name="things"),
            type_names=DataResourceTypeNames(node="DataQueryThingDuplicateFieldType"),
            capabilities=("list",),
            node_type=DataQueryThingDuplicateFieldType,
        )


def test_data_resource_metadata_marks_public_id_field_as_id_scalar() -> None:
    """The GraphQL public id is an ID boundary, not the model's integer pk."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingNodeType(AngeeNode):
        name: auto

    query, _generated_types = data_query(
        DataQueryThingNodeType,
        type_name="DataQueryThingNodeQuery",
        list_name="things",
        include_aggregate=False,
        include_groups=False,
    )

    resource = data_resource_from_data_query(data_query_metadata(query)[0])
    fields = {field.name: field for field in resource.fields}

    assert fields["id"].kind == "scalar"
    assert fields["id"].scalar == "ID"
    assert fields["id"].widget is None


def test_data_resource_metadata_marks_computed_surface_enum_field() -> None:
    """Strawberry enum surfaces own enum field classification."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingComputedEnumType(AngeeNode):
        name: auto

        @strawberry.field
        def mood(self) -> DataQueryThingMoodType:
            return DataQueryThingMood.HAPPY

    resource = make_data_resource_metadata(
        model=DataQueryThing,
        roots=DataResourceRoots(list_name="things"),
        type_names=DataResourceTypeNames(node="DataQueryThingComputedEnumType"),
        capabilities=("list",),
        node_type=DataQueryThingComputedEnumType,
    )
    fields = {field.name: field for field in resource.fields}

    assert fields["mood"].kind == "enum"
    assert fields["mood"].scalar is None


def test_data_resource_metadata_marks_forward_object_field_as_relation() -> None:
    """Unresolved object return types are relation-shaped, not scalar guesses."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingForwardRelationType(AngeeNode):
        name: auto

        @strawberry.field
        def parent(self) -> DataQueryParentForwardTargetType | None:  # type: ignore[name-defined]
            return None

    resource = make_data_resource_metadata(
        model=DataQueryThing,
        roots=DataResourceRoots(list_name="things"),
        type_names=DataResourceTypeNames(node="DataQueryThingForwardRelationType"),
        capabilities=("list",),
        node_type=DataQueryThingForwardRelationType,
    )

    @strawberry_django.type(DataQueryParent)
    class DataQueryParentForwardTargetType(AngeeNode):
        name: auto

    fields = {field.name: field for field in resource.fields}

    assert fields["parent"].kind == "relation"
    assert fields["parent"].scalar is None


def test_data_resource_metadata_rejects_unsupported_surface_scalar() -> None:
    """Scalar resource fields must have a supported metadata scalar family."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingUnsupportedScalarType(AngeeNode):
        name: auto

        @strawberry.field
        def mystery(self) -> DataQueryThingUnsupportedScalar:
            return DataQueryThingUnsupportedScalar("mystery")

    with pytest.raises(
        ImproperlyConfigured,
        match="cannot classify GraphQL scalar for field 'mystery' \\(DataQueryThingUnsupportedScalar\\)",
    ):
        make_data_resource_metadata(
            model=DataQueryThing,
            roots=DataResourceRoots(list_name="things"),
            type_names=DataResourceTypeNames(node="DataQueryThingUnsupportedScalarType"),
            capabilities=("list",),
            node_type=DataQueryThingUnsupportedScalarType,
        )


def test_data_query_metadata_rejects_unsupported_group_axis_field_class() -> None:
    """Group dimensions must fail on field classes the metadata cannot classify."""

    @strawberry_django.type(DataQueryTimedThing)
    class DataQueryTimedThingType(AngeeNode):
        @strawberry.field
        def label(self) -> str:
            return "timed"

    with pytest.raises(
        ImproperlyConfigured,
        match="cannot classify unsupported field 'duration' \\(DurationField\\)",
    ):
        make_data_query_metadata(
            query_type=type("DataQueryTimedThingQuery", (), {}),
            node_type=DataQueryTimedThingType,
            model=DataQueryTimedThing,
            roots=DataQueryRoots(list_name="timedThings"),
            filter_type=None,
            order_type=None,
            aggregate_fields=(),
            group_by_fields=("duration",),
            group_aliases=(),
            enable_filter_echo=False,
        )


def test_data_query_metadata_marks_to_many_node_fields_as_lists() -> None:
    """Resource fields must not describe to-many object lists as to-one relations."""

    @strawberry_django.type(DataQueryParent)
    class DataQueryParentListFieldType:
        name: auto

    @strawberry_django.type(DataQueryChild)
    class DataQueryChildListFieldType:
        name: auto
        related_parents: list[DataQueryParentListFieldType]

    query, _generated_types = data_query(
        DataQueryChildListFieldType,
        type_name="DataQueryChildListFieldQuery",
        list_name="children",
        aggregate_fields=["id"],
        group_by_fields=["name"],
    )

    resource = data_resource_from_data_query(data_query_metadata(query)[0])
    fields = {field.name: field for field in resource.fields}

    assert fields["relatedParents"].kind == "list"
    assert fields["relatedParents"].scalar is None
    assert fields["relatedParents"].widget is None


def test_data_query_metadata_rejects_unknown_aggregate_measure_path() -> None:
    """Aggregate measures must resolve to a concrete model field path."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingUnknownMeasureType:
        name: auto

    class DataQueryThingUnknownMeasureQuery:
        """Synthetic query surface for metadata validation."""

    with pytest.raises(ImproperlyConfigured, match="unknown aggregate measure field path 'name__missing'"):
        make_data_query_metadata(
            query_type=DataQueryThingUnknownMeasureQuery,
            node_type=DataQueryThingUnknownMeasureType,
            model=DataQueryThing,
            roots=DataQueryRoots(list_name="things"),
            filter_type=None,
            order_type=None,
            aggregate_fields=("name__missing",),
            group_by_fields=(),
            group_aliases=(),
            enable_filter_echo=False,
        )


def test_data_query_metadata_rejects_unsupported_aggregate_measure_field_class() -> None:
    """Non-PK aggregate fields must map to a supported frontend measure family."""

    @strawberry_django.type(DataQueryThing)
    class DataQueryThingTextMeasureType:
        name: auto

    class DataQueryThingTextMeasureQuery:
        """Synthetic query surface for metadata validation."""

    with pytest.raises(
        ImproperlyConfigured,
        match="unsupported aggregate measure field path 'name' \\(CharField\\)",
    ):
        make_data_query_metadata(
            query_type=DataQueryThingTextMeasureQuery,
            node_type=DataQueryThingTextMeasureType,
            model=DataQueryThing,
            roots=DataQueryRoots(list_name="things"),
            filter_type=None,
            order_type=None,
            aggregate_fields=("name",),
            group_by_fields=(),
            group_aliases=(),
            enable_filter_echo=False,
        )


def test_data_query_metadata_rejects_to_many_aggregate_measure_path() -> None:
    """Aggregate measures cannot traverse through to-many relations."""

    @strawberry_django.type(DataQueryChild)
    class DataQueryChildToManyMeasureType:
        name: auto

    class DataQueryChildToManyMeasureQuery:
        """Synthetic query surface for metadata validation."""

    with pytest.raises(
        ImproperlyConfigured,
        match="unsupported to-many aggregate measure field path 'related_parents__name'",
    ):
        make_data_query_metadata(
            query_type=DataQueryChildToManyMeasureQuery,
            node_type=DataQueryChildToManyMeasureType,
            model=DataQueryChild,
            roots=DataQueryRoots(list_name="children"),
            filter_type=None,
            order_type=None,
            aggregate_fields=("related_parents__name",),
            group_by_fields=(),
            group_aliases=(),
            enable_filter_echo=False,
        )
