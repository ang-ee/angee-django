"""The pivot data shape served by the existing grouped resource owner.

A pivot is a cross-tabulation: row axes down the side, column axes across the
top, measures in the cells, subtotals per axis and a grand total. Angee already
owns one grouped read surface — ``<resource>_groups(group_by: [...])`` (the
``strawberry-django-aggregates`` compiler behind ``hasura_model_resource``) —
and these tests pin that it serves the whole pivot shape without a pivot-specific
resolver:

- cells are one grouped call over ``[row axes..., column axes...]``;
- row subtotals, column totals and the grand total are sibling aliases in the
  *same* document, so a rendered pivot costs one request whose SQL count is set
  by the number of axis combinations, never by the number of cells;
- every total is computed database-side, so a non-additive measure (``avg``)
  is exact rather than re-derived from cell values;
- an axis window (``limit``/``offset`` plus ``_groups_count``) scopes the cell
  call through ``where``, which is what makes sparse loading possible;
- a time axis buckets by granularity and carries the half-open bucket range that
  drills a cell down to its filtered record list.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

import pytest
import strawberry_django
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import system_context
from strawberry import auto

from angee.base.models import AngeeDataModel
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import (
    SchemaAddon,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
    result_data,
)


class PivotSale(AngeeDataModel):
    """Concrete fact model cross-tabulated by the pivot-shape tests."""

    sqid_prefix = "pvs_"

    region = models.CharField(max_length=32)
    channel = models.CharField(max_length=32)
    booked_at = models.DateTimeField()
    amount = models.IntegerField(default=0)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


@strawberry_django.type(PivotSale)
class PivotSaleType(AngeeNode):
    """Node type exposing the pivot fact resource."""

    region: auto
    channel: auto
    booked_at: auto
    amount: auto


def _pivot_schema() -> Any:
    """Return a schema exposing the pivot fact resource's grouped roots."""

    write_backend = type(
        "NoopWriteBackend",
        (),
        {
            "create": lambda self, info, data: None,
            "update": lambda self, info, pk, data: None,
            "delete": lambda self, info, pk: None,
        },
    )()
    resource = hasura_model_resource(
        PivotSaleType,
        model=PivotSale,
        name="pivot_sales",
        filterable=["id", "region", "channel", "booked_at", "amount"],
        sortable=["region", "channel", "booked_at"],
        aggregatable=["amount"],
        groupable=["region", "channel", "booked_at"],
        get_queryset=lambda info: PivotSale.objects.all(),
        write_backend=write_backend,
        id_decode=lambda value: value,
    )
    return GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": [resource.query],
                        "mutation": [resource.mutation],
                        "types": [PivotSaleType, *resource.types],
                    }
                }
            )
        ]
    ).build("public")


# One region × channel × month fact table with deliberately uneven cells: the
# west/direct cell holds two rows so `avg` differs from every single amount, and
# the north/partner cell is missing so the matrix is sparse.
FACTS: tuple[tuple[str, str, str, int], ...] = (
    ("west", "direct", "2026-01-08", 100),
    ("west", "direct", "2026-01-19", 40),
    ("west", "partner", "2026-02-03", 60),
    ("north", "direct", "2026-01-27", 30),
    ("north", "direct", "2026-02-11", 10),
    ("south", "partner", "2026-02-17", 70),
)


def _seed(facts: tuple[tuple[str, str, str, int], ...] = FACTS) -> None:
    with system_context(reason="test.pivot.seed"):
        for region, channel, day, amount in facts:
            PivotSale.objects.create(
                region=region,
                channel=channel,
                booked_at=timezone.make_aware(dt.datetime.fromisoformat(f"{day}T12:00:00")),
                amount=amount,
            )


@pytest.fixture
def pivot_tables(transactional_db: Any) -> Iterator[None]:
    """Create and clear the pivot fact table around one test."""

    del transactional_db
    created = _create_missing_tables((PivotSale,))
    try:
        yield
    finally:
        _clear_model_tables((PivotSale,))
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


PIVOT_DOCUMENT = """
query Pivot(
  $cellAxes: [PivotSaleTypeGroupBySpec!]!
  $rowAxes: [PivotSaleTypeGroupBySpec!]!
  $columnAxes: [PivotSaleTypeGroupBySpec!]!
  $where: pivot_sales_bool_exp
) {
  cells: pivot_sales_groups(group_by: $cellAxes, where: $where) {
    key { region channel }
    aggregate { count sum { amount } avg { amount } }
  }
  rowTotals: pivot_sales_groups(group_by: $rowAxes, where: $where) {
    key { region }
    aggregate { count sum { amount } avg { amount } }
  }
  columnTotals: pivot_sales_groups(group_by: $columnAxes, where: $where) {
    key { channel }
    aggregate { count sum { amount } avg { amount } }
  }
  grandTotal: pivot_sales_aggregate(where: $where) {
    aggregate { count sum { amount } avg { amount } }
  }
  rowCount: pivot_sales_groups_count(group_by: $rowAxes, where: $where)
}
"""

PIVOT_VARIABLES: dict[str, Any] = {
    "cellAxes": [{"field": "REGION"}, {"field": "CHANNEL"}],
    "rowAxes": [{"field": "REGION"}],
    "columnAxes": [{"field": "CHANNEL"}],
}


def _cells(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(row["key"]["region"], row["key"]["channel"]): row["aggregate"] for row in rows}


def _by_key(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {row["key"][key]: row["aggregate"] for row in rows}


def test_one_document_serves_cells_subtotals_and_grand_total(pivot_tables: None) -> None:
    """The grouped root cross-tabulates and totals the same filtered scope in one round trip."""

    del pivot_tables
    _seed()

    with system_context(reason="test.pivot.matrix"):
        data = result_data(execute_schema(_pivot_schema(), PIVOT_DOCUMENT, PIVOT_VARIABLES))

    cells = _cells(data["cells"])
    # Sparse by construction: only the four populated region × channel pairs.
    assert set(cells) == {
        ("west", "direct"),
        ("west", "partner"),
        ("north", "direct"),
        ("south", "partner"),
    }
    # `sum` over an integer column widens to Postgres bigint, which the
    # aggregates library encodes as the string-valued `BigInt` scalar rather than
    # letting a 32-bit GraphQL Int overflow. A pivot cell renders that measure
    # through the same decode as any other grouped surface.
    assert cells[("west", "direct")] == {"count": 2, "sum": {"amount": "140"}, "avg": {"amount": 70.0}}
    assert cells[("north", "direct")] == {"count": 2, "sum": {"amount": "40"}, "avg": {"amount": 20.0}}

    rows = _by_key(data["rowTotals"], "region")
    assert rows["west"] == {"count": 3, "sum": {"amount": "200"}, "avg": {"amount": 200 / 3}}
    columns = _by_key(data["columnTotals"], "channel")
    assert columns["direct"] == {"count": 4, "sum": {"amount": "180"}, "avg": {"amount": 45.0}}
    assert data["grandTotal"]["aggregate"] == {
        "count": 6,
        "sum": {"amount": "310"},
        "avg": {"amount": 310 / 6},
    }
    assert data["rowCount"] == 3

    # Additive measures reconcile across the axes …
    assert sum(int(cell["sum"]["amount"]) for cell in cells.values()) == 310
    assert sum(int(row["sum"]["amount"]) for row in rows.values()) == 310
    assert sum(int(column["sum"]["amount"]) for column in columns.values()) == 310
    # … and the non-additive one is exact only because the server computed it:
    # averaging the west row's two cell averages would give 65, not 200/3.
    assert rows["west"]["avg"]["amount"] != (70.0 + 60.0) / 2


def test_query_count_follows_axis_combinations_not_cell_count(pivot_tables: None) -> None:
    """A denser fact table costs the same queries: one per aliased axis combination."""

    del pivot_tables
    schema = _pivot_schema()

    _seed()
    with system_context(reason="test.pivot.count.small"), CaptureQueriesContext(connection) as small:
        sparse = result_data(execute_schema(schema, PIVOT_DOCUMENT, PIVOT_VARIABLES))

    _clear_model_tables((PivotSale,))
    _seed(
        tuple(
            (f"region-{row}", f"channel-{column}", "2026-01-08", row * 10 + column)
            for row in range(6)
            for column in range(5)
        )
    )
    with system_context(reason="test.pivot.count.large"), CaptureQueriesContext(connection) as large:
        dense = result_data(execute_schema(schema, PIVOT_DOCUMENT, PIVOT_VARIABLES))

    assert len(sparse["cells"]) == 4
    assert len(dense["cells"]) == 30
    # Five aliased aggregations (cells, row subtotals, column totals, grand
    # total, row cardinality) — a 30-cell matrix costs exactly what a 4-cell one does.
    assert len(small.captured_queries) == len(large.captured_queries) == 5


MULTI_LEVEL_DOCUMENT = """
query MultiLevelPivot(
  $rowsByColumns: [PivotSaleTypeGroupBySpec!]!
  $rowsByYear: [PivotSaleTypeGroupBySpec!]!
  $rows: [PivotSaleTypeGroupBySpec!]!
  $regionsByColumns: [PivotSaleTypeGroupBySpec!]!
  $regionsByYear: [PivotSaleTypeGroupBySpec!]!
  $regions: [PivotSaleTypeGroupBySpec!]!
  $columns: [PivotSaleTypeGroupBySpec!]!
  $years: [PivotSaleTypeGroupBySpec!]!
) {
  cells: pivot_sales_groups(group_by: $rowsByColumns) {
    key { region channel booked_at_year booked_at_month }
    aggregate { sum { amount } }
  }
  cellsByYear: pivot_sales_groups(group_by: $rowsByYear) {
    key { region channel booked_at_year }
    aggregate { sum { amount } }
  }
  rowTotals: pivot_sales_groups(group_by: $rows) {
    key { region channel }
    aggregate { sum { amount } }
  }
  regionsByColumns: pivot_sales_groups(group_by: $regionsByColumns) {
    key { region booked_at_year booked_at_month }
    aggregate { sum { amount } }
  }
  regionsByYear: pivot_sales_groups(group_by: $regionsByYear) {
    key { region booked_at_year }
    aggregate { sum { amount } }
  }
  regionTotals: pivot_sales_groups(group_by: $regions) {
    key { region }
    aggregate { sum { amount } }
  }
  columnTotals: pivot_sales_groups(group_by: $columns) {
    key { booked_at_year booked_at_month }
    aggregate { sum { amount } }
  }
  yearTotals: pivot_sales_groups(group_by: $years) {
    key { booked_at_year }
    aggregate { sum { amount } }
  }
  grandTotal: pivot_sales_aggregate { aggregate { sum { amount } } }
}
"""

ROW_AXES = ({"field": "REGION"}, {"field": "CHANNEL"})
COLUMN_AXES = (
    {"field": "BOOKED_AT", "granularity": "YEAR"},
    {"field": "BOOKED_AT", "granularity": "MONTH"},
)
MULTI_LEVEL_VARIABLES: dict[str, Any] = {
    "rowsByColumns": [*ROW_AXES, *COLUMN_AXES],
    "rowsByYear": [*ROW_AXES, COLUMN_AXES[0]],
    "rows": [*ROW_AXES],
    "regionsByColumns": [ROW_AXES[0], *COLUMN_AXES],
    "regionsByYear": [ROW_AXES[0], COLUMN_AXES[0]],
    "regions": [ROW_AXES[0]],
    "columns": [*COLUMN_AXES],
    "years": [COLUMN_AXES[0]],
}


def _summed(rows: list[dict[str, Any]], *keys: str) -> dict[tuple[Any, ...], int]:
    return {tuple(row["key"][key] for key in keys): int(row["aggregate"]["sum"]["amount"]) for row in rows}


def test_multi_level_axes_cost_one_query_per_axis_prefix_pair(pivot_tables: None) -> None:
    """Two row levels by two column levels: every subtotal is a sibling alias, priced by depth."""

    del pivot_tables
    schema = _pivot_schema()
    _seed()

    with system_context(reason="test.pivot.levels"), CaptureQueriesContext(connection) as queries:
        data = result_data(execute_schema(schema, MULTI_LEVEL_DOCUMENT, MULTI_LEVEL_VARIABLES))

    # (row prefixes: none / region / region+channel) × (column prefixes: none /
    # year / year+month) — the grand total rides the aggregate root, so eight
    # grouped calls plus one aggregate. The price is set by axis depth alone.
    assert len(queries.captured_queries) == 9

    cells = _summed(data["cells"], "region", "channel", "booked_at_year", "booked_at_month")
    by_year = _summed(data["cellsByYear"], "region", "channel", "booked_at_year")
    row_totals = _summed(data["rowTotals"], "region", "channel")
    region_totals = _summed(data["regionTotals"], "region")
    column_totals = _summed(data["columnTotals"], "booked_at_year", "booked_at_month")
    year_totals = _summed(data["yearTotals"], "booked_at_year")
    grand_total = int(data["grandTotal"]["aggregate"]["sum"]["amount"])

    def rolled(source: dict[tuple[Any, ...], int], depth: int) -> dict[tuple[Any, ...], int]:
        rolled_up: dict[tuple[Any, ...], int] = {}
        for key, value in source.items():
            rolled_up[key[:depth]] = rolled_up.get(key[:depth], 0) + value
        return rolled_up

    # Each coarser level is the finer level rolled up, and the innermost cells
    # roll all the way to the grand total: the axes agree at every depth.
    assert rolled(cells, 3) == by_year
    assert rolled(by_year, 2) == row_totals
    assert rolled(row_totals, 1) == region_totals
    assert rolled(column_totals, 1) == year_totals
    assert sum(region_totals.values()) == sum(year_totals.values()) == grand_total


def test_axis_window_scopes_the_cell_call_to_the_visible_rows(pivot_tables: None) -> None:
    """A paged row axis plus a `where` echo loads only the visible rows' cells."""

    del pivot_tables
    schema = _pivot_schema()
    _seed()

    with system_context(reason="test.pivot.window.axis"):
        axis = result_data(
            execute_schema(
                schema,
                """
                query RowWindow($rowAxes: [PivotSaleTypeGroupBySpec!]!, $order: [PivotSaleTypeGroupOrder!]) {
                  rowCount: pivot_sales_groups_count(group_by: $rowAxes)
                  rows: pivot_sales_groups(group_by: $rowAxes, order_by: $order, limit: 2, offset: 0) {
                    key { region }
                    aggregate { sum { amount } }
                  }
                }
                """,
                {
                    "rowAxes": [{"field": "REGION"}],
                    "order": [{"field": "sum_amount", "direction": "DESC"}],
                },
            )
        )

    # Exact cardinality before the window, so the pivot can page its row axis.
    assert axis["rowCount"] == 3
    window = [row["key"]["region"] for row in axis["rows"]]
    assert window == ["west", "south"]

    with system_context(reason="test.pivot.window.cells"):
        windowed = result_data(
            execute_schema(
                schema,
                """
                query WindowCells($cellAxes: [PivotSaleTypeGroupBySpec!]!, $where: pivot_sales_bool_exp) {
                  cells: pivot_sales_groups(group_by: $cellAxes, where: $where) {
                    key { region channel }
                    aggregate { sum { amount } }
                  }
                }
                """,
                {
                    "cellAxes": [{"field": "REGION"}, {"field": "CHANNEL"}],
                    "where": {"_or": [{"region": {"_eq": region}} for region in window]},
                },
            )
        )

    assert set(_cells(windowed["cells"])) == {
        ("west", "direct"),
        ("west", "partner"),
        ("south", "partner"),
    }


def test_time_axis_buckets_carry_the_range_that_drills_a_cell_down(pivot_tables: None) -> None:
    """A granular time column axis buckets by month and hands back its drilldown range."""

    del pivot_tables
    schema = _pivot_schema()
    _seed()

    with system_context(reason="test.pivot.time.axis"):
        data = result_data(
            execute_schema(
                schema,
                """
                query TimePivot($cellAxes: [PivotSaleTypeGroupBySpec!]!) {
                  cells: pivot_sales_groups(group_by: $cellAxes) {
                    key { region booked_at_month booked_at_month_range { from to } }
                    aggregate { count sum { amount } }
                  }
                }
                """,
                {
                    "cellAxes": [
                        {"field": "REGION"},
                        {"field": "BOOKED_AT", "granularity": "MONTH"},
                    ],
                },
            )
        )

    cells = {(row["key"]["region"], row["key"]["booked_at_month"][:7]): row for row in data["cells"]}
    assert set(cells) == {
        ("west", "2026-01"),
        ("west", "2026-02"),
        ("north", "2026-01"),
        ("north", "2026-02"),
        ("south", "2026-02"),
    }
    cell = cells[("west", "2026-01")]
    assert cell["aggregate"] == {"count": 2, "sum": {"amount": "140"}}

    bucket_range = cell["key"]["booked_at_month_range"]
    with system_context(reason="test.pivot.time.drilldown"):
        drilled = result_data(
            execute_schema(
                schema,
                """
                query Drilldown($where: pivot_sales_bool_exp) {
                  pivot_sales(where: $where) { id amount }
                }
                """,
                {
                    "where": {
                        "region": {"_eq": "west"},
                        "booked_at": {"_gte": bucket_range["from"], "_lt": bucket_range["to"]},
                    },
                },
            )
        )

    # The cell's own key drills down to exactly the records it counted.
    assert len(drilled["pivot_sales"]) == cell["aggregate"]["count"]
    assert sum(row["amount"] for row in drilled["pivot_sales"]) == int(cell["aggregate"]["sum"]["amount"])
