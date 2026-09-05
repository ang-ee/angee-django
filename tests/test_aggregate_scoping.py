"""Native Hasura aggregation retains real REBAC row cardinality."""

from __future__ import annotations

from typing import Any

import pytest
import strawberry_django
from django.db import connection, models
from rebac import RelationshipTuple, SubjectRef, system_context, to_object_ref, write_relationships
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.schema import parse_zed
from strawberry import auto

from angee.base.models import AngeeDataModel
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import SchemaAddon, _clear_model_tables, _create_missing_tables, execute_schema, result_data


class ScopedAggregateRecord(AngeeDataModel):
    """A custom-public-identity record with two independent read grant paths."""

    sqid_prefix = "sar_"
    name = models.CharField(max_length=32)
    amount = models.IntegerField()
    bucket = models.CharField(max_length=16)

    class Meta(AngeeDataModel.Meta):
        abstract = False
        app_label = "tests"
        rebac_resource_type = "tests/scoped_aggregate_record"
        rebac_id_attr = "sqid"


@pytest.mark.django_db(transaction=True)
def test_native_aggregates_scope_each_logical_row_once() -> None:
    """Multiple permissions on one row cannot inflate groups, counts, or sums."""

    @strawberry_django.type(ScopedAggregateRecord)
    class ScopedAggregateRecordType(AngeeNode):
        name: auto

    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    active.set_schema(
        parse_zed(
            """
            definition auth/user {}
            definition tests/scoped_aggregate_record {
                relation reader: auth/user
                relation writer: auth/user
                permission read = reader + writer
            }
            """
        )
    )
    created = _create_missing_tables((ScopedAggregateRecord,))
    try:
        resource = hasura_model_resource(
            ScopedAggregateRecordType,
            model=ScopedAggregateRecord,
            name="scoped_records",
            filterable=["id", "amount"],
            sortable=["name"],
            aggregatable=["amount"],
            groupable=["bucket"],
            insert=False,
            update=False,
            delete=False,
        )
        schema = GraphQLSchemas(
            [
                SchemaAddon(
                    {"public": {"query": [resource.query], "types": [ScopedAggregateRecordType, *resource.types]}}
                )
            ]
        ).build("public")
        alice = SubjectRef.of("auth/user", "alice")
        bob = SubjectRef.of("auth/user", "bob")
        with system_context(reason="test.aggregate.scope.seed"):
            first = ScopedAggregateRecord.objects.create(name="first", amount=5, bucket="a")
            second = ScopedAggregateRecord.objects.create(name="second", amount=10, bucket="b")
            hidden = ScopedAggregateRecord.objects.create(name="hidden", amount=1000, bucket="a")
            excluded = ScopedAggregateRecord.objects.create(name="excluded", amount=100, bucket="c")
        write_relationships(
            [
                RelationshipTuple(to_object_ref(first), "reader", alice),
                RelationshipTuple(to_object_ref(first), "writer", alice),
                RelationshipTuple(to_object_ref(second), "reader", alice),
                RelationshipTuple(to_object_ref(hidden), "reader", bob),
                RelationshipTuple(to_object_ref(excluded), "reader", alice),
            ]
        )
        result: dict[str, Any] = result_data(
            execute_schema(
                schema,
                """
                query {
                  rows: scoped_records(where: {amount: {_lte: 20}}, order_by: [{name: asc}]) { id name }
                  total: scoped_records_aggregate(where: {amount: {_lte: 20}}) {
                    aggregate { count sum { amount } }
                  }
                  groups: scoped_records_groups(
                    group_by: [{field: BUCKET}], where: {amount: {_lte: 20}}
                    having: {sum_amount_gt: 0}, limit: 1
                  ) { key { bucket } aggregate { count sum { amount } } }
                  count: scoped_records_groups_count(
                    group_by: [{field: BUCKET}], where: {amount: {_lte: 20}}
                    having: {sum_amount_gt: 0}
                  )
                  actor_total: scoped_records_aggregate { aggregate { count sum { amount } } }
                }
                """,
                user=alice,
            )
        )
        assert result["rows"] == [{"id": str(first.sqid), "name": "first"}, {"id": str(second.sqid), "name": "second"}]
        assert result["total"] == {"aggregate": {"count": 2, "sum": {"amount": "15"}}}
        assert result["actor_total"] == {"aggregate": {"count": 3, "sum": {"amount": "115"}}}
        assert result["count"] == 2
        assert len(result["groups"]) == 1
        group = result["groups"][0]
        assert group["aggregate"]["count"] == 1
        assert group["aggregate"]["sum"]["amount"] == {"a": "5", "b": "10"}[group["key"]["bucket"]]
    finally:
        _clear_model_tables((ScopedAggregateRecord,))
        if created:
            with connection.schema_editor() as editor:
                for model in reversed(created):
                    editor.delete_model(model)
        reset_backend()
