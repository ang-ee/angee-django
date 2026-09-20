"""Tests for the data-surface description contract without GraphQL producers."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import models

from angee.data.metadata import (
    DataLinesMetadata,
    DataQueryField,
    DataQueryFilter,
    DataQueryIdentity,
    DataQueryValueMap,
    DataResourceFieldMetadata,
    DataResourceMetadata,
    DataResourceQuery,
    DataResourceRoots,
    DataResourceSubtitleMetadata,
    DataResourceTypeNames,
    serialize_data_resources,
)


def test_final_resource_description_serializes_without_projection_types() -> None:
    """The final declaration owns aliases and exclusions throughout the envelope."""

    title_field = DataResourceFieldMetadata(
        name="title", kind="scalar", scalar="String", model_field_name="internal_title"
    )
    status_field = DataResourceFieldMetadata(
        name="status",
        kind="enum",
        required_on_create=True,
    )

    final = DataResourceMetadata(
        model=models.Model,
        model_label="catalog.item",
        resource_type=None,
        app_label="catalog",
        model_name="item",
        query=DataResourceQuery(identity=DataQueryIdentity("id")),
        roots=DataResourceRoots(list_name="catalog_items", detail_name="catalog_item"),
        type_names=DataResourceTypeNames(node="CatalogItem", filter="catalog_items_bool_exp"),
        contributors=("CatalogItemQuery", "CatalogItemMutation"),
        capabilities=("list", "detail", "create"),
        fields=(title_field, status_field),
        subtitle=DataResourceSubtitleMetadata(created="created_at", word_count="body.word_count"),
        lines=DataLinesMetadata(field="items", model_label="catalog.line", fields=(title_field,)),
    )

    [wire] = serialize_data_resources((final,), schema_name="console")
    assert wire["schemaName"] == "console"
    assert wire["modelLabel"] == "catalog.item"
    assert wire["query"]["identity"]["field"] == "id"
    assert wire["resourceType"] is None
    assert wire["canonicalLabel"] is None
    assert wire["roots"] == {
        "list": "catalog_items",
        "detail": "catalog_item",
        "aggregate": None,
        "groups": None,
        "groupsCount": None,
        "create": None,
        "update": None,
        "save": None,
        "delete": None,
        "deletePreview": None,
        "revisions": None,
        "changes": None,
    }
    assert wire["fields"][1]["requiredOnCreate"] is True
    assert "modelFieldName" not in wire["fields"][0]
    assert wire["subtitle"] == {"created": "created_at", "updated": None, "wordCount": "body.word_count"}
    assert wire["linesResource"]["modelLabel"] == "catalog.line"
    assert wire["linesResource"]["fields"] == [wire["fields"][0]]
    assert wire["linesResource"]["defaults"] == {}
    assert wire["aggregateFields"] == []
    assert wire["query"]["axes"] == {}
    assert wire["query"]["sort"] == {"default": []}
    assert {"model", "contributors", "nodeType", "filterType", "orderType"}.isdisjoint(wire)


def test_resource_query_values_use_native_json_serialization() -> None:
    """Nested declarations use aliases while arbitrary value keys remain unchanged."""

    final = DataResourceMetadata(
        model=None,
        model_label="catalog.item",
        resource_type=None,
        app_label="catalog",
        model_name="item",
        query=DataResourceQuery(
            identity=DataQueryIdentity("public_id"),
            fields={
                "created_at": DataQueryField(
                    kind="scalar",
                    filter=DataQueryFilter(
                        field="created_at",
                        operators=("eq",),
                        scalar="Date",
                        value_map=(
                            DataQueryValueMap(
                                from_value={
                                    "booked_on": date(2026, 9, 20),
                                    "amount": Decimal("12.50"),
                                    "record_ids": (UUID("00000000-0000-0000-0000-000000000001"),),
                                },
                                to_value=False,
                            ),
                            DataQueryValueMap(from_value=None, to_value=0),
                        ),
                    ),
                ),
            },
        ),
        roots=DataResourceRoots(),
        type_names=DataResourceTypeNames(),
    )

    wire = final.as_wire(schema_name="console")

    assert wire["query"]["identity"] == {"field": "public_id"}
    [field_name] = wire["query"]["fields"]
    assert field_name == "created_at"
    assert wire["query"]["fields"][field_name]["filter"]["valueMap"] == [
        {
            "from": {
                "booked_on": "2026-09-20",
                "amount": "12.50",
                "record_ids": ["00000000-0000-0000-0000-000000000001"],
            },
            "to": False,
        },
        {"from": None, "to": 0},
    ]
    assert wire["linesResource"] is None
