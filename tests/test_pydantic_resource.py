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
    assert meta.public_id_field == "id"


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
