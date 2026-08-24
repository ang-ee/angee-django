"""Tests for the data-surface description contract without GraphQL producers."""

from angee.data.metadata import (
    DataResourceFieldMetadata,
    DataResourceMetadata,
    DataResourceRoots,
    DataResourceSubtitleMetadata,
    DataResourceTypeNames,
    _metadata_key,
    merge_data_resources,
    merge_resource_fields,
    serialize_data_resources,
)


def test_resource_descriptions_merge_and_serialize_without_projection_types() -> None:
    """Neutral descriptions compose and retain their historical wire envelope."""

    left_field = DataResourceFieldMetadata(name="title", kind="scalar", scalar="String", sortable=True)
    right_field = DataResourceFieldMetadata(name="title", kind="scalar", filterable=True)
    status_field = DataResourceFieldMetadata(
        name="status",
        kind="enum",
        required_on_create=True,
    )

    fields = merge_resource_fields((left_field,), (right_field, status_field))
    assert tuple(field.name for field in fields) == ("title", "status")
    assert fields[0].sortable is True
    assert fields[0].filterable is True

    left = DataResourceMetadata(
        model=None,
        model_label="catalog.item",
        resource_type=None,
        app_label="catalog",
        model_name="item",
        public_id_field="id",
        roots=DataResourceRoots(list_name="catalog_items"),
        type_names=DataResourceTypeNames(node="CatalogItem"),
        contributors=("CatalogItemQuery",),
        capabilities=("detail",),
        fields=(left_field,),
        subtitle=DataResourceSubtitleMetadata(created="created_at"),
        node_type=object,
    )
    right = DataResourceMetadata(
        model=None,
        model_label="catalog.item",
        resource_type=None,
        app_label="catalog",
        model_name="item",
        public_id_field="id",
        roots=DataResourceRoots(detail_name="catalog_item"),
        type_names=DataResourceTypeNames(filter="catalog_items_bool_exp"),
        contributors=("CatalogItemMutation",),
        capabilities=("list", "create"),
        fields=(right_field, status_field),
        subtitle=DataResourceSubtitleMetadata(word_count="body.word_count"),
        filter_type=object,
        order_type=object,
    )

    [merged] = merge_data_resources((left, right))
    assert merged.roots == DataResourceRoots(
        list_name="catalog_items",
        detail_name="catalog_item",
    )
    assert merged.capabilities == ("list", "detail", "create")
    assert merged.fields == fields
    assert merged.subtitle == DataResourceSubtitleMetadata(
        created="created_at",
        word_count="body.word_count",
    )

    [wire] = serialize_data_resources((merged,), schema_name="console")
    assert wire["schemaName"] == "console"
    assert wire["modelLabel"] == "catalog.item"
    assert wire["publicIdField"] == "id"
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
    assert {"model", "contributors", "nodeType", "filterType", "orderType"}.isdisjoint(wire)


def test_metadata_keys_match_the_historical_envelope_casing() -> None:
    """Envelope field names use the contract's stable camelCase conversion."""

    assert _metadata_key("model_label") == "modelLabel"
    assert _metadata_key("delete_preview_name") == "deletePreviewName"
    assert _metadata_key("already") == "already"
