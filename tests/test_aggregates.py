"""Tests for Hasura resource metadata and aggregate contracts."""

from __future__ import annotations

import dataclasses
import enum
import json
import warnings
from collections.abc import Iterator
from decimal import Decimal
from typing import Any, NewType, cast

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import connection, models
from rebac import system_context
from strawberry import auto
from strawberry_django_aggregates.errors import GroupByFieldNotAllowed

from angee.base.models import AngeeDataModel
from angee.data.metadata import (
    DataAggregateMeasureMetadata,
    DataGroupBucketFilterMetadata,
    DataGroupDimensionMetadata,
    DataResourceFieldMetadata,
    DataResourceRoots,
    DataResourceSubtitleMetadata,
    DataResourceTypeNames,
)
from angee.graphql.data import hasura_model_resource, public_pk_decoder
from angee.graphql.data import metadata as metadata_module
from angee.graphql.data.hasura import (
    _hasura_group_dimensions,
    _measure_ops_for_field,
    _relation_filter_decoders,
    _relation_group_key_encoders,
)
from angee.graphql.data.metadata import (
    make_data_resource_metadata,
)
from angee.graphql.ids import require_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import (
    SchemaAddon,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
    result_data,
)


class ResourceThing(AngeeDataModel):
    """Concrete test model used by resource metadata tests."""

    sqid_prefix = "rt_"

    name = models.CharField(max_length=64)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class ResourceParent(AngeeDataModel):
    """Concrete parent model used by relation group-axis tests."""

    sqid_prefix = "rp_"

    name = models.CharField(max_length=64)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class ResourceChild(AngeeDataModel):
    """Concrete child model used by relation group-axis tests."""

    sqid_prefix = "rc_"

    name = models.CharField(max_length=64)
    parent = models.ForeignKey(ResourceParent, on_delete=models.CASCADE, related_name="children")
    related_parents = models.ManyToManyField(ResourceParent, related_name="related_children")

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class ResourceGrandchild(AngeeDataModel):
    """Concrete grandchild model used by nested relation group-axis tests."""

    sqid_prefix = "rg_"

    child = models.ForeignKey(ResourceChild, on_delete=models.CASCADE, related_name="grandchildren")

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


@strawberry_django.type(ResourceParent)
class ResourceSubtitleParentType:
    """Nested object projection used by subtitle leaf-validation tests."""

    name: auto


@strawberry_django.type(ResourceChild)
class ResourceSubtitleChildType:
    """Resource projection carrying a to-one object field."""

    name: auto
    parent: ResourceSubtitleParentType


class ResourceTimedThing(AngeeDataModel):
    """Concrete model with a field class not supported by resource metadata."""

    sqid_prefix = "rtt_"

    duration = models.DurationField()

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class ResourceFlaggedTimestampThing(models.Model):
    """Model whose timestamp semantics come only from Django field flags."""

    label = models.CharField(max_length=64)
    born_on = models.DateTimeField(auto_now_add=True)
    touched_on = models.DateTimeField(auto_now=True)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class HasuraResourceThing(AngeeDataModel):
    """Concrete model used by Hasura resource metadata bridge tests."""

    sqid_prefix = "hrt_"

    name = models.CharField(max_length=64)
    word_count = models.IntegerField(default=0)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"
        ordering = ("-word_count", "name")
        rebac_resource_type = "tests/hasura_resource_thing"


class HasuraJsonResourceThing(AngeeDataModel):
    """Concrete model used by JSON-path aggregate bridge tests."""

    sqid_prefix = "hjrt_"

    name = models.CharField(max_length=64)
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"
        rebac_resource_type = "tests/hasura_json_resource_thing"


class MeasureOpsThing(AngeeDataModel):
    """Concrete model exercising every curated measure-op field family."""

    sqid_prefix = "mot_"

    count = models.IntegerField(default=0)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    ratio = models.FloatField(default=0.0)
    on_date = models.DateField(null=True)
    at_time = models.DateTimeField(null=True)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class ResourceThingMood(enum.Enum):
    """Synthetic computed enum used by resource field metadata tests."""

    HAPPY = "happy"


ResourceThingMoodType = strawberry.enum(ResourceThingMood)
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Passing a class to strawberry.scalar")
    ResourceThingUnsupportedScalar = strawberry.scalar(
        NewType("ResourceThingUnsupportedScalar", str),
        serialize=str,
        parse_value=str,
    )


def test_resource_field_metadata_has_a_field_owner_module() -> None:
    """The field description is neutral while GraphQL projection stays local."""

    from angee.graphql.data import resource_fields

    assert DataResourceFieldMetadata.__module__ == "angee.data.metadata"
    assert not hasattr(metadata_module, "DataResourceMetadata")
    assert not hasattr(resource_fields, "DataResourceFieldMetadata")
    assert metadata_module.resource_type_name is resource_fields.resource_type_name
    assert metadata_module.resource_wire_field_name is resource_fields.resource_wire_field_name
    assert not hasattr(metadata_module, "_optional_type_name")


def test_resource_subtitle_timestamp_defaults_follow_model_field_flags() -> None:
    """Created/updated facts follow auto_now_add/auto_now, independent of names."""

    @strawberry_django.type(ResourceFlaggedTimestampThing)
    class ResourceFlaggedTimestampThingType:
        label: auto
        born_on: auto
        touched_on: auto

    metadata = make_data_resource_metadata(
        model=ResourceFlaggedTimestampThing,
        node_type=ResourceFlaggedTimestampThingType,
        roots=DataResourceRoots(),
        type_names=DataResourceTypeNames(),
        capabilities=(),
    )

    assert metadata.subtitle == DataResourceSubtitleMetadata(
        created="born_on",
        updated="touched_on",
    )


def test_resource_subtitle_rejects_relation_valued_terminal_field() -> None:
    """A subtitle must name a selectable leaf, never a bare GraphQL object."""

    with pytest.raises(
        ImproperlyConfigured,
        match=(
            "resource metadata for tests.ResourceChild declares subtitle.word_count "
            "selection path 'parent'.*declare a scalar subfield"
        ),
    ):
        make_data_resource_metadata(
            model=ResourceChild,
            node_type=ResourceSubtitleChildType,
            roots=DataResourceRoots(),
            type_names=DataResourceTypeNames(),
            capabilities=(),
            subtitle=DataResourceSubtitleMetadata(word_count="parent"),
        )


def test_hasura_resource_attaches_angee_resource_metadata() -> None:
    """The Hasura builder remains external while Angee owns resource metadata."""

    @strawberry_django.type(HasuraResourceThing)
    class HasuraResourceThingType(AngeeNode):
        name: auto
        word_count: auto
        created_at: auto
        updated_at: auto

    write_backend = type(
        "NoopWriteBackend",
        (),
        {
            "create": lambda self, info, data: None,
            "update": lambda self, info, pk, data: None,
            "delete": lambda self, info, pk: None,
        },
    )()
    resource = hasura_model_resource(
        HasuraResourceThingType,
        model=HasuraResourceThing,
        name="things",
        filterable=["id", "name", "word_count"],
        sortable=["word_count", "name"],
        aggregatable=["id", "word_count"],
        groupable=["name"],
        get_queryset=lambda info: HasuraResourceThing.objects.all(),
        write_backend=write_backend,
        id_decode=lambda value: value,
        subtitle=DataResourceSubtitleMetadata(word_count="word_count"),
    )
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": [resource.query],
                        "mutation": [resource.mutation],
                        "types": [HasuraResourceThingType, *resource.types],
                    }
                }
            )
        ]
    ).build("public")
    metadata = schema.angee_resources[0]
    fields = {field.name: field for field in metadata.fields}

    assert metadata.resource_type == "tests/hasura_resource_thing"
    assert metadata.roots == DataResourceRoots(
        list_name="things",
        detail_name="things_by_pk",
        aggregate_name="things_aggregate",
        group_name="things_groups",
        group_count_name="things_groups_count",
        create_name="insert_things_one",
        update_name="update_things_by_pk",
        delete_name="delete_things_by_pk",
    )
    assert metadata.type_names == DataResourceTypeNames(
        query="things_Query",
        node="HasuraResourceThingType",
        filter="things_bool_exp",
        order="things_order_by",
        aggregate="things_aggregate",
        grouped="things_group",
        group_key="HasuraResourceThingTypeGroupKey",
        group_by_spec="HasuraResourceThingTypeGroupBySpec",
        group_order="HasuraResourceThingTypeGroupOrder",
        having="HasuraResourceThingTypeHaving",
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
    assert metadata.subtitle == DataResourceSubtitleMetadata(
        created="created_at",
        updated="updated_at",
        word_count="word_count",
    )
    assert fields["word_count"].filterable is True
    assert fields["word_count"].sortable is True
    assert fields["word_count"].aggregatable is True
    assert fields["word_count"].creatable is True
    assert fields["word_count"].updatable is True
    serialized = schema._schema.extensions["angee"]["resources"][0]
    assert serialized["roots"]["groupsCount"] == "things_groups_count"
    assert serialized["subtitle"] == {
        "created": "created_at",
        "updated": "updated_at",
        "wordCount": "word_count",
    }
    sdl = schema.as_str()
    assert "word_count" in sdl
    assert "wordCount" not in sdl


def test_hasura_nested_relation_group_dimension_matches_group_key_contract() -> None:
    """A nested FK dimension uses the aggregate builder's attname key and relation shape."""

    @strawberry_django.type(ResourceGrandchild)
    class ResourceGrandchildType(AngeeNode):
        pass

    write_backend = type(
        "NoopWriteBackend",
        (),
        {
            "create": lambda self, info, data: None,
            "update": lambda self, info, pk, data: None,
            "delete": lambda self, info, pk: None,
        },
    )()
    resource = hasura_model_resource(
        ResourceGrandchildType,
        model=ResourceGrandchild,
        name="resource_grandchildren",
        filterable=["id", "child", "child__parent"],
        sortable=["child"],
        aggregatable=["id"],
        groupable=["child", "child__parent"],
        get_queryset=lambda info: ResourceGrandchild.objects.all(),
        write_backend=write_backend,
        id_decode=lambda value: value,
    )
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": [resource.query],
                        "types": [ResourceGrandchildType, *resource.types],
                    }
                }
            )
        ]
    ).build("public")
    metadata = schema.angee_resources[0]
    nested = {dimension.field: dimension for dimension in metadata.group_dimensions}["child__parent"]
    group_key = schema._schema.get_type(metadata.type_names.group_key)

    assert nested == DataGroupDimensionMetadata(
        field="child__parent",
        input="CHILD__PARENT",
        key="child__parent_id",
        kind="relation",
        scalar="ID",
        filter=DataGroupBucketFilterMetadata(
            kind="equality",
            field="child__parent",
            value_key="child__parent_id",
            lookup="sqid",
        ),
    )
    assert group_key is not None
    assert nested.key in group_key.fields  # type: ignore[attr-defined]
    # The relation label fallback resolves the related model's preferred
    # display column even without a node surface (donor/scalar-id axes).
    assert metadata.relation_axes[0].label_axis == "child__name"


def test_hasura_single_level_relation_group_metadata_is_byte_identical() -> None:
    """The established direct-FK dimension payload stays byte-identical."""

    dimension = _hasura_group_dimensions(ResourceChild, ("parent",), ("parent",))[0]
    payload = json.dumps(dataclasses.asdict(dimension), separators=(",", ":"), sort_keys=True).encode()

    assert payload == (
        b'{"extractions":[],"field":"parent","filter":{"field":"parent","kind":"equality",'
        b'"lookup":"sqid","null_lookup":"isNull","range_key":null,"value_key":"parent_id",'
        b'"value_map":[],"value_transform":null},"input":"PARENT","key":"parent_id",'
        b'"kind":"relation","scalar":"ID"}'
    )


def test_hasura_model_resource_groups_json_path_axes() -> None:
    """Hasura resources can expose allowlisted JSON paths as group axes."""

    @strawberry_django.type(HasuraJsonResourceThing)
    class HasuraJsonResourceThingType(AngeeNode):
        name: auto

    write_backend = type(
        "NoopWriteBackend",
        (),
        {
            "create": lambda self, info, data: None,
            "update": lambda self, info, pk, data: None,
            "delete": lambda self, info, pk: None,
        },
    )()
    resource = hasura_model_resource(
        HasuraJsonResourceThingType,
        model=HasuraJsonResourceThing,
        name="json_things",
        filterable=["id", "name"],
        sortable=["name"],
        aggregatable=["id"],
        groupable=["metadata.mailbox"],
        json_paths={"metadata.mailbox": "str"},
        get_queryset=lambda info: HasuraJsonResourceThing.objects.all(),
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
                        "types": [HasuraJsonResourceThingType, *resource.types],
                    }
                }
            )
        ]
    ).build("public")
    metadata = schema.angee_resources[0]

    assert metadata.group_by_fields == ("metadata.mailbox",)
    assert metadata.group_dimensions == (
        DataGroupDimensionMetadata(
            field="metadata.mailbox",
            input="METADATA__MAILBOX",
            key="metadata__mailbox",
            kind="json",
            filter=DataGroupBucketFilterMetadata(
                kind="equality",
                field="metadata",
                value_key="metadata__mailbox",
                lookup="jsonContains",
                null_lookup=None,
                value_transform="jsonObject:mailbox",
            ),
        ),
    )
    assert "METADATA__MAILBOX" in schema.as_str()
    assert "metadata__mailbox: String" in schema.as_str()


def test_hasura_model_resource_groups_json_path_values(transactional_db: Any) -> None:
    """The generated groups resolver groups by allowlisted JSON path values."""

    del transactional_db

    @strawberry_django.type(HasuraJsonResourceThing)
    class HasuraJsonValuesThingType(AngeeNode):
        name: auto

    write_backend = type(
        "NoopWriteBackend",
        (),
        {
            "create": lambda self, info, data: None,
            "update": lambda self, info, pk, data: None,
            "delete": lambda self, info, pk: None,
        },
    )()
    resource = hasura_model_resource(
        HasuraJsonValuesThingType,
        model=HasuraJsonResourceThing,
        name="json_value_things",
        filterable=["id", "name"],
        sortable=["name"],
        aggregatable=["id"],
        groupable=["metadata.mailbox"],
        json_paths={"metadata.mailbox": "str"},
        get_queryset=lambda info: HasuraJsonResourceThing.objects.all(),
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
                        "types": [HasuraJsonValuesThingType, *resource.types],
                    }
                }
            )
        ]
    ).build("public")
    created = _create_missing_tables((HasuraJsonResourceThing,))
    try:
        with system_context(reason="test.aggregate.json_path_group.seed"):
            HasuraJsonResourceThing.objects.create(name="one", metadata={"mailbox": "INBOX"})
            HasuraJsonResourceThing.objects.create(name="two", metadata={"mailbox": "Sent Messages"})
            HasuraJsonResourceThing.objects.create(name="three", metadata={"mailbox": "INBOX"})

        with system_context(reason="test.aggregate.json_path_group.query"):
            result = result_data(
                execute_schema(
                    schema,
                    """
                    query MailboxGroups($groupBy: [HasuraJsonValuesThingTypeGroupBySpec!]!) {
                      json_value_things_groups_count(group_by: $groupBy)
                      json_value_things_groups(group_by: $groupBy, limit: 10) {
                        key { metadata__mailbox }
                        aggregate { count }
                      }
                    }
                    """,
                    {"groupBy": [{"field": "METADATA__MAILBOX"}]},
                )
            )
    finally:
        _clear_model_tables((HasuraJsonResourceThing,))
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)

    assert result["json_value_things_groups_count"] == 2
    assert sorted(
        result["json_value_things_groups"],
        key=lambda row: row["key"]["metadata__mailbox"] or "",
    ) == [
        {"key": {"metadata__mailbox": "INBOX"}, "aggregate": {"count": 2}},
        {"key": {"metadata__mailbox": "Sent Messages"}, "aggregate": {"count": 1}},
    ]


def test_measure_ops_pin_the_curated_subset_per_field_family() -> None:
    """Curated aggregate ops stay frozen so an upstream op-list change cannot widen them.

    The op vocabulary per Django type is owned by ``default_operators_for``, but
    Angee advertises only the curated ``(sum, avg, min, max)`` subset in curated
    order. This pins the resolved output for every curated field family so a
    widening upstream (or a curation drift) fails loudly instead of silently
    growing the advertised ops / the order-sensitive ``aggregate_measures`` JSON.
    """

    expected = {
        "count": ("sum", "avg", "min", "max"),
        "amount": ("sum", "avg", "min", "max"),
        "ratio": ("sum", "avg", "min", "max"),
        "on_date": ("min", "max"),
        "at_time": ("min", "max"),
    }
    resolved = {name: _measure_ops_for_field(MeasureOpsThing._meta.get_field(name)) for name in expected}

    assert resolved == expected


def test_data_resource_metadata_requires_direct_relation_axis_for_relation_label() -> None:
    """A relation label axis only describes a bucket when the relation id axis exists."""

    @strawberry_django.type(ResourceChild)
    class ResourceChildInvalidRelationType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="requires matching direct relation"):
        make_data_resource_metadata(
            model=ResourceChild,
            roots=DataResourceRoots(list_name="children", group_name="children_groups"),
            type_names=DataResourceTypeNames(node="ResourceChildInvalidRelationType"),
            capabilities=("list", "groups"),
            node_type=ResourceChildInvalidRelationType,
            group_by_fields=("parent__name",),
        )


def test_data_resource_metadata_rejects_multiple_relation_label_axes() -> None:
    """One direct relation bucket gets one label axis in metadata."""

    @strawberry_django.type(ResourceChild)
    class ResourceChildAmbiguousRelationType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="multiple label axes"):
        make_data_resource_metadata(
            model=ResourceChild,
            roots=DataResourceRoots(list_name="children", group_name="children_groups"),
            type_names=DataResourceTypeNames(node="ResourceChildAmbiguousRelationType"),
            capabilities=("list", "groups"),
            node_type=ResourceChildAmbiguousRelationType,
            group_by_fields=("parent", "parent__name", "parent__created_at"),
        )


def test_data_resource_metadata_rejects_duplicate_group_axes() -> None:
    """Duplicate backend group declarations must fail before artifact emission."""

    @strawberry_django.type(ResourceThing)
    class ResourceThingDuplicateGroupType:
        name: auto

    with pytest.raises(ImproperlyConfigured, match="duplicate group axis 'name'"):
        make_data_resource_metadata(
            model=ResourceThing,
            roots=DataResourceRoots(list_name="things", group_name="things_groups"),
            type_names=DataResourceTypeNames(node="ResourceThingDuplicateGroupType"),
            capabilities=("list", "groups"),
            node_type=ResourceThingDuplicateGroupType,
            group_by_fields=("name", "name"),
        )


def test_data_resource_metadata_rejects_duplicate_field_metadata() -> None:
    """Resource field metadata names are authoritative and must be unique."""

    with pytest.raises(ImproperlyConfigured, match="duplicate resource field 'name'"):
        make_data_resource_metadata(
            model=ResourceThing,
            roots=DataResourceRoots(list_name="things"),
            type_names=DataResourceTypeNames(node="ResourceThingType"),
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
            model=ResourceThing,
            roots=DataResourceRoots(list_name="things"),
            type_names=DataResourceTypeNames(node="ResourceThingType"),
            capabilities=("list",),
            fields=(field,),
        )


def test_data_resource_metadata_rejects_generated_duplicate_field_names() -> None:
    """Generated resource field metadata must not emit duplicate wire names."""

    @strawberry_django.type(ResourceThing)
    class ResourceThingDuplicateFieldType:
        name: auto

        @strawberry.field(name="name")
        def name_copy(self) -> str:
            return self.name

    with pytest.raises(ImproperlyConfigured, match="duplicate resource field 'name'"):
        make_data_resource_metadata(
            model=ResourceThing,
            roots=DataResourceRoots(list_name="things"),
            type_names=DataResourceTypeNames(node="ResourceThingDuplicateFieldType"),
            capabilities=("list",),
            node_type=ResourceThingDuplicateFieldType,
        )


def test_data_resource_metadata_marks_public_id_field_as_id_scalar() -> None:
    """The GraphQL public id is an ID boundary, not the model's integer pk."""

    @strawberry_django.type(ResourceThing)
    class ResourceThingNodeType(AngeeNode):
        name: auto

    resource = make_data_resource_metadata(
        model=ResourceThing,
        roots=DataResourceRoots(list_name="things"),
        type_names=DataResourceTypeNames(node="ResourceThingNodeType"),
        capabilities=("list",),
        node_type=ResourceThingNodeType,
    )
    fields = {field.name: field for field in resource.fields}

    assert fields["id"].kind == "scalar"
    assert fields["id"].scalar == "ID"
    assert fields["id"].widget is None


def test_model_resource_metadata_marks_decimal_fields_as_decimal_scalar() -> None:
    """Model-field metadata keeps Decimal distinct from Float."""

    fields = {
        field.name: field
        for field in metadata_module.model_resource_fields(
            MeasureOpsThing,
            ("amount", "ratio"),
            filter_fields=("amount", "ratio"),
            order_fields=("amount", "ratio"),
            aggregate_fields=("amount", "ratio"),
        )
    }

    assert fields["amount"].kind == "scalar"
    assert fields["amount"].scalar == "Decimal"
    assert fields["amount"].widget == "float"
    assert fields["amount"].filterable is True
    assert fields["amount"].sortable is True
    assert fields["amount"].aggregatable is True
    assert fields["ratio"].scalar == "Float"


def test_surface_resource_metadata_marks_decimal_fields_as_decimal_scalar() -> None:
    """Strawberry Decimal surfaces keep the same metadata scalar."""

    @strawberry_django.type(MeasureOpsThing)
    class MeasureOpsThingType(AngeeNode):
        amount: auto
        ratio: auto

        @strawberry.field
        def computed_amount(self) -> Decimal:
            return self.amount

    resource = make_data_resource_metadata(
        model=MeasureOpsThing,
        roots=DataResourceRoots(list_name="measure_ops"),
        type_names=DataResourceTypeNames(node="MeasureOpsThingType"),
        capabilities=("list",),
        node_type=MeasureOpsThingType,
    )
    fields = {field.name: field for field in resource.fields}

    assert fields["amount"].scalar == "Decimal"
    assert fields["amount"].widget == "float"
    assert fields["computed_amount"].scalar == "Decimal"
    assert fields["computed_amount"].widget is None
    assert fields["ratio"].scalar == "Float"


def test_data_resource_metadata_marks_computed_surface_enum_field() -> None:
    """Strawberry enum surfaces own enum field classification."""

    @strawberry_django.type(ResourceThing)
    class ResourceThingComputedEnumType(AngeeNode):
        name: auto

        @strawberry.field
        def mood(self) -> ResourceThingMoodType:
            return ResourceThingMood.HAPPY

    resource = make_data_resource_metadata(
        model=ResourceThing,
        roots=DataResourceRoots(list_name="things"),
        type_names=DataResourceTypeNames(node="ResourceThingComputedEnumType"),
        capabilities=("list",),
        node_type=ResourceThingComputedEnumType,
    )
    fields = {field.name: field for field in resource.fields}

    assert fields["mood"].kind == "enum"
    assert fields["mood"].scalar is None


def test_data_resource_metadata_marks_forward_object_field_as_relation() -> None:
    """Unresolved object return types are relation-shaped, not scalar guesses."""

    @strawberry_django.type(ResourceThing)
    class ResourceThingForwardRelationType(AngeeNode):
        name: auto

        @strawberry.field
        def parent(self) -> ResourceParentForwardTargetType | None:  # type: ignore[name-defined]
            return None

    resource = make_data_resource_metadata(
        model=ResourceThing,
        roots=DataResourceRoots(list_name="things"),
        type_names=DataResourceTypeNames(node="ResourceThingForwardRelationType"),
        capabilities=("list",),
        node_type=ResourceThingForwardRelationType,
    )

    @strawberry_django.type(ResourceParent)
    class ResourceParentForwardTargetType(AngeeNode):
        name: auto

    fields = {field.name: field for field in resource.fields}

    assert fields["parent"].kind == "relation"
    assert fields["parent"].scalar is None


def test_data_resource_metadata_rejects_unsupported_surface_scalar() -> None:
    """Scalar resource fields must have a supported metadata scalar family."""

    @strawberry_django.type(ResourceThing)
    class ResourceThingUnsupportedScalarType(AngeeNode):
        name: auto

        @strawberry.field
        def mystery(self) -> ResourceThingUnsupportedScalar:
            return ResourceThingUnsupportedScalar("mystery")

    with pytest.raises(
        ImproperlyConfigured,
        match="cannot classify GraphQL scalar for field 'mystery' \\(ResourceThingUnsupportedScalar\\)",
    ):
        make_data_resource_metadata(
            model=ResourceThing,
            roots=DataResourceRoots(list_name="things"),
            type_names=DataResourceTypeNames(node="ResourceThingUnsupportedScalarType"),
            capabilities=("list",),
            node_type=ResourceThingUnsupportedScalarType,
        )


def test_data_resource_metadata_marks_to_many_node_fields_as_lists() -> None:
    """Resource fields must not describe to-many object lists as to-one relations."""

    @strawberry_django.type(ResourceParent)
    class ResourceParentListFieldType:
        name: auto

    @strawberry_django.type(ResourceChild)
    class ResourceChildListFieldType:
        name: auto
        related_parents: list[ResourceParentListFieldType]

    resource = make_data_resource_metadata(
        model=ResourceChild,
        roots=DataResourceRoots(list_name="children"),
        type_names=DataResourceTypeNames(node="ResourceChildListFieldType"),
        capabilities=("list",),
        node_type=ResourceChildListFieldType,
    )
    fields = {field.name: field for field in resource.fields}

    assert fields["related_parents"].kind == "list"
    assert fields["related_parents"].scalar is None
    assert fields["related_parents"].widget is None


def test_data_resource_metadata_marks_plain_relation_targets() -> None:
    """Object relations expose their target model even when they are not group axes."""

    @strawberry_django.type(ResourceParent)
    class ResourceParentRelationType:
        name: auto

    @strawberry_django.type(ResourceChild)
    class ResourceChildRelationType:
        name: auto
        parent: ResourceParentRelationType

    resource = make_data_resource_metadata(
        model=ResourceChild,
        roots=DataResourceRoots(list_name="children"),
        type_names=DataResourceTypeNames(node="ResourceChildRelationType"),
        capabilities=("list",),
        node_type=ResourceChildRelationType,
    )
    fields = {field.name: field for field in resource.fields}

    assert fields["parent"].kind == "relation"
    assert fields["parent"].widget == "many2one"
    assert fields["parent"].relation_model_label == "tests.ResourceParent"
    assert fields["parent"].relation_label_axis is None


@pytest.mark.parametrize(
    ("groupable", "aggregatable"),
    [
        pytest.param(["name__missing"], ["id"], id="unknown-group-path"),
        pytest.param(["related_parents"], ["id"], id="to-many-group-axis"),
        pytest.param(["name"], ["id", "name__missing"], id="unknown-measure-path"),
        pytest.param(["name"], ["id", "related_parents__name"], id="to-many-measure-path"),
    ],
)
def test_hasura_resource_rejects_unresolvable_axis_paths(
    groupable: list[str],
    aggregatable: list[str],
) -> None:
    """A group/aggregate axis path that does not resolve to a column fails the build.

    Path resolution is owned by ``strawberry-django-aggregates`` (unknown and
    to-many measure paths) and Angee's groupable guard (to-many group axes);
    either way the misconfiguration fails fast at build time rather than emitting
    a broken resource.
    """

    @strawberry_django.type(ResourceChild)
    class ResourceChildResourceType(AngeeNode):
        name: auto

    write_backend = type(
        "NoopWriteBackend",
        (),
        {
            "create": lambda self, info, data: None,
            "update": lambda self, info, pk, data: None,
            "delete": lambda self, info, pk: None,
        },
    )()
    with pytest.raises((ImproperlyConfigured, FieldDoesNotExist, GroupByFieldNotAllowed)):
        hasura_model_resource(
            ResourceChildResourceType,
            model=ResourceChild,
            name="children",
            filterable=["id", "name"],
            sortable=["name"],
            aggregatable=aggregatable,
            groupable=groupable,
            get_queryset=lambda info: ResourceChild.objects.all(),
            write_backend=write_backend,
            id_decode=lambda value: value,
        )


@pytest.fixture()
def relation_filter_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete relation-chain tables used by filter tests."""

    del transactional_db
    models_under_test = (ResourceParent, ResourceChild, ResourceGrandchild)
    created = _create_missing_tables(models_under_test)
    try:
        yield
    finally:
        _clear_model_tables(models_under_test)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def test_relation_filter_decoders_covers_public_id_relations_only() -> None:
    """Filterable to-one relation columns get an auto decoder; scalars and the id do not."""

    decoders = _relation_filter_decoders(
        ResourceChild,
        filterable=["id", "name", "parent"],
        declared=None,
    )
    assert decoders is not None
    assert set(decoders) == {"parent"}


def test_relation_filter_decoders_never_overrides_a_declared_decoder() -> None:
    """A caller-declared field decoder wins over the auto-derived one."""

    sentinel = lambda value: value  # noqa: E731 - test double
    decoders = _relation_filter_decoders(
        ResourceChild,
        filterable=["parent"],
        declared={"parent": sentinel},
    )
    assert decoders is not None
    assert decoders["parent"] is sentinel


def test_filterable_relation_filters_by_public_id_without_field_id_decode(
    relation_filter_tables: None,
) -> None:
    """A child list filters by parent sqid even when the resource declares no field_id_decode."""

    @strawberry_django.type(ResourceChild)
    class ResourceChildFilterType(AngeeNode):
        name: auto

        @strawberry_django.field(only=["parent_id"])
        def parent(self) -> strawberry.ID:
            """Return the parent's public id."""

            return require_public_id(ResourceParent, cast(Any, self).parent_id)

    resource = hasura_model_resource(
        ResourceChildFilterType,
        model=ResourceChild,
        name="resource_children",
        filterable=["id", "name", "parent"],
        sortable=["name"],
        aggregatable=["id"],
        insert=False,
        update=False,
        delete=False,
        get_queryset=lambda info: ResourceChild._base_manager.all(),
        id_column="sqid",
    )
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": [resource.query],
                        "types": [ResourceChildFilterType, *resource.types],
                    }
                }
            )
        ]
    ).build("public")

    first = ResourceParent._base_manager.create(name="First")
    second = ResourceParent._base_manager.create(name="Second")
    ResourceChild._base_manager.create(name="under-first", parent=first)
    ResourceChild._base_manager.create(name="under-second", parent=second)

    rows = result_data(
        execute_schema(
            schema,
            """
            query ChildrenOf($parent: String!) {
              resource_children(where: {parent: {_eq: $parent}}) { name }
            }
            """,
            {"parent": str(first.sqid)},
        )
    )["resource_children"]
    assert [row["name"] for row in rows] == ["under-first"]


def test_nested_relation_group_key_filters_its_bucket_rows(
    relation_filter_tables: None,
) -> None:
    """A nested relation bucket sqid decodes through its full filter path."""

    @strawberry_django.type(ResourceGrandchild)
    class ResourceGrandchildFilterType(AngeeNode):
        pass

    resource = hasura_model_resource(
        ResourceGrandchildFilterType,
        model=ResourceGrandchild,
        name="resource_grandchildren",
        filterable=["id", "child__parent"],
        sortable=["id"],
        aggregatable=["id"],
        groupable=["child__parent"],
        insert=False,
        update=False,
        delete=False,
        field_id_decode={"child__parent": public_pk_decoder(ResourceParent)},
        get_queryset=lambda info: ResourceGrandchild._base_manager.all(),
        id_column="sqid",
    )
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": [resource.query],
                        "types": [ResourceGrandchildFilterType, *resource.types],
                    }
                }
            )
        ]
    ).build("public")

    first_parent = ResourceParent._base_manager.create(name="First")
    second_parent = ResourceParent._base_manager.create(name="Second")
    first_child = ResourceChild._base_manager.create(name="first-child", parent=first_parent)
    second_child = ResourceChild._base_manager.create(name="second-child", parent=second_parent)
    first_rows = [
        ResourceGrandchild._base_manager.create(child=first_child),
        ResourceGrandchild._base_manager.create(child=first_child),
    ]
    ResourceGrandchild._base_manager.create(child=second_child)

    groups = result_data(
        execute_schema(
            schema,
            """
            query {
              resource_grandchildren_groups(group_by: [{field: CHILD__PARENT}]) {
                key { child__parent_id }
                aggregate { count }
              }
            }
            """,
        )
    )["resource_grandchildren_groups"]
    bucket = next(group for group in groups if group["key"]["child__parent_id"] == str(first_parent.sqid))
    bucket_key = bucket["key"]["child__parent_id"]

    assert bucket["aggregate"]["count"] == 2
    assert bucket_key == str(first_parent.sqid)

    rows = result_data(
        execute_schema(
            schema,
            """
            query GrandchildrenInBucket($where: resource_grandchildren_bool_exp) {
              resource_grandchildren(where: $where, order_by: [{id: asc}]) { id }
            }
            """,
            {"where": {"child__parent": {"_eq": bucket_key}}},
        )
    )["resource_grandchildren"]
    assert [row["id"] for row in rows] == [str(row.sqid) for row in first_rows]


def test_non_pk_relation_group_identity_fails_at_build() -> None:
    """Natural target columns cannot accidentally be encoded as primary keys."""

    class NamedTarget(models.Model):
        code = models.CharField(max_length=32, unique=True)

        class Meta:
            app_label = "tests"

    class NamedSource(models.Model):
        target = models.ForeignKey(NamedTarget, to_field="code", on_delete=models.CASCADE)

        class Meta:
            app_label = "tests"

    with pytest.raises(ImproperlyConfigured, match="non-primary identity"):
        _relation_group_key_encoders(NamedSource, ["target"])


def test_interleaved_json_resources_do_not_replace_upstream_builders(monkeypatch: Any) -> None:
    """Concurrent schema composition keeps each JSON allowlist resource-local."""

    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from strawberry_django_aggregates import AggregateBuilder
    from strawberry_django_hasura import resource as hasura_owner

    barrier = Barrier(2)
    original_build = AggregateBuilder.build

    def interleaved_build(builder: Any) -> Any:
        barrier.wait(timeout=10)
        assert hasura_owner.AggregateBuilder is AggregateBuilder
        return original_build(builder)

    monkeypatch.setattr(AggregateBuilder, "build", interleaved_build)

    def build_resource(name: str, path: str) -> Any:
        node = strawberry_django.type(HasuraJsonResourceThing, name=name)(
            type(name, (AngeeNode,), {"__annotations__": {"name": str}})
        )
        return hasura_model_resource(
            node,
            model=HasuraJsonResourceThing,
            name=name.lower(),
            filterable=["id", "name"],
            sortable=["name"],
            aggregatable=["id"],
            groupable=[path],
            json_paths={path: "str"},
            insert=False,
            update=False,
            delete=False,
            get_queryset=lambda info: HasuraJsonResourceThing._base_manager.all(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(build_resource, "MailboxInterleave", "metadata.mailbox")
        second = pool.submit(build_resource, "RegionInterleave", "metadata.region")
        mailbox, region = first.result(timeout=15), second.result(timeout=15)
    assert "metadata__mailbox" in mailbox.group_key_type.__annotations__
    assert "metadata__region" not in mailbox.group_key_type.__annotations__
    assert "metadata__region" in region.group_key_type.__annotations__
    assert "metadata__mailbox" not in region.group_key_type.__annotations__
