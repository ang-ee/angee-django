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
    get_named_type,
)

from angee.graphql.schema import GraphQLSchemas
from tests.composed_host import COMPOSED_TEST_TIMEOUT

ROOT = Path(__file__).resolve().parents[1]


def test_every_server_grouped_resource_emits_both_roots_and_metadata() -> None:
    """Audit the executable SDL, including computed resources, in both named schemas."""

    schemas = GraphQLSchemas.from_discovery()
    audited: set[tuple[str, str]] = set()
    for schema_name in ("public", "console"):
        schema = schemas.build(schema_name)
        query = schema._schema.query_type
        assert query is not None
        for resource in schema.angee_resources:
            if not any(axis.server is not None for axis in resource.query.axes.values()):
                continue
            audited.add((schema_name, resource.model_label))
            roots = resource.roots
            types = resource.type_names
            assert roots.group_name in query.fields, (schema_name, resource.model_label, roots)
            assert roots.grouped_rows_name in query.fields, (schema_name, resource.model_label, roots)
            assert types.group_by_spec is not None, (schema_name, resource.model_label)
            assert types.grouped_row_page is not None, (schema_name, resource.model_label)
            grouped_rows = query.fields[roots.grouped_rows_name]
            assert set(grouped_rows.args) == {"group_by", "pages", "where", "order_by"}
            assert get_named_type(grouped_rows.args["group_by"].type).name == types.group_by_spec
            page_type = get_named_type(grouped_rows.args["pages"].type)
            assert isinstance(page_type, GraphQLInputObjectType)
            assert page_type.name == types.grouped_row_page
            assert set(page_type.fields) == {"key", "limit", "offset"}
            result_type = get_named_type(grouped_rows.type)
            assert isinstance(result_type, GraphQLObjectType)
            assert result_type.name == f"{roots.grouped_rows_name}_page_result"
            assert str(grouped_rows.type) == f"[{result_type.name}!]!"
            assert str(result_type.fields["key"].type) == "JSON"
            assert str(result_type.fields["rows"].type) == f"[{types.node}!]!"
    assert ("console", "platform.Field") in audited
    assert any(label != "platform.Field" for _, label in audited)


def test_composed_grouped_resources_emit_group_and_row_roots(tmp_path: Path) -> None:
    """Audit the full generated host, whose addon roster exceeds the source fixture."""

    rendered = _composed_sdl(tmp_path)
    for schema_name in ("public", "console"):
        schema = build_schema(rendered[schema_name])
        query = schema.query_type
        assert query is not None
        grouped = {
            name.removesuffix("_groups")
            for name, field in query.fields.items()
            if name.endswith("_groups") and "group_by" in field.args
        }
        assert grouped, schema_name
        for name in grouped:
            rows_root = f"{name}_grouped_rows"
            assert rows_root in query.fields, (schema_name, name)
            rows = query.fields[rows_root]
            assert set(rows.args) == {"group_by", "pages", "where", "order_by"}
            page_type = get_named_type(rows.args["pages"].type)
            assert isinstance(page_type, GraphQLInputObjectType)
            assert set(page_type.fields) == {"key", "limit", "offset"}
            result_type = get_named_type(rows.type)
            assert isinstance(result_type, GraphQLObjectType)
            assert str(rows.type) == f"[{result_type.name}!]!"
            assert str(result_type.fields["key"].type) == "JSON"
            assert isinstance(result_type.fields["rows"].type, GraphQLNonNull)
            assert isinstance(result_type.fields["rows"].type.of_type, GraphQLList)
        if schema_name == "console":
            assert "platform_fields_grouped_rows" in query.fields


def _composed_sdl(tmp_path: Path, *, app: str | None = None) -> dict[str, str]:
    """Render SDL from the isolated composed host."""

    report = tmp_path / "schemas.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests" / "composed_host.py"),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            *(["--app", app] if app is not None else []),
            "--action",
            "schemas",
            "--output",
            str(report),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=COMPOSED_TEST_TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, f"fresh schema composition failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(report.read_text())


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
        sdl_path=ROOT / "src/angee/operator/web/schema/operator.graphql",
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

    sdl = (
        case.sdl_path.read_text(encoding="utf-8")
        if case.sdl_path is not None
        else _composed_sdl(tmp_path, app="example.notes")["public"]
    )
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
