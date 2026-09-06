"""Tests for the data-surface description contract without GraphQL producers."""

from angee.data.metadata import (
    DataResourceFieldMetadata,
    DataResourceMetadata,
    DataResourceRoots,
    DataResourceSubtitleMetadata,
    DataResourceTypeNames,
    _metadata_key,
    serialize_data_resources,
)


def test_final_resource_description_serializes_without_projection_types() -> None:
    """The sole final neutral description retains its historical wire envelope."""

    title_field = DataResourceFieldMetadata(
        name="title", kind="scalar", scalar="String", sortable=True, filterable=True
    )
    status_field = DataResourceFieldMetadata(
        name="status",
        kind="enum",
        required_on_create=True,
    )

    final = DataResourceMetadata(
        model=None,
        model_label="catalog.item",
        resource_type=None,
        app_label="catalog",
        model_name="item",
        public_id_field="id",
        roots=DataResourceRoots(list_name="catalog_items", detail_name="catalog_item"),
        type_names=DataResourceTypeNames(node="CatalogItem", filter="catalog_items_bool_exp"),
        contributors=("CatalogItemQuery", "CatalogItemMutation"),
        capabilities=("list", "detail", "create"),
        fields=(title_field, status_field),
        subtitle=DataResourceSubtitleMetadata(created="created_at", word_count="body.word_count"),
    )

    [wire] = serialize_data_resources((final,), schema_name="console")
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
