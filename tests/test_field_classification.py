"""Native resource field classification boundary regressions."""

from __future__ import annotations

from typing import cast

import strawberry
from django.db import models

from angee.base.fields import ColorField, StateField
from angee.data.field_classification import RESOURCE_FIELD_SCALARS, is_to_one_relation, resource_field_widget
from angee.data.metadata import DataResourceRoots, DataResourceTypeNames, serialize_data_resources
from angee.graphql.data.metadata import _finalize_data_resource


def test_uuid_is_a_supported_resource_scalar() -> None:
    assert "UUID" in RESOURCE_FIELD_SCALARS


def test_generic_relation_without_one_fixed_model_is_not_a_to_one_axis() -> None:
    generic = cast(models.Field, type("GenericRelation", (), {
        "many_to_one": True,
        "one_to_one": False,
        "related_model": None,
    })())
    assert is_to_one_relation(generic) is False


def test_generated_field_projects_as_its_output_field_scalar() -> None:
    from angee.data.field_classification import model_field_scalar

    field = models.GeneratedField(
        expression=models.Q(active=True),
        output_field=models.BooleanField(),
        db_persist=True,
    )
    assert model_field_scalar(field) == "Boolean"


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

    [artifact] = serialize_data_resources((metadata,), schema_name="console")
    fields = cast("list[dict[str, object]]", artifact["fields"])
    status = next(field for field in fields if field["name"] == "status")

    assert status["statusDisplay"] == "dot"
