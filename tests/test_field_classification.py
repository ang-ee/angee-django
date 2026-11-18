"""Native resource field classification boundary regressions."""

from __future__ import annotations

from typing import cast

import strawberry
import strawberry_django
from django.db import models
from strawberry import auto

from angee.base.fields.state import StateField
from angee.base.fields.text import ColorField
from angee.base.models.base import AngeeDataModel
from angee.graphql.data.contracts import DataQueryOrder, DataResourceRoots, DataResourceTypeNames
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.data.metadata import _finalize_data_resource
from angee.graphql.data.resource_fields import RESOURCE_FIELD_SCALARS, resource_field_widget
from angee.graphql.introspection import is_to_one_relation
from angee.graphql.node import AngeeNode
from tests.conftest import addon_schema


class ClassifiedTimeSlot(AngeeDataModel):
    """A model resource carrying a native time-of-day column."""

    sqid_prefix = "cts_"

    starts = models.TimeField()

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"
        rebac_resource_type = "tests/classified_time_slot"


def test_uuid_is_a_supported_resource_scalar() -> None:
    assert "UUID" in RESOURCE_FIELD_SCALARS


def test_time_field_projects_the_time_scalar_widget_filters_and_sort() -> None:
    """A Django TimeField reads, writes, filters and sorts as GraphQL ``Time`` with Date's operators."""

    @strawberry_django.type(ClassifiedTimeSlot)
    class ClassifiedTimeSlotType(AngeeNode):
        starts: auto

    resource = hasura_model_resource(
        ClassifiedTimeSlotType,
        model=ClassifiedTimeSlot,
        name="time_slots",
        filterable=["id", "starts"],
        sortable=["starts"],
        aggregatable=["id"],
        groupable=["starts"],
        get_queryset=lambda info: ClassifiedTimeSlot.objects.all(),
    )
    schema = addon_schema(
        {"public": {"query": [resource.query], "mutation": [resource.mutation],
                    "types": [ClassifiedTimeSlotType, *resource.types]}},
        "public",
    )
    [metadata] = schema.angee_resources
    starts = next(field for field in metadata.fields if field.name == "starts")
    query = metadata.query.fields["starts"]

    assert (starts.kind, starts.scalar, starts.widget) == ("scalar", "Time", "time")
    assert starts.creatable and starts.updatable and starts.required_on_create
    assert query.scalar == "Time"
    assert query.filter is not None
    assert query.filter.scalar == "Time"
    assert query.filter.operators == ("exact", "ne", "gt", "gte", "lt", "lte", "inList", "notInList", "isNull")
    assert query.sort == DataQueryOrder("starts")
    # A time of day groups by value; calendar extractions belong to Date and DateTime.
    assert metadata.query.axes["starts"].kind == "column"
    assert metadata.query.axes["starts"].extractions == ()


def test_generic_relation_without_one_fixed_model_is_not_a_to_one_axis() -> None:
    generic = cast(models.Field, type("GenericRelation", (), {
        "many_to_one": True,
        "one_to_one": False,
        "related_model": None,
    })())
    assert is_to_one_relation(generic) is False




def test_color_field_classifies_to_the_color_widget() -> None:
    """A colour column renders the shared chooser without the owner naming a widget."""

    field = ColorField(blank=True, default="")
    assert field.max_length == 7
    assert resource_field_widget(field, "scalar", scalar="String") == "color"
    assert resource_field_widget(models.CharField(max_length=7), "scalar", scalar="String") is None


def test_state_field_status_display_reaches_the_resource_artifact() -> None:
    """A field-owned status shape is classified into the serialized frontend fact."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"

    class StatusRecord(models.Model):
        status = StateField(choices_enum=Status, status_display="dot")

        class Meta:
            app_label = "tests"

    @strawberry.type(name="ClassifiedStatusRecord")
    class StatusRecordType:
        id: strawberry.ID
        status: str

    @strawberry.type
    class StatusProbeQuery:
        ready: bool = True

    metadata = _finalize_data_resource(
        graphql_schema=strawberry.Schema(
            query=StatusProbeQuery,
            types=[StatusRecordType],
        )._schema,
        model=StatusRecord,
        roots=DataResourceRoots(list_name="classified_status_records"),
        type_names=DataResourceTypeNames(
            query="classified_status_records_Query",
            node="ClassifiedStatusRecord",
        ),
        capabilities=("list",),
        public_id_field="id",
        filter_fields=("id", "status"),
    )

    [artifact] = [item.as_wire(schema_name="console") for item in (metadata,)]
    fields = cast("list[dict[str, object]]", artifact["fields"])
    status = next(field for field in fields if field["name"] == "status")

    assert status["statusDisplay"] == "dot"
