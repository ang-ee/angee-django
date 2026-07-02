"""Tests for Hasura resource metadata and aggregate contracts."""

from __future__ import annotations

import enum
import warnings
from typing import NewType

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry import auto
from strawberry_django_aggregates.errors import GroupByFieldNotAllowed

from angee.base.models import AngeeDataModel
from angee.graphql.data import hasura_model_resource
from angee.graphql.data import metadata as metadata_module
from angee.graphql.data.hasura import _measure_ops_for_field
from angee.graphql.data.metadata import (
    DataAggregateMeasureMetadata,
    DataResourceFieldMetadata,
    DataResourceRoots,
    DataResourceTypeNames,
    make_data_resource_metadata,
)
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import SchemaAddon


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


class ResourceTimedThing(AngeeDataModel):
    """Concrete model with a field class not supported by resource metadata."""

    sqid_prefix = "rtt_"

    duration = models.DurationField()

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
    """Resource field metadata/classification lives outside the resource envelope module."""

    from angee.graphql.data import resource_fields

    assert DataResourceFieldMetadata.__module__ == "angee.graphql.data.resource_fields"
    assert metadata_module.resource_type_name is resource_fields.resource_type_name
    assert metadata_module.resource_wire_field_name is resource_fields.resource_wire_field_name
    assert not hasattr(metadata_module, "_optional_type_name")


def test_hasura_resource_attaches_angee_resource_metadata() -> None:
    """The Hasura builder remains external while Angee owns resource metadata."""

    @strawberry_django.type(HasuraResourceThing)
    class HasuraResourceThingType(AngeeNode):
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
    assert fields["word_count"].filterable is True
    assert fields["word_count"].sortable is True
    assert fields["word_count"].aggregatable is True
    assert fields["word_count"].creatable is True
    assert fields["word_count"].updatable is True
    sdl = schema.as_str()
    assert "word_count" in sdl
    assert "wordCount" not in sdl


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
    resolved = {
        name: _measure_ops_for_field(MeasureOpsThing._meta.get_field(name)) for name in expected
    }

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
