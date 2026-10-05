"""Stage 2: ``hasura_pydantic_resource`` — a Hasura resource from a pydantic model.

A computed source (no Django model) presents the same list / aggregate(count) /
by-pk surface as a model resource and attaches ``roots.list`` metadata, so the
frontend drives it through ``useList`` like any other resource.
"""

from __future__ import annotations

from pydantic import BaseModel

from angee.graphql.data import hasura_pydantic_resource
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import SchemaAddon


class PlatformAddon(BaseModel):
    """A computed platform-explorer row (no Django table behind it)."""

    id: str
    label: str
    model_count: int


_ROWS = [
    PlatformAddon(id="iam", label="IAM", model_count=5),
    PlatformAddon(id="storage", label="Storage", model_count=12),
    PlatformAddon(id="notes", label="Notes", model_count=3),
]


def _schema() -> object:
    resource = hasura_pydantic_resource(
        PlatformAddon,
        name="platform_addons",
        model_label="platform.addon",
        filterable=["id", "label", "model_count"],
        sortable=["label", "model_count"],
        rows=lambda info: _ROWS,
    )
    return GraphQLSchemas([SchemaAddon({"public": {"query": [resource.query], "types": [*resource.types]}})]).build(
        "public"
    )


def test_pydantic_resource_metadata_is_hasura_backed() -> None:
    schema = _schema()
    [meta] = schema.angee_resources
    assert meta.model is None
    assert meta.model_label == "platform.addon"
    assert meta.roots.list_name == "platform_addons"  # frontend -> useList
    assert meta.roots.aggregate_name == "platform_addons_aggregate"
    # The advertised identity field matches the by-pk addressing column.
    assert meta.query.identity.field == "id"


def test_pydantic_resource_list_filter_sort_count() -> None:
    result = _schema().execute_sync(
        """
        query {
          platform_addons(
            where: {model_count: {_gt: 4}}
            order_by: [{model_count: asc}]
          ) { id model_count }
          platform_addons_aggregate(where: {model_count: {_gt: 4}}) {
            aggregate { count }
          }
        }
        """
    )
    assert result.errors is None, result.errors
    assert [row["id"] for row in result.data["platform_addons"]] == [
        "iam",
        "storage",
    ]
    count = result.data["platform_addons_aggregate"]["aggregate"]["count"]
    assert count == 2


def test_pydantic_resource_by_pk() -> None:
    result = _schema().execute_sync('query { platform_addons_by_pk(id: "storage") { label } }')
    assert result.errors is None, result.errors
    assert result.data["platform_addons_by_pk"]["label"] == "Storage"


class CatalogueEntry(BaseModel):
    """A computed row whose scalar columns group through the run-query roots."""

    id: str
    category: str
    shelf: str | None
    active: bool


_ENTRIES = [
    CatalogueEntry(id="hammer", category="tools", shelf="A", active=True),
    CatalogueEntry(id="saw", category="tools", shelf="", active=False),
    CatalogueEntry(id="atlas", category="books", shelf=None, active=True),
]


def _grouped_schema() -> object:
    resource = hasura_pydantic_resource(
        CatalogueEntry,
        name="catalogue_entries",
        model_label="catalogue.entry",
        filterable=["id", "category", "shelf", "active"],
        sortable=["category"],
        groupable=["category", "shelf", "active"],
        rows=lambda info: _ENTRIES,
        frontend_row_model="server",
    )
    return GraphQLSchemas([SchemaAddon({"public": {"query": [resource.query], "types": [*resource.types]}})]).build(
        "public"
    )


def test_pydantic_resource_groupable_columns_project_server_axes() -> None:
    """Declared group columns expose the native group roots and drillable axes."""

    [meta] = _grouped_schema().angee_resources
    assert meta.roots.group_name == "catalogue_entries_groups"
    assert meta.roots.group_count_name == "catalogue_entries_groups_count"
    assert meta.type_names.group_key is not None
    assert meta.type_names.group_by_spec is not None
    assert {
        name: (axis.server.input, axis.server.key, axis.drill.field)
        for name, axis in meta.query.axes.items()
        if axis.server is not None and axis.drill is not None
    } == {
        "category": ("CATEGORY", "category", "category"),
        "shelf": ("SHELF", "shelf", "shelf"),
        "active": ("ACTIVE", "active", "active"),
    }


def test_pydantic_resource_groups_filtered_rows_with_distinct_blank_and_null() -> None:
    """Grouping counts the filtered rows; a blank string and NULL stay separate buckets."""

    result = _grouped_schema().execute_sync(
        """
        query {
          by_category: catalogue_entries_groups(group_by: [{field: CATEGORY}]) {
            key { category } aggregate { count }
          }
          active: catalogue_entries_groups(where: {active: {_eq: true}}, group_by: [{field: CATEGORY}]) {
            key { category } aggregate { count }
          }
          by_shelf: catalogue_entries_groups(group_by: [{field: SHELF}]) {
            key { shelf } aggregate { count }
          }
          catalogue_entries_groups_count(group_by: [{field: SHELF}])
        }
        """
    )
    assert result.errors is None, result.errors
    assert result.data["by_category"] == [
        {"key": {"category": "books"}, "aggregate": {"count": 1}},
        {"key": {"category": "tools"}, "aggregate": {"count": 2}},
    ]
    assert result.data["active"] == [
        {"key": {"category": "books"}, "aggregate": {"count": 1}},
        {"key": {"category": "tools"}, "aggregate": {"count": 1}},
    ]
    assert result.data["by_shelf"] == [
        {"key": {"shelf": ""}, "aggregate": {"count": 1}},
        {"key": {"shelf": "A"}, "aggregate": {"count": 1}},
        {"key": {"shelf": None}, "aggregate": {"count": 1}},
    ]
    assert result.data["catalogue_entries_groups_count"] == 3
