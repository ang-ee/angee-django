// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { ModelMetadata } from "@angee/metadata";
import type {
  AggregateBucket,
  GroupByBatchScope,
  GroupByRequestOptions,
} from "@angee/refine";
import type { ColumnDescriptor } from "./page";
import type {
  PivotCollectionSurfaceProps,
} from "./pivot-collection-surface";
import type { PivotViewSpec } from "./resource-view-types";
import type { ResourceViewInitialState } from "./resource-view-model";

// Stand in for the grouped/aggregate owners so the axis levels and cell blocks
// the surface asks for are observable, and answer each scope from the fixture
// matrix keyed by the axes it groups on.
const mocks = vi.hoisted(() => ({
  scopes: [] as readonly GroupByBatchScope[],
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeGroupByBatch: (
      _target: unknown,
      scopes: readonly GroupByBatchScope[],
    ) => {
      mocks.scopes = scopes;
      return new Map(
        scopes.map((scope) => [
          scope.key,
          {
            ...groupResultFor(scope.query),
            fetching: false,
            error: null,
            refetch: vi.fn(),
          },
        ]),
      );
    },
    useAngeeAggregate: () => ({
      aggregate: bucket({}, 6, 310),
      fetching: false,
      error: null,
      refetch: vi.fn(),
    }),
  };
});

vi.mock("./resource-operations", () => ({
  useGroupOperation: () => ({
    target: { dataProviderName: "default", root: "sales_groups" },
    document: {},
  }),
  useAggregateOperation: () => ({
    target: { dataProviderName: "default", root: "sales_aggregate" },
    document: {},
  }),
}));

import { PivotCollectionSurface } from "./pivot-collection-surface";
import {
  ResourceViewProvider,
  useResourceView,
  type ResourceViewContextValue,
} from "./resource-view-context";

const METADATA = {
  typeName: "SaleType",
  fields: {
    region: { name: "region", kind: "string" },
    channel: { name: "channel", kind: "string" },
    amount: { name: "amount", kind: "number", label: "Amount" },
  },
  resource: {
    modelLabel: "sales.Sale",
    schemaName: "default",
    roots: { groups: "sales_groups", aggregate: "sales_aggregate" },
    groupByFields: ["region", "channel"],
    groupDimensions: [
      {
        field: "region",
        input: "REGION",
        key: "region",
        kind: "column",
        scalar: "String",
        filter: { kind: "equality", field: "region", valueKey: "region" },
      },
      {
        field: "channel",
        input: "CHANNEL",
        key: "channel",
        kind: "column",
        scalar: "String",
        filter: { kind: "equality", field: "channel", valueKey: "channel" },
      },
    ],
  },
} as unknown as ModelMetadata;

const COLUMNS: readonly ColumnDescriptor[] = [
  { field: "region", header: "Region" },
  { field: "amount", header: "Amount", aggregate: "sum" },
];

const PIVOT: PivotViewSpec = {
  rows: [{ field: "region" }],
  columns: [{ field: "channel" }],
};

/** region × channel amounts; `north/partner` is absent, so the matrix is sparse. */
const CELLS: Record<string, Record<string, [number, number]>> = {
  west: { direct: [2, 140], partner: [1, 60] },
  north: { direct: [2, 40] },
  south: { partner: [1, 70] },
};

function bucket(
  key: Record<string, unknown>,
  count: number,
  amount: number,
): AggregateBucket {
  // `sum` over an integer column arrives as the string-valued BigInt scalar.
  return { key, count, sum: { amount: String(amount) } };
}

function groupResultFor(query: GroupByRequestOptions): {
  count: number;
  totalCount: number;
  buckets: readonly AggregateBucket[];
} {
  const axes = query.dimensions.map((dimension) => dimension.input).join("+");
  const buckets = bucketsForAxes(axes);
  return {
    count: buckets.length,
    totalCount: buckets.length,
    buckets,
  };
}

function bucketsForAxes(axes: string): readonly AggregateBucket[] {
  if (axes === "REGION") {
    return Object.entries(CELLS).map(([region, byChannel]) => {
      const totals = Object.values(byChannel);
      return bucket(
        { region },
        totals.reduce((total, [count]) => total + count, 0),
        totals.reduce((total, [, amount]) => total + amount, 0),
      );
    });
  }
  if (axes === "CHANNEL") {
    const byChannel = new Map<string, [number, number]>();
    for (const channels of Object.values(CELLS)) {
      for (const [channel, [count, amount]] of Object.entries(channels)) {
        const [totalCount, totalAmount] = byChannel.get(channel) ?? [0, 0];
        byChannel.set(channel, [totalCount + count, totalAmount + amount]);
      }
    }
    return [...byChannel].map(([channel, [count, amount]]) =>
      bucket({ channel }, count, amount));
  }
  if (axes === "REGION+CHANNEL") {
    return Object.entries(CELLS).flatMap(([region, channels]) =>
      Object.entries(channels).map(([channel, [count, amount]]) =>
        bucket({ region, channel }, count, amount)));
  }
  return [];
}

let latestResourceView: ResourceViewContextValue | null = null;

type PivotToolbarProps = Pick<
  PivotCollectionSurfaceProps,
  | "activeFilterIds"
  | "customFilterChips"
  | "customFilterFields"
  | "favorites"
  | "filterOptions"
  | "filterText"
  | "textFilterField"
>;

function Harness({
  pivot,
  columns = COLUMNS,
  toolbarProps,
}: {
  pivot: PivotViewSpec;
  columns?: readonly ColumnDescriptor[];
  toolbarProps?: Partial<PivotToolbarProps>;
}): React.ReactElement {
  const resourceView = useResourceView();
  latestResourceView = resourceView;
  return (
    <PivotCollectionSurface
      resource="sales.Sale"
      resourceView={resourceView}
      pivot={pivot}
      columns={columns}
      modelMetadata={METADATA}
      availableViews={["list", "board", "pivot"]}
      emptyContent="No data."
      {...toolbarProps}
    />
  );
}

function renderPivot(
  pivot: PivotViewSpec = PIVOT,
  options: {
    columns?: readonly ColumnDescriptor[];
    initialState?: ResourceViewInitialState;
    toolbarProps?: Partial<PivotToolbarProps>;
  } = {},
): void {
  render(
    <ResourceViewProvider
      scope="local"
      resource="sales.Sale"
      initialState={{ view: "pivot", ...options.initialState }}
    >
      <Harness
        pivot={pivot}
        columns={options.columns}
        toolbarProps={options.toolbarProps}
      />
    </ResourceViewProvider>,
  );
}

/** The axis signature of every scope the surface asked for, in request order. */
function requestedAxes(): string[] {
  return mocks.scopes.map((scope) =>
    scope.query.dimensions.map((dimension) => dimension.input).join("+"));
}

function rowCells(label: string): string[] {
  const row = screen.getByRole("row", { name: new RegExp(`^${label}`) });
  return within(row)
    .getAllByRole("cell")
    .map((cell) => cell.textContent?.trim() ?? "");
}

beforeEach(() => {
  mocks.scopes = [];
  latestResourceView = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("pivot collection surface", () => {
  test("asks for one grouped call per axis level and cell block", () => {
    renderPivot();

    // Row axis, column axis, and the single cell block between them — the price
    // is (rows+1) x (columns+1) calls, set by axis depth, not by cell count.
    expect(requestedAxes()).toEqual(["REGION", "CHANNEL", "REGION+CHANNEL"]);
  });

  test("scopes the cell block to the loaded axis members", () => {
    renderPivot();

    const cellScope = mocks.scopes.find((scope) =>
      scope.query.dimensions.length === 2);
    // The paged row window and the column window are echoed as `where`, so a
    // wide pivot never pulls cells outside what it can render.
    expect(JSON.stringify(cellScope?.query.where)).toContain("_or");
    expect(JSON.stringify(cellScope?.query.where)).toContain("west");
    expect(JSON.stringify(cellScope?.query.where)).toContain("partner");
  });

  test("renders cells, both subtotals and the grand total", () => {
    renderPivot();

    // west: direct 140, partner 60, row total 200.
    expect(rowCells("west")).toEqual(["140", "60", "200"]);
    // north has no partner cell: the sparse cell renders empty, not zero.
    expect(rowCells("north")).toEqual(["40", "", "40"]);
    // The footer carries the column totals and the grand total.
    expect(rowCells("Total")).toEqual(["180", "130", "310"]);
  });

  test("renders measures in the order declared by the pivot", () => {
    renderPivot(
      { ...PIVOT, measures: ["amount", "id"] },
      {
        columns: [
          { field: "id", header: "Count", aggregate: "count" },
          ...COLUMNS,
        ],
      },
    );

    // The source columns declare Count first, but the pivot explicitly puts
    // Amount first: direct amount/count, partner amount/count, row totals.
    expect(rowCells("west")).toEqual(["140", "2", "60", "1", "200", "3"]);
  });

  test("shows and can clear the declared column axis", () => {
    renderPivot();

    fireEvent.click(screen.getByRole("button", { name: "Remove Channel" }));

    expect(latestResourceView?.state.hasColumnStack).toBe(true);
    expect(latestResourceView?.state.columnStack).toEqual([]);
    expect(requestedAxes()).toEqual(["REGION"]);
  });

  test("keeps the active filter model visible in pivot chrome", () => {
    renderPivot(PIVOT, {
      initialState: { filter: { region: { exact: "west" } } },
      toolbarProps: {
        filterOptions: [
          {
            id: "region:west",
            label: "West",
            filter: { region: { exact: "west" } },
          },
        ],
        activeFilterIds: ["region:west"],
        customFilterFields: [
          { id: "region", field: "region", label: "Region", type: "text" },
        ],
        customFilterChips: [{ id: "region:exact", label: "Region is west" }],
        favorites: [{ id: "favorite:west", label: "West only" }],
        filterText: "west",
        textFilterField: "region",
      },
    });

    expect(screen.getByText("West")).toBeTruthy();
    expect(screen.getByText("Region is west")).toBeTruthy();
    expect(screen.getByDisplayValue("west")).toBeTruthy();
  });

  test("ignores an unsupported column axis from URL state", () => {
    expect(() =>
      renderPivot(PIVOT, {
        initialState: { columnStack: [{ field: "unknown" }] },
      }),
    ).not.toThrow();

    expect(requestedAxes()).toEqual(["REGION"]);
  });

  test("drills a cell down into the filtered list", () => {
    renderPivot();

    fireEvent.click(screen.getByRole("button", { name: "west, direct: Amount" }));

    expect(latestResourceView?.state.view).toBe("list");
    expect(latestResourceView?.state.filter).toEqual({
      region: "west",
      channel: "direct",
    });
  });

  test("leaves the cell block unpaged so no cell is clipped out of the matrix", () => {
    renderPivot();

    const [rowScope, , cellScope] = mocks.scopes;
    // The axes page (the pager windows the row axis); the block between them
    // must not, or the page-size clamp would drop cells out of the middle.
    expect(rowScope?.query.pageSize).toBeGreaterThan(0);
    expect(cellScope?.query.pageSize).toBeUndefined();
  });

  test("sorts the row axis by a measure, leaving the column edge in axis order", () => {
    renderPivot();

    // Every column block heads its measure with a sort control; they drive the
    // one row-axis order, so clicking the first is clicking the sort.
    fireEvent.click(screen.getAllByRole("button", { name: /^Sort Amount/ })[0]!);

    expect(latestResourceView?.state.sort).toEqual({ field: "amount", dir: "asc" });
    const scopeFor = (axis: string) =>
      mocks.scopes.find((scope) =>
        scope.query.dimensions.length === 1
        && scope.query.dimensions[0]?.input === axis);
    // A measure sort names the aggregate alias the grouping owner exposes …
    expect(scopeFor("REGION")?.query.orderBy).toEqual([
      { field: "sum_amount", direction: "ASC" },
    ]);
    // … and never reshuffles the headers the reader compares across.
    expect(scopeFor("CHANNEL")?.query.orderBy).toEqual([
      { field: "channel", direction: "ASC", nulls: "LAST" },
    ]);
  });

  test("loads a nested row level only once its parent is expanded", () => {
    renderPivot({ rows: [{ field: "region" }, { field: "channel" }] });

    // Nothing below the outermost axis is knowable yet, so nothing is asked for.
    expect(requestedAxes()).toEqual(["REGION"]);

    fireEvent.click(screen.getByRole("button", { name: "west" }));

    expect(requestedAxes()).toEqual(["REGION", "REGION+CHANNEL"]);
    const nested = mocks.scopes.find((scope) => scope.query.dimensions.length === 2);
    // The nested level is queried under its expanded parent, never the whole axis.
    expect(JSON.stringify(nested?.query.where)).toContain("west");
    expect(screen.getByRole("row", { name: /^direct/ })).toBeTruthy();
  });

  test("swaps the axes onto the URL-owned stacks", () => {
    renderPivot();

    fireEvent.click(screen.getByRole("button", { name: "Swap rows and columns" }));

    expect(latestResourceView?.state.groupStack).toEqual([{ field: "channel" }]);
    expect(latestResourceView?.state.columnStack).toEqual([{ field: "region" }]);
  });
});
