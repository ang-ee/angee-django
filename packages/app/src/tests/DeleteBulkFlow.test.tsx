// @vitest-environment happy-dom

import type {
  Row,
} from "@angee/resources";
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
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  } from "@tanstack/react-router";
import {
  QueryClient,
  QueryClientProvider,
  } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useMemo,
  type ReactElement,
  type ReactNode,
  } from "react";
import { afterEach,
  describe,
  expect,
  test,
  vi } from "vitest";
import {
  ModelMetadataProvider,
} from "@angee/resources";
import {
  OperationDocumentsProvider,
  extractDeletePreview,
} from "@angee/refine";
import {
  AppRuntimeProvider,
} from "@angee/ui/runtime";
import {
  type SchemaFieldMetadata,
} from "@angee/resources";

import { baseIcons } from "@angee/ui/chrome/icon-registry";
import { parseFlatSearch, stringifyFlatSearch } from "../create-app";
import { ToastProvider } from "@angee/ui/feedback/index";
import { DeletePreviewTree } from "@angee/ui/views/DeletePreviewTree";
import { ListView, type ListColumn } from "@angee/ui/views/ListView";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    { id: "sale-1", title: "First sale" },
    { id: "sale-2", title: "Second sale" },
  ] satisfies Row[],
  mutate: vi.fn(),
}));

vi.mock("@angee/ui/runtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui/runtime")>();
  return {
    ...actual,
  };
});

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useCustomMutation: () => ({
      mutateAsync: async ({ values }: { values?: { id?: string; confirm?: boolean } }) => ({
        data: { deleteSalePreview: await sdkMocks.mutate(values ?? {}) },
      }),
      mutation: { isPending: false, error: null, reset: vi.fn() },
    }),
    useCustom: () => ({
      result: { data: undefined },
      query: { isFetching: false, error: null, refetch: vi.fn() },
    }),
    useCan: () => ({
      data: { can: true },
      isLoading: false,
      error: null,
    }),
    useInvalidate: () => vi.fn(async () => undefined),
  };
});

vi.mock("@refinedev/react-table", async () => {
  const TanStackTable = await import("@tanstack/react-table");
  return {
    useTable: (options: {
      columns?: unknown[];
      state?: { pagination?: { pageIndex?: number; pageSize?: number } };
      getRowId?: (row: Row, index: number) => string;
      refineCoreProps?: {
        pagination?: { currentPage?: number; pageSize?: number };
        queryOptions?: { enabled?: boolean };
      };
      onColumnVisibilityChange?: (updater: unknown) => void;
    }) => {
      const props = options.refineCoreProps ?? {};
      const pageSize =
        props.pagination?.pageSize ?? options.state?.pagination?.pageSize ?? 50;
      const requestedPage =
        props.pagination?.currentPage
        ?? ((options.state?.pagination?.pageIndex ?? 0) + 1);
      const active = props.queryOptions?.enabled !== false;
      const pageCount = Math.max(1, Math.ceil(sdkMocks.rows.length / pageSize));
      const page = Math.min(pageCount, Math.max(1, requestedPage));
      const rows = active
        ? sdkMocks.rows.slice((page - 1) * pageSize, page * pageSize)
        : [];
      const pagination = {
        pageIndex: options.state?.pagination?.pageIndex ?? page - 1,
        pageSize,
      };
      const reactTable = TanStackTable.createTable({
        data: rows,
        columns: (options.columns ?? []) as never[],
        getCoreRowModel: TanStackTable.getCoreRowModel(),
        getRowId: options.getRowId as never,
        state: {
          ...(options.state ?? {}),
          columnPinning: { left: [], right: [] },
          pagination,
          rowSelection: {},
        },
        onStateChange: () => undefined,
        renderFallbackValue: null,
      });
      return {
        reactTable,
        refineCore: {
          result: { data: rows, total: active ? sdkMocks.rows.length : undefined },
          tableQuery: {
            isFetching: false,
            error: null,
            refetch: vi.fn(),
          },
        },
      };
    },
  };
});

const columns = [
  { field: "title", header: "Title" },
] satisfies readonly ListColumn[];

describe("bulk delete flow", () => {
  afterEach(async () => {
    await act(async () => {
      cleanup();
      await nextTask();
    });
    sdkMocks.mutate.mockReset();
  });

  test("SelectionBar shows Delete when rows are selected", async () => {
    sdkMocks.mutate.mockResolvedValue(previewFor("sale-1", "First sale"));

    render(
      <TestUrlState>
        <ListView resource="sales.Sale" columns={columns} />
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);

    expect(screen.getByText("1 selected")).toBeTruthy();
    const deleteButton = screen.getByRole("button", { name: "Delete" });
    expect(deleteButton).toBeTruthy();
    expect(deleteButton.querySelector("svg")).toBeTruthy();
  });

  test("SelectionBar omits Delete when the resource exposes no delete root", async () => {
    render(
      <TestUrlState>
        <NoDeleteMetadata>
          <ListView resource="sales.Sale" columns={columns} />
        </NoDeleteMetadata>
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);

    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("clicking Delete runs dry-run preview and opens the tree dialog", async () => {
    sdkMocks.mutate.mockResolvedValue(previewFor("sale-1", "First sale"));

    render(
      <TestUrlState>
        <ListView resource="sales.Sale" columns={columns} />
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(sdkMocks.mutate).toHaveBeenCalledWith({ id: "sale-1", confirm: false }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete 1 records?")).toBeTruthy();
    expect(
      within(dialog).getByRole("button", { name: "Delete" }).querySelector("svg"),
    ).toBeTruthy();
    expect(within(dialog).getByText("First sale")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "2 line items" })).toBeTruthy();
    expect(within(dialog).getByText("Line 1")).toBeTruthy();
  });

  test("confirming deletes and clears selection", async () => {
    sdkMocks.mutate.mockResolvedValue(previewFor("sale-1", "First sale"));

    render(
      <TestUrlState>
        <ListView resource="sales.Sale" columns={columns} />
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(sdkMocks.mutate).toHaveBeenCalledWith({ id: "sale-1", confirm: true }),
    );
    await waitFor(() => expect(screen.queryByText("1 selected")).toBeNull());
  });

  test("tree renders nested nodes collapsibly", () => {
    render(
      <TestLayout>
        <DeletePreviewTree
          nodes={[
            extractDeletePreview(
              { sale: previewFor("sale-1", "First sale") },
              "sale",
            )!.root,
          ]}
        />
      </TestLayout>,
    );

    expect(screen.getByText("Line 1")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "2 line items" }));

    expect(screen.queryByText("Line 1")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "2 line items" }));

    expect(screen.getByText("Line 1")).toBeTruthy();
  });
});

// The wire payload `extractDeletePreview` parses is snake_case (the schema's
// Hasura naming); the views consume the camelCase shape it returns.
function previewFor(id: string, objectLabel: string) {
  return {
    total_deleted_count: 3,
    has_blockers: false,
    deleted: [
      { label: "sales", count: 1 },
      { label: "line items", count: 2 },
    ],
    updated: [],
    blocked: [],
    root: {
      label: "sale",
      object_label: objectLabel,
      object_id: id,
      children: [
        {
          label: "line items",
          object_label: "2 line items",
          object_id: null,
          children: [
            {
              label: "line item",
              object_label: "Line 1",
              object_id: "line-1",
              children: [],
            },
          ],
        },
      ],
    },
  };
}

function nextTask(): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, 0);
  });
}

const TestUrlStateContext = createContext<{ children: ReactNode } | null>(null);

function TestUrlState({ children }: { children: ReactNode }): ReactElement {
  const router = useMemo(() => {
    const rootRoute = createRootRoute({ component: TestRootRoute });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: TestScreen,
    });
    return createRouter({
      routeTree: rootRoute.addChildren([indexRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
      parseSearch: parseFlatSearch,
      stringifySearch: stringifyFlatSearch,
    });
  }, []);

  return (
    <TestUrlStateContext.Provider value={{ children }}>
      <RouterProvider router={router} />
    </TestUrlStateContext.Provider>
  );
}

function TestRootRoute(): ReactElement {
  return (
    <TestLayout>
      <Outlet />
    </TestLayout>
  );
}

function TestScreen(): ReactElement | null {
  const context = useContext(TestUrlStateContext);
  return context ? <>{context.children}</> : null;
}

function TestLayout({ children }: { children: ReactNode }): ReactElement {
  const queryClient = useMemo(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      }),
    [],
  );
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      <QueryClientProvider client={queryClient}>
        <OperationDocumentsProvider documents={SALE_OPERATION_DOCUMENTS}>
          <ModelMetadataProvider metadata={SALE_METADATA}>
            <ToastProvider>{children}</ToastProvider>
          </ModelMetadataProvider>
        </OperationDocumentsProvider>
      </QueryClientProvider>
    </AppRuntimeProvider>
  );
}

function NoDeleteMetadata({ children }: { children: ReactNode }): ReactElement {
  return (
    <ModelMetadataProvider
      metadata={{
        types: {
          SaleType: {
            typeName: "SaleType",
            fields: {
              title: { name: "title", kind: "scalar", scalar: "String" },
            },
            rootFields: {
              detail: "sale",
              list: "sales",
              aggregate: "saleAggregate",
            },
            resource: {
              schemaName: "console",
              modelLabel: "sales.Sale",
              appLabel: "sales",
              modelName: "Sale",
              publicIdField: "id",
              roots: {
                list: "sales",
                detail: "sale",
                aggregate: "saleAggregate",
              },
              typeNames: {
                node: "SaleType",
                filter: "SaleFilter",
                order: "SaleOrder",
                aggregate: "SaleAggregate",
              },
              capabilities: ["list", "aggregate"],
              filterFields: [],
              orderFields: ["title"],
              aggregateFields: ["id"],
              groupByFields: [],
              relationAxes: [],
            },
          },
        },
      }}
    >
      {children}
    </ModelMetadataProvider>
  );
}

const SALE_METADATA: SchemaFieldMetadata = {
  types: {
    SaleType: {
      typeName: "SaleType",
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
      },
      rootFields: {
        detail: "sale",
        list: "sales",
        aggregate: "saleAggregate",
        delete: "deleteSale",
      },
      resource: {
        schemaName: "console",
        modelLabel: "sales.Sale",
        appLabel: "sales",
        modelName: "Sale",
        publicIdField: "id",
        roots: {
          list: "sales",
          detail: "sale",
          aggregate: "saleAggregate",
          delete: "deleteSale",
          deletePreview: "deleteSalePreview",
        },
        typeNames: {
          node: "SaleType",
          filter: "SaleFilter",
          order: "SaleOrder",
          aggregate: "SaleAggregate",
          deletePayload: "SaleDeletePreview",
        },
        capabilities: ["list", "aggregate", "delete"],
        filterFields: [],
        orderFields: ["title"],
        aggregateFields: ["id"],
        groupByFields: [],
        relationAxes: [],
      },
    },
  },
};

const SALE_OPERATION_DOCUMENTS = {
  console: {
    deletePreviews: {
      "sales.Sale": { kind: "Document", definitions: [] },
    },
  },
};
