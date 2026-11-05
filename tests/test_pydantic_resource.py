"""Stage 2: ``hasura_pydantic_resource`` — a Hasura resource from a pydantic model.

A computed source (no Django model) presents the same list / aggregate(count) /
by-pk surface as a model resource and attaches ``roots.list`` metadata, so the
frontend drives it through ``useList`` like any other resource.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
import strawberry
from pydantic import BaseModel, computed_field
from strawberry_django_hasura import InMemoryRowSource, RowSource

from angee.graphql.data.pydantic_resource import hasura_pydantic_resource, pydantic_node
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


class ComputedGroup(BaseModel):
    label: str
    count: int


class ComputedEntry(BaseModel):
    id: str
    values: list[str]

    @computed_field
    @property
    def detail(self) -> str:
        return ", ".join(self.values)

    @computed_field
    @property
    def groups(self) -> list[ComputedGroup]:
        return [ComputedGroup(label=value, count=self.values.count(value)) for value in sorted(set(self.values))]


def _computed_schema(include_computed: bool | None) -> strawberry.Schema:
    pydantic_node(ComputedGroup, name="ComputedGroup")
    options = {} if include_computed is None else {"include_computed": include_computed}
    node = pydantic_node(ComputedEntry, name="ComputedEntry", **options)
    row = ComputedEntry(id="entry", values=["b", "a", "b"])
    query = strawberry.type(
        type("ComputedQuery", (), {"row": strawberry.field(resolver=lambda: row, graphql_type=node)})
    )
    return GraphQLSchemas([SchemaAddon({"public": {"query": [query]}})]).build("public")


@pytest.mark.parametrize("include_computed", [None, False])
def test_pydantic_node_excludes_computed_fields_by_default(include_computed: bool | None) -> None:
    schema = _computed_schema(include_computed)
    assert "detail: String!" not in schema.as_str()
    assert "groups: [ComputedGroup!]!" not in schema.as_str()
    result = schema.execute_sync("{ row { id values } }")
    assert result.errors is None, result.errors
    assert result.data == {"row": {"id": "entry", "values": ["b", "a", "b"]}}


def test_pydantic_node_includes_native_computed_scalars_and_nested_rows() -> None:
    schema = _computed_schema(True)
    assert "detail: String!" in schema.as_str()
    assert "groups: [ComputedGroup!]!" in schema.as_str()
    result = schema.execute_sync("{ row { id detail groups { label count } } }")
    assert result.errors is None, result.errors
    assert result.data == {
        "row": {
            "id": "entry",
            "detail": "b, a, b",
            "groups": [{"label": "a", "count": 1}, {"label": "b", "count": 2}],
        }
    }


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
    created: date


_ENTRIES = [
    CatalogueEntry(id="hammer", category="tools", shelf="A", active=True, created=date(2026, 11, 1)),
    CatalogueEntry(id="saw", category="tools", shelf="", active=False, created=date(2026, 11, 2)),
    CatalogueEntry(id="atlas", category="books", shelf=None, active=True, created=date(2026, 10, 1)),
]


def _grouped_schema(*, source: RowSource | None = None) -> object:
    resource = hasura_pydantic_resource(
        CatalogueEntry,
        name="catalogue_entries",
        model_label="catalogue.entry",
        filterable=["id", "category", "shelf", "active", "created"],
        sortable=["category", "id"],
        groupable=["category", "shelf", "active", "created"],
        source=source,
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
    assert meta.roots.grouped_rows_name == "catalogue_entries_grouped_rows"
    assert meta.type_names.grouped_row_page == "catalogue_entries_grouped_row_page"
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
        "created": ("CREATED", "created", "created"),
    }


def test_pydantic_grouped_rows_use_group_keys_and_one_source_read_per_page() -> None:
    calls: list[tuple[Any, Any, int | None, int | None]] = []

    class CountingSource(InMemoryRowSource):
        def query(
            self,
            info: strawberry.Info,
            *,
            where: Any,
            order_by: list[Any] | None,
            limit: int | None,
            offset: int | None,
        ) -> list[Any]:
            calls.append((where, order_by, limit, offset))
            return super().query(info, where=where, order_by=order_by, limit=limit, offset=offset)

    schema = _grouped_schema(source=CountingSource(lambda info: _ENTRIES))
    groups = schema.execute_sync(
        """{ catalogue_entries_groups(group_by: [{field: CREATED, granularity: MONTH}]) {
          key { created_month }
        } }"""
    )
    assert groups.errors is None, groups.errors
    keys = [group["key"]["created_month"] for group in groups.data["catalogue_entries_groups"]]
    calls.clear()
    rows = schema.execute_sync(
        """query($pages: [catalogue_entries_grouped_row_page!]!) {
          catalogue_entries_grouped_rows(
            group_by: {field: CREATED, granularity: MONTH}, pages: $pages,
            order_by: [{id: desc}]
          ) { id }
        }""",
        variable_values={
            "pages": [{"key": key, "limit": 1, "offset": 1 if index else 0} for index, key in enumerate(keys)]
        },
    )
    assert rows.errors is None, rows.errors
    assert [row["id"] for row in rows.data["catalogue_entries_grouped_rows"]] == ["atlas", "hammer"]
    assert len(calls) == len(keys)
    assert all(limit is None and offset is None for _, _, limit, offset in calls)

    blanks = schema.execute_sync(
        """{ catalogue_entries_grouped_rows(
          group_by: {field: SHELF}, pages: [
            {key: null, limit: 2}, {key: "", limit: 2}, {key: "A", limit: 2}
          ], where: {active: {_eq: true}}
        ) { id } }"""
    )
    assert blanks.errors is None, blanks.errors
    assert [row["id"] for row in blanks.data["catalogue_entries_grouped_rows"]] == ["atlas", "hammer"]


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
