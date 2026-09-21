"""Cross-backend contract tests for Hasura-style grouped resources."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from graphql import (
    GraphQLEnumType,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    build_schema,
)

ROOT = Path(__file__).resolve().parents[1]


def _composed_notes_sdl(tmp_path: Path) -> str:
    """Render the reference addon's schema through the isolated composed host."""

    report = tmp_path / "schemas.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests" / "composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"),
            "--app", "example.notes",
            "--action", "schemas",
            "--output", str(report),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"fresh schema composition failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(report.read_text())["public"]


@dataclass(frozen=True)
class GroupContractCase:
    """One grouped root emitted by a Hasura-compatible backend."""

    sdl_path: Path | None
    root_field: str
    group_type: str
    where_type: str
    expected_groupable_fields: frozenset[str]
    expected_key_fields: frozenset[str]


GROUP_CONTRACT_CASES = (
    GroupContractCase(
        sdl_path=None,
        root_field="notes_groups",
        group_type="notes_group",
        where_type="notes_bool_exp",
        expected_groupable_fields=frozenset({"STATUS", "UPDATED_AT"}),
        expected_key_fields=frozenset({"status", "updated_at", "updated_at_month", "updated_at_month_range"}),
    ),
    GroupContractCase(
        sdl_path=ROOT / "addons/angee/operator/web/schema/operator.graphql",
        root_field="services_groups",
        group_type="services_group",
        where_type="services_bool_exp",
        expected_groupable_fields=frozenset({"STATUS", "RUNTIME", "HEALTH"}),
        expected_key_fields=frozenset({"status", "runtime", "health"}),
    ),
)


@pytest.mark.parametrize("case", GROUP_CONTRACT_CASES, ids=lambda case: case.root_field)
def test_grouped_resource_roots_share_hasura_ndc_contract(case: GroupContractCase, tmp_path: Path) -> None:
    """Django and operator grouped roots expose the same typed-key DDN/NDC shape."""

    sdl = case.sdl_path.read_text(encoding="utf-8") if case.sdl_path is not None else _composed_notes_sdl(tmp_path)
    schema = build_schema(sdl)

    query = schema.get_type("Query")
    assert isinstance(query, GraphQLObjectType)
    root = query.fields[case.root_field]

    assert str(root.type) == f"[{case.group_type}!]!"
    assert set(root.args) == {"group_by", "where", "having", "order_by", "limit", "offset"}
    # Each emitter owns its generated names; follow the native GraphQL links
    # while requiring the shared non-null list of non-null input objects.
    group_by_argument = root.args["group_by"].type
    assert isinstance(group_by_argument, GraphQLNonNull)
    group_by_list = group_by_argument.of_type
    assert isinstance(group_by_list, GraphQLList)
    group_by_item = group_by_list.of_type
    assert isinstance(group_by_item, GraphQLNonNull)
    group_by_spec = group_by_item.of_type
    assert isinstance(group_by_spec, GraphQLInputObjectType)
    assert str(root.args["where"].type) == case.where_type
    assert "having" in root.args
    assert "order_by" in root.args
    assert str(root.args["limit"].type) == "Int"
    assert str(root.args["offset"].type) == "Int"

    group = schema.get_type(case.group_type)
    assert isinstance(group, GraphQLObjectType)
    group_key = _required_object(group.fields["key"].type)
    aggregates = _required_object(group.fields["aggregate"].type)
    count = aggregates.fields["count"]
    assert str(count.type) == "Int!"

    field_type = group_by_spec.fields["field"].type
    assert isinstance(field_type, GraphQLNonNull)
    groupable = field_type.of_type
    assert isinstance(groupable, GraphQLEnumType)
    assert case.expected_groupable_fields <= set(groupable.values)

    assert case.expected_key_fields <= set(group_key.fields)


def _required_object(graphql_type: Any) -> GraphQLObjectType:
    assert isinstance(graphql_type, GraphQLNonNull)
    inner = graphql_type.of_type
    assert isinstance(inner, GraphQLObjectType)
    return inner
