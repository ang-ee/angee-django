"""Resource query vocabulary follows final SDL and executable native owners."""

import datetime
import decimal
import enum
import json
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import strawberry
from django.db import models
from django.db.backends.postgresql.base import DatabaseWrapper
from graphql import GraphQLError, build_schema, coerce_input_value, get_named_type
from strawberry.scalars import JSON
from strawberry_django_hasura import hasura_config
from strawberry_django_hasura.inputs import build_bool_exp, comparison_for_python_type, host_module

from angee.data.metadata import (
    DataDefaultSortMetadata,
    DataQueryAxis,
    DataQueryDrill,
    DataQueryExtraction,
    DataQueryServerAxis,
    DataResourceFieldMetadata,
    DataResourceMetadata,
    DataResourceRoots,
    DataResourceTypeNames,
)
from angee.graphql.data.lookups import resource_filter_lookups
from angee.graphql.data.query import ResourceQueryProjection

_SCHEMA = """
interface Node { id: ID! }
type Channel implements Node { id: ID!, display_name: String! }
enum State { OPEN CLOSED }
type Entry implements Node { id: ID!, channel: Channel, payload: JSON, state: State!, created: DateTime }
scalar JSON
scalar DateTime
input String_comparison_exp {
  _eq: String, _neq: String, _in: [String!], _is_null: Boolean,
  _like: String, _ilike: String, _nlike: String, _nilike: String,
  _iregex: String, _similar: String, _nsimilar: String
}
input ID_comparison_exp { _eq: ID, _in: [ID!], _is_null: Boolean }
input DateTime_comparison_exp { _eq: DateTime, _gte: DateTime, _lt: DateTime, _is_null: Boolean }
input EntryFilter { state: String_comparison_exp, channel: ID_comparison_exp, created: DateTime_comparison_exp }
enum EntryGroupField { CHANNEL CHANNEL__DISPLAY_NAME PAYLOAD__MAILBOX CREATED }
enum Granularity { MONTH WEEK DAY DAY_OF_WEEK }
input EntryGroupBySpec { field: EntryGroupField!, granularity: Granularity }
type BucketRange { from: DateTime!, to: DateTime! }
type EntryKey {
  channel_id: ID, channel__display_name: String, payload__mailbox: String, created: DateTime,
  created_month: DateTime, created_month_range: BucketRange,
  created_week: DateTime, created_week_range: BucketRange,
  created_day: DateTime, created_day_range: BucketRange, created_day_of_week: Int
}
type Query { entries(where: EntryFilter): [Entry!]! }
"""


def _projection(**kwargs: object) -> ResourceQueryProjection:
    return ResourceQueryProjection(
        schema=build_schema(_SCHEMA),
        types=DataResourceTypeNames(
            node="Entry", filter="EntryFilter", group_key="EntryKey", group_by_spec="EntryGroupBySpec"
        ),
        fields=(
            DataResourceFieldMetadata(name="id", kind="scalar", scalar="ID"),
            DataResourceFieldMetadata(
                name="channel", kind="relation", relation_model_label="tests.Channel", relation_object=True
            ),
            DataResourceFieldMetadata(name="payload", kind="scalar", scalar="JSON"),
            DataResourceFieldMetadata(name="state", kind="enum"),
            DataResourceFieldMetadata(name="created", kind="scalar", scalar="DateTime"),
        ),
        identity="id",
        filter_fields=("channel", "state", "created"),
        **kwargs,
    )


def test_query_separates_row_selections_from_bucket_keys() -> None:
    query = _projection(
        axes=(
            DataQueryAxis(
                field="channel",
                kind="relation",
                server=DataQueryServerAxis("CHANNEL", "channel_id"),
                drill=DataQueryDrill("identity", "channel", "channel_id"),
            ),
            DataQueryAxis(
                field="channel__display_name",
                server=DataQueryServerAxis("CHANNEL__DISPLAY_NAME", "channel__display_name"),
            ),
            DataQueryAxis(
                field="payload.mailbox", kind="json", server=DataQueryServerAxis("PAYLOAD__MAILBOX", "payload__mailbox")
            ),
        ),
        label_axes={"channel": "channel__display_name"},
    ).build()
    assert set(query.axes) == {"channel", "payload.mailbox"}
    channel = query.axes["channel"]
    assert channel.identity_path == "channel.id"
    assert channel.label_path == "channel.display_name"
    assert channel.paths == ("channel.id", "channel.display_name")
    assert channel.server.key == "channel_id"
    assert channel.server.label_key == "channel__display_name"
    assert channel.drill.field == "channel"
    mailbox = query.axes["payload.mailbox"]
    assert mailbox.identity_path == "payload.mailbox"
    assert mailbox.paths == ("payload",)
    assert mailbox.drill is None
    assert query.fields["payload.mailbox"].row.path == "payload.mailbox"
    assert query.fields["payload.mailbox"].row.paths == ("payload",)
    assert query.fields["channel"].row.path == "channel.id"
    assert query.fields["channel"].row.paths == ("channel.id",)


def test_client_filter_only_field_has_no_invented_row_accessor() -> None:
    schema = build_schema(_SCHEMA.replace("state: State!, ", ""))
    server = replace(_projection(), schema=schema).build()
    assert server.fields["state"].filter is not None
    assert server.fields["state"].row is None
    client = replace(_projection(), schema=schema, row_model="client").build()
    assert client.fields["state"].filter is None
    assert client.fields["state"].sort is None
    assert "state" not in client.axes
    sorted_client = replace(
        _projection(),
        schema=schema,
        row_model="client",
        order_fields=("state",),
        default_sort=(DataDefaultSortMetadata("state", "ASC"),),
    ).build()
    assert sorted_client.sort.default == ()


def test_query_filter_operand_domain_comes_from_input_not_output_enum() -> None:
    query = _projection().build()
    state = query.fields["state"]
    assert state.kind == "enum"
    assert state.filter.scalar == "String"
    assert state.filter.values == ()
    assert coerce_input_value({"state": {"_eq": "open"}}, _projection().schema.get_type("EntryFilter")) == {
        "state": {"_eq": "open"}
    }


def test_query_operators_are_executable_intersection_not_all_sdl_fields() -> None:
    computed = _projection().build().fields["state"].filter.operators
    assert {"like", "iLike", "notLike", "notILike", "startsWith", "endsWith"} <= set(computed)
    assert not {"iRegex", "similar", "notSimilar"} & set(computed)
    model = _projection(filter_operators=("iregex", "similar", "nsimilar")).build()
    assert {"iRegex", "similar", "notSimilar"} <= set(model.fields["state"].filter.operators)
    assert "similar" not in model.fields["channel"].filter.operators


def test_query_has_explicit_client_grouping_and_optional_extraction_drill() -> None:
    month = DataQueryExtraction(
        "month",
        "MONTH",
        "created_month",
        "created_month_range",
        DataQueryDrill("range", "created", "created_month", "created_month_range"),
    )
    day_of_week = DataQueryExtraction("day_of_week", "DAY_OF_WEEK", "created_day_of_week")
    query = _projection(
        row_model="client",
        axes=(
            DataQueryAxis(
                field="created",
                kind="date",
                server=DataQueryServerAxis("CREATED", "created"),
                extractions=(month, day_of_week),
            ),
        ),
    ).build()
    assert query.axes["state"].server is None
    assert query.axes["state"].paths == ("state",)
    assert query.axes["created"].extractions[0].drill.kind == "range"
    assert query.axes["created"].extractions[1].drill is None


def test_query_axes_follow_composed_group_inputs_keys_and_range_fields() -> None:
    schema = build_schema(
        _SCHEMA.replace("channel_id: ID, ", "")
        .replace("MONTH WEEK DAY DAY_OF_WEEK", "WEEK DAY DAY_OF_WEEK")
        .replace("  created_week: DateTime, created_week_range: BucketRange,", "")
        .replace("from: DateTime!, to: DateTime!", "from: DateTime!")
    )
    projection = _projection(
        axes=(
            DataQueryAxis(field="channel", server=DataQueryServerAxis("CHANNEL", "channel_id")),
            DataQueryAxis(
                field="channel__display_name",
                server=DataQueryServerAxis("CHANNEL__DISPLAY_NAME", "channel__display_name"),
            ),
            DataQueryAxis(
                field="created",
                kind="date",
                server=DataQueryServerAxis("CREATED", "created"),
                extractions=tuple(
                    DataQueryExtraction(
                        name,
                        name.upper(),
                        f"created_{name}",
                        f"created_{name}_range",
                        DataQueryDrill("range", "created", f"created_{name}", f"created_{name}_range"),
                    )
                    for name in ("month", "week", "day")
                ),
            ),
        ),
        label_axes={"channel": "channel__display_name"},
    )
    query = replace(projection, schema=schema).build()
    assert set(query.axes) == {"created"}
    assert [(item.name, item.range_key, item.drill) for item in query.axes["created"].extractions] == [
        ("day", None, None),
    ]
    without_field = build_schema(_SCHEMA.replace("PAYLOAD__MAILBOX CREATED", "PAYLOAD__MAILBOX"))
    assert "created" not in replace(projection, schema=without_field).build().axes


def test_query_resolves_nested_final_aliases_without_rewriting_filter_keys() -> None:
    schema = build_schema(
        _SCHEMA.replace("display_name: String!", "displayName: String!").replace(
            "input EntryFilter {", "input EntryFilter { channel__display_name: String_comparison_exp,"
        )
    )
    schema.get_type("Channel").fields["displayName"].extensions = {
        "strawberry-definition": SimpleNamespace(python_name="display_name")
    }
    query = replace(_projection(), schema=schema, filter_fields=("channel__display_name",), row_model="client").build()
    field = query.fields["channel.displayName"]
    assert field.row.path == "channel.displayName"
    assert field.row.paths == ("channel.displayName",)
    assert field.filter.field == "channel__display_name"
    assert query.axes["channel.displayName"].identity_path == "channel.displayName"


def test_query_preserves_declared_custom_relation_identity() -> None:
    schema = build_schema(
        _SCHEMA.replace(
            "type Channel implements Node { id: ID!, display_name: String! }",
            "type Channel { id: ID!, public_key: ID!, display_name: String! }",
        )
    )
    projection = replace(_projection(), schema=schema, identity_policies={"Channel": "public_key"})
    assert projection.build().fields["channel"].relation.identity_path == "channel.public_key"


def test_query_does_not_infer_relation_identity_from_an_unrelated_id_field() -> None:
    schema = build_schema(
        _SCHEMA.replace(
            "type Channel implements Node { id: ID!, display_name: String! }",
            "type Channel { owner_id: ID!, display_name: String! }",
        )
    )
    relation = replace(_projection(), schema=schema).build().fields["channel"].relation
    assert relation.identity_path is None
    assert relation.label_path == "channel.display_name"


def test_unexposed_relation_uses_public_identity_constant(monkeypatch) -> None:
    monkeypatch.setattr("angee.graphql.data.query.PUBLIC_ID_FIELD_NAME", "public_key")
    schema = build_schema(
        _SCHEMA.replace(
            "type Channel implements Node { id: ID!, display_name: String! }",
            "type Channel { sqid: ID!, public_key: ID!, display_name: String! }",
        )
    )
    relation = replace(_projection(), schema=schema).build().fields["channel"].relation
    assert relation.identity_path == "channel.public_key"


class QueryPattern(models.Model):
    name = models.CharField(max_length=100)
    related = models.ManyToManyField("self")

    class Meta:
        app_label = "tests"


@strawberry.enum
class QueryState(enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


@strawberry.input
class QueryStateComparison:
    """A composed enum comparison exercises input domains absent from Django choices."""

    eq: QueryState | None = strawberry.field(name="_eq", default=strawberry.UNSET)
    in_: list[QueryState] | None = strawberry.field(name="_in", default=strawberry.UNSET)
    is_null: bool | None = strawberry.field(name="_is_null", default=strawberry.UNSET)


@strawberry.type
class QueryScalarRow:
    id: strawberry.ID
    name: str
    count: int
    ratio: float
    amount: decimal.Decimal
    active: bool
    date: datetime.date
    timestamp: datetime.datetime
    time: datetime.time
    uuid: uuid.UUID
    payload: JSON
    state: QueryState
    choice: QueryState


@strawberry.type
class QueryScalarRoot:
    ready: bool = True


_SCALAR_OPERANDS = {
    "id": "row_123",
    "name": "50%_\\path",
    "count": 0,
    "ratio": 0.125,
    "amount": "9007199254740993.000000000000000001",
    "active": False,
    "date": "2024-02-29",
    "timestamp": "2026-09-07T12:34:56.123456+02:00",
    "time": "12:34:56.123456",
    "uuid": "12345678-1234-5678-1234-567812345678",
    "payload": {"count": 0, "active": False, "tags": ["a"]},
    "state": "OPEN",
    "choice": "open",
}
_OPERATOR_FIXTURE = (
    Path(__file__).resolve().parents[1] / "packages/app/src/tests/fixtures/resource-query-operators.json"
)
_TIME_OPERANDS = {
    "valid": [
        "00:00", "23:59:59.999999", "12:34:56.123456789", "12:34:56Z",
        "12:34:56+02:30", "12:34:56-05:00", "12:34:56+05:30:15.123456",
        "24:00", "24:00:00.000000001+02:00",
    ],
    "invalid": [
        "not-a-time", "25:00", "24:01", "24:00:00.001", "12:60", "12:34:60", "12:34:56+24:00", "12:34\n",
    ],
}
_INVALID_DATE_OPERANDS = {
    "date": ["2026-09-07\n"],
    "timestamp": ["2026-09-07\n", "2026-09-07T12:34:56Z\n"],
}


def _native_operator_schema():
    """Use the installed Hasura inputs and Strawberry scalar implementations unchanged."""

    comparisons = {
        name: comparison_for_python_type(python_type)
        for name, python_type in QueryScalarRow.__annotations__.items()
        if name not in {"state", "choice"}
    }
    comparisons.update(state=QueryStateComparison, choice=comparison_for_python_type(str))
    where = build_bool_exp("query_scalars", comparisons, host_module("query_scalars"))
    return strawberry.Schema(
        query=QueryScalarRoot, types=[QueryScalarRow, where], config=hasura_config()
    )._schema


def _native_operator_resource(schema):
    types = DataResourceTypeNames(node="QueryScalarRow", filter="query_scalars_bool_exp")
    query = ResourceQueryProjection(
        schema=schema,
        types=types,
        fields=(),
        identity="id",
        filter_fields=tuple(_SCALAR_OPERANDS),
        model=QueryPattern,
        filter_operators=tuple(resource_filter_lookups(QueryPattern, ("name",))),
    ).build()
    return DataResourceMetadata(
        model=QueryPattern,
        model_label="tests.QueryScalarRow",
        resource_type=None,
        app_label="tests",
        model_name="QueryScalarRow",
        query=query,
        roots=DataResourceRoots(),
        type_names=types,
    ).as_wire(schema_name="console")


def test_native_operator_fixture_matches_final_schema_and_metadata(monkeypatch) -> None:
    """JS projects this native fixture; fail here whenever its generated owners drift.

    The Python and pnpm CI lanes remain independent. Regenerate this fixture
    with these native builders, never edit its SDL or capability lists by hand.
    """

    schema = _native_operator_schema()
    fixture = json.loads(_OPERATOR_FIXTURE.read_text())
    from graphql import print_schema

    assert fixture["sdl"] == print_schema(schema)
    assert fixture["operands"] == _SCALAR_OPERANDS
    assert fixture["times"] == _TIME_OPERANDS
    assert fixture["invalidDates"] == _INVALID_DATE_OPERANDS
    for backend in ("sqlite", "postgresql"):
        connection = DatabaseWrapper({"ENGINE": "django.db.backends.postgresql", "NAME": "test"})
        if backend == "sqlite":
            from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteWrapper

            connection = SQLiteWrapper({"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"})
        monkeypatch.setattr("angee.graphql.data.query.connection", connection)
        monkeypatch.setattr("angee.graphql.data.lookups.connection", connection)
        assert fixture["resources"][backend] == _native_operator_resource(schema)


def test_native_operator_samples_use_real_scalar_parsers() -> None:
    """SDL shape checks in JS complement real native custom-scalar coercion here."""

    schema = _native_operator_schema()
    where = schema.get_type("query_scalars_bool_exp")
    for field, value in _SCALAR_OPERANDS.items():
        operand = get_named_type(where.fields[field].type).fields["_eq"].type
        coerced = coerce_input_value(value, operand)
        assert coerced is not None
    assert coerce_input_value(_SCALAR_OPERANDS["amount"], schema.get_type("Decimal")) == decimal.Decimal(
        "9007199254740993.000000000000000001"
    )
    assert coerce_input_value(_SCALAR_OPERANDS["timestamp"], schema.get_type("DateTime")).microsecond == 123456
    for value in _TIME_OPERANDS["valid"]:
        assert isinstance(coerce_input_value(value, schema.get_type("Time")), datetime.time)
    for value in _TIME_OPERANDS["invalid"]:
        with pytest.raises(GraphQLError):
            coerce_input_value(value, schema.get_type("Time"))
    for field, values in _INVALID_DATE_OPERANDS.items():
        operand = get_named_type(where.fields[field].type).fields["_eq"].type
        for value in values:
            with pytest.raises(GraphQLError):
                coerce_input_value(value, operand)
    for scalar, invalid in (
        ("Int", 1.5), ("Int", 2**31), ("Boolean", "false"),
        ("Date", "2026-02-30"), ("DateTime", "not-a-date"),
        ("Time", "25:00:00"), ("Decimal", "not-a-number"),
        ("QueryState", "open"),
    ):
        with pytest.raises(GraphQLError):
            coerce_input_value(invalid, schema.get_type(scalar))


def test_postgres_similar_uses_native_lookup_with_bound_sql_pattern(monkeypatch) -> None:
    postgres = DatabaseWrapper({"ENGINE": "django.db.backends.postgresql", "NAME": "test"}, alias="query_contract")
    monkeypatch.setattr("angee.graphql.data.lookups.connection", postgres)
    lookups = resource_filter_lookups(QueryPattern, ("name", "related"))
    assert lookups["similar"] == ("__angee_similar", False)
    assert lookups["nsimilar"] == ("__angee_similar", True)
    pattern = "A_%(foo|bar)"
    sql, params = (
        QueryPattern.objects.filter(name__angee_similar=pattern).query.get_compiler(connection=postgres).as_sql()
    )
    assert " SIMILAR TO %s" in sql
    assert params == (pattern,)
