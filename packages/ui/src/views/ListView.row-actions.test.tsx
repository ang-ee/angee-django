// @vitest-environment happy-dom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import type { TypedDocumentNode } from "@angee/refine";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TableOptions } from "@tanstack/react-table";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../feedback";
import { defineRowAction } from "./RowActions";

interface TestRow extends Record<string, unknown> {
  id: string;
  name: string;
}

interface RemoveResult {
  remove: TestRow | null;
}

interface RemoveVariables extends Record<string, unknown> {
  id: string;
}

const REMOVE_DOCUMENT = {} as TypedDocumentNode<RemoveResult, RemoveVariables>;

const harness = vi.hoisted(() => ({
  rows: [
    { id: "row-a", name: "Alpha" },
    { id: "row-b", name: "Beta" },
  ] as TestRow[],
  fetching: false,
  mutate: vi.fn(),
  refetch: vi.fn(),
  setFilters: vi.fn(),
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useList: () => ({
      result: { data: harness.rows, total: harness.rows.length },
      query: {
        error: null,
        isFetching: harness.fetching,
        refetch: vi.fn(),
      },
    }),
    useCan: () => ({ data: { can: false }, isLoading: false, error: null }),
    useInvalidate: () => vi.fn(async () => undefined),
  };
});

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeAggregate: () => ({
      aggregate: { key: null, count: harness.rows.length },
      fetching: false,
      error: null,
    }),
    useAngeeFacets: () => ({ facets: {}, fetching: false, error: null }),
    useAngeeGroupBy: () => ({
      groups: [],
      total: undefined,
      fetching: false,
      error: null,
    }),
    useOperationDocuments: () => ({}),
  };
});

vi.mock("@refinedev/react-table", async () => {
  const { getCoreRowModel, useReactTable } = await import("@tanstack/react-table");
  return {
    useTable: (options: Record<string, unknown>) => ({
      refineCore: {
        result: { data: harness.rows, total: harness.rows.length },
        filters: [],
        setFilters: harness.setFilters,
        tableQuery: {
          error: null,
          isFetching: harness.fetching,
          refetch: harness.refetch,
        },
      },
      reactTable: useReactTable<TestRow>({
        ...options,
        columns: options.columns ?? [],
        data: harness.rows,
        getCoreRowModel: getCoreRowModel(),
      } as TableOptions<TestRow>),
    }),
  };
});

vi.mock("./authored-resource-mutation", () => ({
  useAuthoredResourceMutation: () => [
    harness.mutate,
    { fetching: false, error: null },
  ],
}));

vi.mock("./useBulkDelete", () => ({
  useBulkDelete: () => ({
    canDelete: false,
    deleteInitiate: vi.fn(),
    isPending: false,
    isPreviewOpen: false,
    onCancel: vi.fn(),
    onConfirm: vi.fn(),
    previewBlockedRecordCount: 0,
    previewOverflowCount: 0,
    previewRecordCount: 0,
    previewState: null,
  }),
}));

import { ListView } from "./ListView";

describe("ListView row actions", () => {
  beforeEach(() => {
    harness.rows = [
      { id: "row-a", name: "Alpha" },
      { id: "row-b", name: "Beta" },
    ];
    harness.fetching = false;
    harness.mutate.mockReset();
    harness.mutate.mockRejectedValue(new Error("transport unavailable"));
    harness.refetch.mockReset();
    harness.setFilters.mockReset();
  });

  afterEach(() => cleanup());

  test("owns confirm, double-click gating, mutation toast, and loaded table alignment", async () => {
    renderList();

    const [action] = await screen.findAllByRole("button", { name: "Remove row" });
    if (!action) throw new Error("Expected the first row action.");
    act(() => {
      fireEvent.click(action);
      fireEvent.click(action);
    });

    const dialogs = await screen.findAllByRole("alertdialog");
    expect(dialogs).toHaveLength(1);
    expect((action as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(dialogs[0]!).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(harness.mutate).toHaveBeenCalledTimes(1));
    expect(harness.mutate).toHaveBeenCalledWith({ id: "row-a" });
    expect(await screen.findByText("transport unavailable", {
      selector: "p[data-type='danger']",
    })).toBeTruthy();
    expect(screen.getByText("Remove failed", {
      selector: "[data-type='danger']",
    })).toBeTruthy();

    const table = screen.getByRole("table");
    expect(table.querySelectorAll("thead th")).toHaveLength(3);
    for (const row of table.querySelectorAll("tbody tr")) {
      expect(row.querySelectorAll("td")).toHaveLength(3);
    }
    expect(table.querySelectorAll("tfoot tr td")).toHaveLength(3);
  });

  test("keeps action-column colSpans aligned while the first page is loading", () => {
    harness.rows = [];
    harness.fetching = true;

    renderList();

    const table = screen.getByRole("table");
    expect(table.querySelectorAll("thead th")).toHaveLength(3);
    const status = table.querySelector("tbody td[role='status']");
    expect(status?.getAttribute("colspan")).toBe("3");
    for (const row of table.querySelectorAll("tbody tr[aria-hidden='true']")) {
      expect(row.querySelectorAll("td")).toHaveLength(3);
    }
  });
});

function renderList(): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ModelMetadataProvider metadata={TEST_METADATA}>
        <ModalsHost>
          <ToastProvider>
            <ListView<TestRow>
              resource="test.Row"
              scope="local"
              columns={[{ field: "name", header: "Name", aggregate: "count" }]}
              rowActions={[
                defineRowAction({
                  kind: "authored",
                  id: "remove",
                  label: "Remove row",
                  icon: "trash",
                  variant: "danger",
                  document: REMOVE_DOCUMENT,
                  variables: (row: TestRow) => ({ id: row.id }),
                  invalidateModels: ["test.Row"],
                  confirm: {
                    title: (row: TestRow) => `Remove ${row.name}?`,
                    body: () => "This cannot be undone.",
                    confirm: () => "Remove",
                  },
                  toast: {
                    title: () => "Remove failed",
                    description: () => "Could not remove row.",
                  },
                  pendingPolicy: "active-row",
                }),
              ]}
            />
          </ToastProvider>
        </ModalsHost>
      </ModelMetadataProvider>
    </QueryClientProvider>,
  );
}

const TEST_METADATA = schemaFieldMetadataFromDataResources([
  {
    schemaName: "console",
    modelLabel: "test.Row",
    appLabel: "test",
    modelName: "row",
    publicIdField: "id",
    roots: { list: "test_rows", aggregate: "test_rows_aggregate" },
    typeNames: { node: "TestRowType" },
    recordRepresentation: "name",
    capabilities: ["list", "aggregate"],
    fields: [
      field("id", "ID"),
      field("name", "String"),
    ],
    filterFields: ["id", "name"],
    orderFields: ["name"],
    aggregateFields: ["id", "name"],
    groupByFields: [],
    relationAxes: [],
  },
]);

function field(name: string, scalar: string) {
  return {
    name,
    kind: "scalar" as const,
    scalar,
    readable: true,
    filterable: true,
    sortable: true,
    aggregatable: true,
    groupable: false,
    nullable: false,
    creatable: true,
    updatable: true,
    requiredOnCreate: false,
  };
}
