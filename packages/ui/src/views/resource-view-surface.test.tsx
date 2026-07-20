// @vitest-environment happy-dom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type {
  DataResourceFieldMetadata,
  DataResourceMetadata,
  ModelFieldMetadata,
  ModelMetadata,
  Row,
} from "@angee/metadata";
import type { ColumnDef } from "@tanstack/react-table";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ToastProvider } from "../feedback";
import { ResourceViewProvider, useResourceView } from "./resource-view-context";
import {
  useGroupedResourceViewSurface,
  useResourceViewSurface,
  type ResourceListSnapshot,
} from "./resource-view-surface";
import type { ColumnDescriptor } from "./page";

const tableMocks = vi.hoisted(() => ({
  activeFilters: [] as unknown[][],
  rows: [
    { id: "note_1", title: "First", status: "active" },
  ] as Row[],
  refetch: vi.fn(),
  mutateAsync: vi.fn(),
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useList: () => ({
      result: { data: [], total: 0 },
      query: { isFetching: false, refetch: vi.fn() },
    }),
    useUpdate: () => ({ mutateAsync: tableMocks.mutateAsync }),
  };
});

vi.mock("@refinedev/react-table", async () => {
  const React = await import("react");
  const {
    getCoreRowModel,
    useReactTable,
  } = await import("@tanstack/react-table");

  return {
    useTable: (props: {
      refineCoreProps?: {
        filters?: {
          initial?: unknown[];
          permanent?: unknown[];
        };
      };
      columns: ColumnDef<Row>[];
      [key: string]: unknown;
    }) => {
      const { refineCoreProps, ...tableProps } = props;
      const initialFilters = [
        ...(refineCoreProps?.filters?.permanent ?? []),
        ...(refineCoreProps?.filters?.initial ?? []),
      ];
      const [filters, setFilters] = React.useState(initialFilters);
      const replaceFilters = React.useCallback(
        (next: unknown[]) => setFilters(next),
        [],
      );
      const permanentFilters = refineCoreProps?.filters?.permanent ?? [];
      const activeFilters = unionFilters(permanentFilters, filters);
      tableMocks.activeFilters.push(JSON.parse(JSON.stringify(activeFilters)));

      const table = useReactTable({
        ...tableProps,
        columns: props.columns,
        data: tableMocks.rows,
        getCoreRowModel: getCoreRowModel(),
      });

      return {
        reactTable: table,
        refineCore: {
          result: {
            data: tableMocks.rows,
            total: tableMocks.rows.length,
          },
          tableQuery: {
            error: null,
            isFetching: false,
            refetch: tableMocks.refetch,
          },
          filters,
          setFilters: replaceFilters,
        },
      };
    },
  };

  function unionFilters(
    permanentFilters: readonly unknown[],
    filters: readonly unknown[],
  ): unknown[] {
    const byKey = new Map<string, unknown>();
    for (const filter of [...filters, ...permanentFilters]) {
      byKey.set(JSON.stringify(filter), filter);
    }
    return [...byKey.values()];
  }
});

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: () => ({
    getTotalSize: () => 0,
    getVirtualItems: () => [],
    measureElement: vi.fn(),
    scrollToIndex: vi.fn(),
  }),
}));

// The grouped surface's server-grouped data hooks are stubbed empty: the emit it
// publishes (the navigation scope) is independent of the group data, and empty
// batches keep the render model trivial without a Refine/react-query provider.
vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeAggregate: () => ({
      aggregate: null,
      fetching: false,
      error: null,
      refetch: vi.fn(),
    }),
    useAngeeGroupByBatch: () => new Map(),
    useAngeeListBatch: () => new Map(),
  };
});

afterEach(() => {
  cleanup();
  tableMocks.activeFilters = [];
  tableMocks.refetch.mockClear();
  tableMocks.mutateAsync.mockClear();
});

describe("useResourceViewSurface", () => {
  test("row query filters follow cleared resource-view facets after mount", async () => {
    render(
      <ToastProvider>
        <ResourceViewProvider
          resource="notes.Note"
          scope="local"
          initialState={{ filter: { status: "active" } }}
        >
          <SurfaceProbe />
        </ResourceViewProvider>
      </ToastProvider>,
    );

    expect(tableMocks.activeFilters.at(-1)).toEqual([
      { field: "status", operator: "eq", value: "active" },
    ]);

    fireEvent.click(screen.getByRole("button", { name: "clear filter" }));

    await waitFor(() => {
      expect(tableMocks.activeFilters.at(-1)).toEqual([]);
    });
  });
});

function SurfaceProbe(): React.ReactElement {
  const resourceView = useResourceView();
  useResourceViewSurface({
    resource: "notes.Note",
    columns: NOTE_COLUMNS,
    resourceView,
    modelMetadata: NOTE_METADATA,
  });
  return (
    <button type="button" onClick={() => resourceView.setFilter({})}>
      clear filter
    </button>
  );
}

describe("useGroupedResourceViewSurface", () => {
  test("publishes a snapshot carrying its own scope so the record pager never keeps a stale flat scope", () => {
    const onListStateChange = vi.fn();
    const filter = { drive: { exact: "drive-a" }, is_trashed: { exact: false } };
    render(
      <ToastProvider>
        <ResourceViewProvider resource="notes.Note" scope="local">
          <GroupedProbe filter={filter} onListStateChange={onListStateChange} />
        </ResourceViewProvider>
      </ToastProvider>,
    );

    // The flat surface emits on mount; the grouped surface must too, or the pager
    // hook retains whatever flat (single-folder) snapshot was last published.
    expect(onListStateChange).toHaveBeenCalled();
    const snapshot = onListStateChange.mock.calls.at(-1)?.[0] as
      | ResourceListSnapshot<Row>
      | undefined;
    // Empty rows (the grouped render stream owns the visible records) but a
    // non-null scope carrying the grouped filter — the signal the pager replays.
    expect(snapshot?.rows).toEqual([]);
    expect(snapshot?.navigationScope?.filter).toEqual(filter);
  });
});

function GroupedProbe({
  filter,
  onListStateChange,
}: {
  filter: Record<string, unknown>;
  onListStateChange: (state: ResourceListSnapshot<Row>) => void;
}): React.ReactElement {
  const resourceView = useResourceView();
  useGroupedResourceViewSurface({
    resource: "notes.Note",
    columns: NOTE_COLUMNS,
    filter,
    resourceView,
    modelMetadata: NOTE_METADATA,
    groupStack: [{ field: "status" }],
    onListStateChange,
  });
  return <div />;
}

const NOTE_COLUMNS: readonly ColumnDescriptor<Row>[] = [
  { field: "title", header: "Title" },
];

const ID_FIELD: ModelFieldMetadata = {
  name: "id",
  kind: "scalar",
  scalar: "ID",
};

const TITLE_FIELD: ModelFieldMetadata = {
  name: "title",
  kind: "scalar",
  scalar: "String",
};

const STATUS_FIELD: ModelFieldMetadata = {
  name: "status",
  kind: "scalar",
  scalar: "String",
  filterable: true,
};

const NOTE_RESOURCE: DataResourceMetadata = {
  schemaName: "console",
  modelLabel: "notes.Note",
  appLabel: "notes",
  modelName: "note",
  publicIdField: "id",
  roots: { list: "notes" },
  typeNames: { node: "NoteType" },
  capabilities: ["list"],
  fields: [
    resourceField(ID_FIELD, { filterable: true, aggregatable: true }),
    resourceField(TITLE_FIELD),
    resourceField(STATUS_FIELD, { filterable: true }),
  ],
  filterFields: ["status"],
  orderFields: [],
  aggregateFields: [],
  groupByFields: ["status"],
  groupDimensions: [
    { field: "status", input: "status", key: "status", kind: "column", scalar: "String" },
  ],
  relationAxes: [],
};

const NOTE_METADATA: ModelMetadata = {
  typeName: "NoteType",
  fields: {
    id: ID_FIELD,
    title: TITLE_FIELD,
    status: STATUS_FIELD,
  },
  rootFields: { list: "notes" },
  resource: NOTE_RESOURCE,
  recordRepresentation: "title",
};

function resourceField(
  field: ModelFieldMetadata,
  overrides: Partial<DataResourceFieldMetadata> = {},
): DataResourceFieldMetadata {
  return {
    name: field.name,
    kind: field.kind,
    ...(field.scalar ? { scalar: field.scalar } : {}),
    readable: true,
    filterable: false,
    sortable: false,
    aggregatable: false,
    groupable: false,
    creatable: false,
    updatable: false,
    requiredOnCreate: false,
    ...overrides,
  };
}
