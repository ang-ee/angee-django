// @vitest-environment happy-dom

import type {
  Row,
} from "@angee/metadata";
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
} from "@angee/metadata";
import {
  OperationDocumentsProvider,
  extractDeletePreview,
} from "@angee/refine";
import {
  AppRuntimeProvider,
} from "@angee/ui/runtime";
import {
  type SchemaFieldMetadata,
} from "@angee/metadata";
import { withTestResourceInventory, testResourceQuery, testQueryField } from "@angee/metadata/testing";

import { baseIcons } from "@angee/ui/chrome/icon-registry";
import { parseFlatSearch, stringifyFlatSearch } from "../create-app";
import { ToastProvider } from "@angee/ui/feedback/index";
import { DeletePreviewTree } from "@angee/ui/views/DeletePreviewTree";
import { ListView, type ListColumn } from "@angee/ui/views/ListView";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    { id: "record-1", title: "First record" },
    { id: "record-2", title: "Second record" },
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
    useList: () => ({
      result: { data: sdkMocks.rows, total: sdkMocks.rows.length },
      query: { isFetching: false, error: null, refetch: vi.fn() },
    }),
    useCustomMutation: () => ({
      mutateAsync: async ({ values }: { values?: { id?: string; confirm?: boolean } }) => ({
        data: { deleteRecordPreview: await sdkMocks.mutate(values ?? {}) },
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
    sdkMocks.mutate.mockResolvedValue(previewFor("record-1", "First record"));

    render(
      <TestUrlState>
        <ListView resource="example.Record" columns={columns} />
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
          <ListView resource="example.Record" columns={columns} />
        </NoDeleteMetadata>
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);

    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("clicking Delete runs dry-run preview and opens the tree dialog", async () => {
    sdkMocks.mutate.mockResolvedValue(previewFor("record-1", "First record"));

    render(
      <TestUrlState>
        <ListView resource="example.Record" columns={columns} />
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(sdkMocks.mutate).toHaveBeenCalledWith({ id: "record-1", confirm: false }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete 1 records?")).toBeTruthy();
    expect(
      within(dialog).getByRole("button", { name: "Delete" }).querySelector("svg"),
    ).toBeTruthy();
    expect(within(dialog).getByText("First record")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "2 line items" })).toBeTruthy();
    expect(within(dialog).getByText("Line 1")).toBeTruthy();
  });

  test("confirming deletes and clears selection", async () => {
    sdkMocks.mutate.mockResolvedValue(previewFor("record-1", "First record"));

    render(
      <TestUrlState>
        <ListView resource="example.Record" columns={columns} />
      </TestUrlState>,
    );

    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(sdkMocks.mutate).toHaveBeenCalledWith({ id: "record-1", confirm: true }),
    );
    await waitFor(() => expect(screen.queryByText("1 selected")).toBeNull());
  });

  test("tree renders nested nodes collapsibly", () => {
    render(
      <TestLayout>
        <DeletePreviewTree
          nodes={[
            extractDeletePreview(
              { record: previewFor("record-1", "First record") },
              "record",
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
      { label: "records", count: 1 },
      { label: "line items", count: 2 },
    ],
    updated: [],
    blocked: [],
    root: {
      label: "record",
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
        <OperationDocumentsProvider documents={RECORD_OPERATION_DOCUMENTS}>
          <ModelMetadataProvider metadata={RECORD_METADATA}>
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
      metadata={withTestResourceInventory({
        types: {
          RecordType: {
            fields: {
              title: { name: "title", kind: "scalar", scalar: "String" },
            },
            resource: {
              query: testResourceQuery({ identity: { field: "id" }, fields: { "title": testQueryField("title", { scalar: "String", kind: "scalar", filter: null, sort: { field: "title" } }),
                      "id": testQueryField("id", { scalar: "ID", filter: null }) }, axes: {}, sort: { default: [] } }),

              schemaName: "console",
              modelLabel: "example.Record",
              appLabel: "example",
              modelName: "Record",

              roots: {
                list: "records",
                detail: "record",
                aggregate: "recordAggregate",
              },
              typeNames: {
                node: "RecordType",
                filter: "RecordFilter",
                order: "RecordOrder",
                aggregate: "RecordAggregate",
              },
              capabilities: ["list", "aggregate"],

              aggregateFields: ["id"],

            },
          },
        },
      })}
    >
      {children}
    </ModelMetadataProvider>
  );
}

const RECORD_METADATA: SchemaFieldMetadata = withTestResourceInventory({
  types: {
    RecordType: {
      fields: {
        title: { name: "title", kind: "scalar", scalar: "String" },
      },
      resource: {
        query: testResourceQuery({ identity: { field: "id" }, fields: { "title": testQueryField("title", { scalar: "String", kind: "scalar", filter: null, sort: { field: "title" } }),
                "id": testQueryField("id", { scalar: "ID", filter: null }) }, axes: {}, sort: { default: [] } }),

        schemaName: "console",
        modelLabel: "example.Record",
        appLabel: "example",
        modelName: "Record",

        roots: {
          list: "records",
          detail: "record",
          aggregate: "recordAggregate",
          delete: "deleteRecord",
          deletePreview: "deleteRecordPreview",
        },
        typeNames: {
          node: "RecordType",
          filter: "RecordFilter",
          order: "RecordOrder",
          aggregate: "RecordAggregate",
          deletePayload: "RecordDeletePreview",
        },
        capabilities: ["list", "aggregate", "delete"],

        aggregateFields: ["id"],

      },
    },
  },
});

const RECORD_OPERATION_DOCUMENTS = {
  console: {
    deletePreviews: {
      "example.Record": { kind: "Document", definitions: [] },
    },
  },
};
