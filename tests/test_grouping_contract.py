"""Cross-backend contract tests for Hasura-style grouped resources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from graphql import (
    GraphQLEnumType,
    GraphQLInputObjectType,
    GraphQLNonNull,
    GraphQLObjectType,
    build_schema,
)

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class GroupContractCase:
    """One grouped root emitted by a Hasura-compatible backend."""

    sdl_path: Path
    root_field: str
    group_type: str
    group_by_spec: str
    group_key: str
    aggregate_type: str
    where_type: str
    expected_groupable_fields: frozenset[str]
    expected_key_fields: frozenset[str]


GROUP_CONTRACT_CASES = (
    GroupContractCase(
        sdl_path=ROOT / "examples/notes-angee/runtime/schemas/public.graphql",
        root_field="notes_groups",
        group_type="notes_group",
        group_by_spec="NoteTypeGroupBySpec",
        group_key="NoteTypeGroupKey",
        aggregate_type="NoteTypeAggregate",
        where_type="notes_bool_exp",
        expected_groupable_fields=frozenset({"STATUS", "UPDATED_AT"}),
        expected_key_fields=frozenset({"status", "updated_at", "updated_at_month", "updated_at_month_range"}),
    ),
    GroupContractCase(
        sdl_path=ROOT / "addons/angee/operator/web/schema/operator.graphql",
        root_field="services_groups",
        group_type="services_group",
        group_by_spec="ServicesGroupBySpec",
        group_key="ServicesGroupKey",
        aggregate_type="services_aggregate_fields",
        where_type="services_bool_exp",
        expected_groupable_fields=frozenset({"STATUS", "RUNTIME", "HEALTH"}),
        expected_key_fields=frozenset({"status", "runtime", "health"}),
    ),
)


@pytest.mark.parametrize("case", GROUP_CONTRACT_CASES, ids=lambda case: case.root_field)
def test_grouped_resource_roots_share_hasura_ndc_contract(case: GroupContractCase) -> None:
    """Django and operator grouped roots expose the same typed-key DDN/NDC shape."""

    schema = build_schema(case.sdl_path.read_text(encoding="utf-8"))

    query = schema.get_type("Query")
    assert isinstance(query, GraphQLObjectType)
    root = query.fields[case.root_field]

    assert str(root.type) == f"[{case.group_type}!]!"
    assert set(root.args) == {"group_by", "where", "having", "order_by", "limit", "offset"}
    assert str(root.args["group_by"].type) == f"[{case.group_by_spec}!]!"
    assert str(root.args["where"].type) == case.where_type
    assert "having" in root.args
    assert "order_by" in root.args
    assert str(root.args["limit"].type) == "Int"
    assert str(root.args["offset"].type) == "Int"

    group = schema.get_type(case.group_type)
    assert isinstance(group, GraphQLObjectType)
    assert str(group.fields["key"].type) == f"{case.group_key}!"
    assert str(group.fields["aggregate"].type) == f"{case.aggregate_type}!"

    aggregates = _required_object(group.fields["aggregate"].type)
    count = aggregates.fields["count"]
    assert str(count.type) == "Int!"

    group_by_spec = schema.get_type(case.group_by_spec)
    assert isinstance(group_by_spec, GraphQLInputObjectType)
    field_type = group_by_spec.fields["field"].type
    assert isinstance(field_type, GraphQLNonNull)
    groupable = field_type.of_type
    assert isinstance(groupable, GraphQLEnumType)
    assert case.expected_groupable_fields <= set(groupable.values)

    group_key = schema.get_type(case.group_key)
    assert isinstance(group_key, GraphQLObjectType)
    assert case.expected_key_fields <= set(group_key.fields)


def _required_object(graphql_type: Any) -> GraphQLObjectType:
    assert isinstance(graphql_type, GraphQLNonNull)
    inner = graphql_type.of_type
    assert isinstance(inner, GraphQLObjectType)
    return inner
