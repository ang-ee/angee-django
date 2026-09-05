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
import { afterEach, describe, expect, test, vi } from "vitest";
import { OperationDocumentsProvider, type GroupByBatchScope } from "@angee/refine";

import { ToastProvider } from "../../feedback";
import { ResourceViewProvider, useResourceView } from "./resource-view-context";
import {
  useClientResourceViewSurface,
  useGroupedResourceViewSurface,
  useResourceViewSurface,
  type ResourceListSnapshot,
} from "./resource-view-surface";
import type { ColumnDescriptor } from "../page";

const tableMocks = vi.hoisted(() => ({
  activeFilters: [] as unknown[][],
  rows: [
    { id: "note_1", title: "First", status: "active" },
  ] as Row[],
  refetch: vi.fn(),
  mutateAsync: vi.fn(),
  pageSizes: [] as number[],
  subgroupTotal: null as number | null,
  subgroupPages: [] as number[],
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  interface KeyBuilder {
    data: () => KeyBuilder;
    resource: () => KeyBuilder;
    action: () => KeyBuilder;
    params: () => KeyBuilder;
    get: () => readonly unknown[];
  }
  const keyBuilder: KeyBuilder = {
    data: () => keyBuilder,
    resource: () => keyBuilder,
    action: () => keyBuilder,
    params: () => keyBuilder,
    get: () => [],
  };
  return {
    ...actual,
    useList: ({ resource, filters, pagination }: { resource?: string; filters?: unknown[]; pagination?: { pageSize?: number } }) => {
      if (resource === "notes") {
        tableMocks.activeFilters.push(filters ?? []);
        if (pagination?.pageSize !== undefined) tableMocks.pageSizes.push(pagination.pageSize);
      }
      return {
        result: { data: tableMocks.rows, total: tableMocks.rows.length },
        query: { isFetching: false, refetch: tableMocks.refetch },
      };
    },
    useUpdate: () => ({ mutateAsync: tableMocks.mutateAsync }),
    useDataProvider: () => () => ({
      custom: vi.fn(),
      getList: vi.fn(),
    }),
    useKeys: () => ({ keys: () => keyBuilder }),
    useResourceSubscription: () => undefined,
  };
});

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQueries: ({ queries }: { queries: readonly unknown[] }) =>
      queries.map(() => ({
        data: {
          notes_groups: [
            { key: { status: "active" }, aggregate: { count: 1 } },
          ],
          totalCount: 1,
        },
        error: null,
        isFetching: false,
        refetch: vi.fn(),
      })),
  };
});



vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: () => ({
    getTotalSize: () => 0,
    getVirtualItems: () => [],
    measureElement: vi.fn(),
    scrollToIndex: vi.fn(),
  }),
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeGroupByBatch: (...args: Parameters<typeof actual.useAngeeGroupByBatch>) => {
      if (tableMocks.subgroupTotal === null) return actual.useAngeeGroupByBatch(...args);
      return new Map(args[1].map((scope: GroupByBatchScope, index) => {
        const page = scope.query.page ?? 1;
        if (index > 0) tableMocks.subgroupPages.push(page);
        return [scope.key, {
          buckets: index === 0
            ? [{ key: { status: "active" }, count: 5 }]
            : page <= Math.ceil(tableMocks.subgroupTotal! / 2)
              ? [{ key: { title: "subgroup" }, count: 1 }]
              : [],
          count: 5,
          totalCount: index === 0 ? 1 : tableMocks.subgroupTotal!,
          fetching: false,
          error: null,
          refetch: vi.fn(),
        }];
      }));
    },
    useAngeeAggregate: () => ({
      aggregate: null,
      fetching: false,
      error: null,
      refetch: vi.fn(),
    }),
  };
});

afterEach(() => {
  cleanup();
  tableMocks.activeFilters = [];
  tableMocks.refetch.mockClear();
  tableMocks.mutateAsync.mockClear();
  tableMocks.pageSizes = [];
  tableMocks.rows = [{ id: "note_1", title: "First", status: "active" }];
  tableMocks.subgroupTotal = null;
  tableMocks.subgroupPages = [];
});

describe("useClientResourceViewSurface", () => {
  test("client row-model pagination retains page sizes above the server cap", () => {
    tableMocks.rows = Array.from({ length: 250 }, (_, index) => ({
      id: `note_${index + 1}`,
      title: `Note ${index + 1}`,
      status: "active",
    }));
    render(
      <ToastProvider>
        <ResourceViewProvider scope="local" initialState={{ pageSize: 200 }}>
          <ClientSurfaceProbe />
        </ResourceViewProvider>
      </ToastProvider>,
    );

    expect(screen.getByTestId("client-page-size").textContent).toBe("200:200:200");
  });
});

function ClientSurfaceProbe(): React.ReactElement {
  const resourceView = useResourceView();
  const surface = useClientResourceViewSurface({
    resource: "notes.Note",
    columns: NOTE_COLUMNS,
    resourceView,
    modelMetadata: CLIENT_NOTE_METADATA,
  });
  return (
    <span data-testid="client-page-size">
      {resourceView.state.pagination.pageSize}:{surface.list.pageSize}:{surface.rows.length}
    </span>
  );
}

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

  test("server pagination clamps oversized native state before query and navigation", async () => {
    const onListStateChange = vi.fn();
    render(
      <ToastProvider>
        <ResourceViewProvider scope="local" initialState={{ pageSize: 200 }}>
          <SurfaceProbe onListStateChange={onListStateChange} />
        </ResourceViewProvider>
      </ToastProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("flat-page-size").textContent).toBe("100:100"));
    expect(tableMocks.pageSizes.at(-1)).toBe(100);
    expect((onListStateChange.mock.calls.at(-1)?.[0] as ResourceListSnapshot<Row>)
      .navigationScope?.pageSize).toBe(100);

    fireEvent.click(screen.getByRole("button", { name: "oversize page" }));
    await waitFor(() => expect(screen.getByTestId("flat-page-size").textContent).toBe("100:100"));
    expect(tableMocks.pageSizes.at(-1)).toBe(100);
  });
});

function SurfaceProbe({ onListStateChange }: {
  onListStateChange?: (state: ResourceListSnapshot<Row>) => void;
} = {}): React.ReactElement {
  const resourceView = useResourceView();
  const surface = useResourceViewSurface({
    resource: "notes.Note",
    columns: NOTE_COLUMNS,
    resourceView,
    modelMetadata: NOTE_METADATA,
    onListStateChange,
  });
  return <>
    <button type="button" onClick={() => resourceView.setFilter({})}>clear filter</button>
    <button type="button" onClick={() => resourceView.setPageSize(500)}>oversize page</button>
    <span data-testid="flat-page-size">
      {resourceView.state.pagination.pageSize}:{surface.list.pageSize}
    </span>
  </>;
}

describe("useGroupedResourceViewSurface", () => {
  test("fetches the last valid nested page after a live count shrinks", async () => {
    tableMocks.subgroupTotal = 5;
    const probe = () => (
      <ToastProvider>
        <OperationDocumentsProvider documents={{ console: { groups: { "notes.Note": {} } } }}>
          <ResourceViewProvider scope="local" initialState={{ pageSize: 2 }}>
            <NestedPageProbe />
          </ResourceViewProvider>
        </OperationDocumentsProvider>
      </ToastProvider>
    );
    const { rerender } = render(probe());
    fireEvent.click(await screen.findByRole("button", { name: "open root" }));
    fireEvent.click(await screen.findByRole("button", { name: "last subgroup page" }));
    await waitFor(() => expect(tableMocks.subgroupPages.at(-1)).toBe(3));

    tableMocks.subgroupTotal = 2;
    rerender(probe());
    await waitFor(() => expect(tableMocks.subgroupPages.at(-1)).toBe(1));
    expect(screen.getByTestId("nested-page").textContent).toBe("1");
  });

  test("publishes a snapshot carrying its own scope so the record pager never keeps a stale flat scope", () => {
    const onListStateChange = vi.fn();
    const filter = { drive: { exact: "drive-a" }, is_trashed: { exact: false } };
    renderGroupedProbe(filter, onListStateChange);

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

  test("renders non-empty grouped buckets through the real batch hook", async () => {
    renderGroupedProbe({}, vi.fn());

    await waitFor(() => {
      expect(screen.getByTestId("grouped-kinds").textContent).toContain("groupHeader");
    });
  });
});

function NestedPageProbe(): React.ReactElement {
  const resourceView = useResourceView();
  const surface = useGroupedResourceViewSurface({
    resource: "notes.Note", columns: NOTE_COLUMNS, resourceView, modelMetadata: NOTE_METADATA,
    groupStack: [{ field: "status" }, { field: "title" }],
    defaultExpandedGroups: "none",
  });
  const header = surface.groupedItems.find((item) => item.kind === "groupHeader");
  return <>
    {header ? <button onClick={() => surface.toggleGroup(header.bucketKey)}>open root</button> : null}
    {header?.pager ? <>
      <button onClick={() => surface.setScopePage(header.pager!.pageKey, 3)}>last subgroup page</button>
      <span data-testid="nested-page">{header.pager.page}</span>
    </> : null}
  </>;
}

function renderGroupedProbe(
  filter: Record<string, unknown>,
  onListStateChange: (state: ResourceListSnapshot<Row>) => void,
): void {
  render(
    <ToastProvider>
      <OperationDocumentsProvider
        documents={{ console: { groups: { "notes.Note": {} } } }}
      >
        <ResourceViewProvider resource="notes.Note" scope="local">
          <GroupedProbe filter={filter} onListStateChange={onListStateChange} />
        </ResourceViewProvider>
      </OperationDocumentsProvider>
    </ToastProvider>,
  );
}

function GroupedProbe({
  filter,
  onListStateChange,
}: {
  filter: Record<string, unknown>;
  onListStateChange: (state: ResourceListSnapshot<Row>) => void;
}): React.ReactElement {
  const resourceView = useResourceView();
  const surface = useGroupedResourceViewSurface({
    resource: "notes.Note",
    columns: NOTE_COLUMNS,
    filter,
    resourceView,
    modelMetadata: NOTE_METADATA,
    groupStack: [{ field: "status" }],
    onListStateChange,
  });
  return (
    <div data-testid="grouped-kinds">
      {surface.groupedItems.map((item) => item.kind).join(",")}
    </div>
  );
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
  roots: { list: "notes", groups: "notes_groups" },
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
  groupByFields: ["status", "title"],
  groupDimensions: [
    {
      field: "title", input: "title", key: "title", kind: "column", scalar: "String",
      filter: { kind: "equality", field: "title", valueKey: "title" },
    },
    {
      field: "status",
      input: "status",
      key: "status",
      kind: "column",
      scalar: "String",
      filter: {
        kind: "equality",
        field: "status",
        valueKey: "status",
      },
    },
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

const CLIENT_NOTE_METADATA: ModelMetadata = {
  ...NOTE_METADATA,
  resource: { ...NOTE_RESOURCE, rowModel: "client" },
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
