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
import { ResourceViewProvider } from "@angee/ui/views/resource-view-context";
import { createUiTestProviders } from "@angee/ui/testing";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    { id: "record-1", title: "First record" },
    { id: "record-2", title: "Second record" },
  ] as Row[],
  mutate: vi.fn(),
  getList: vi.fn(),
}));

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://delete-bulk",
  queryClientConfig: { defaultOptions: { queries: { retry: false }, mutations: { retry: false } } },
  dataProvider: {
    getList: async () => {
      sdkMocks.getList();
      return { data: sdkMocks.rows, total: sdkMocks.rows.length };
    },
    custom: async ({ payload }) => ({ data: { deleteRecordPreview: await sdkMocks.mutate(payload ?? {}) } }),
  },
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
    clearClients();
    sdkMocks.getList.mockClear();
    sdkMocks.rows = [
      { id: "record-1", title: "First record" },
      { id: "record-2", title: "Second record" },
    ];
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
    expect(deleteButton.classList.contains("hover:text-danger-text")).toBe(true);
  });

  test("page bulk actions precede generic Delete and Clear", async () => {
    render(<TestUrlState>
      <ListView resource="example.Record" columns={columns}
        bulkActions={() => <button>Export selected</button>} />
    </TestUrlState>);
    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    const actions = screen.getByRole("button", { name: "Export selected" }).parentElement!;
    expect(within(actions).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Export selected", "Delete", "Clear",
    ]);
    expect(screen.getByRole("button", { name: "Delete" }).classList.contains("hover:text-danger-text")).toBe(true);
  });

  test.each([false, true])("bulk Delete requires projected delete only when the model declares permissions (%s)", async (declaresPermissions) => {
    const model = RECORD_METADATA.types.RecordType!;
    const metadata = withTestResourceInventory({ types: { RecordType: { ...model,
      fields: { ...model.fields, ...(declaresPermissions
        ? { permissions: { name: "permissions", kind: "list" as const, scalar: "String" } } : {}) },
    } } });
    render(<TestUrlState><ModelMetadataProvider metadata={metadata}>
      <ListView resource="example.Record" columns={columns} />
    </ModelMetadataProvider></TestUrlState>);
    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(Boolean(screen.queryByRole("button", { name: "Delete" }))).toBe(!declaresPermissions);
    expect(sdkMocks.mutate).not.toHaveBeenCalled();
  });

  test("bulk Delete previews and deletes only permitted rows, keeping omitted permissions selected", async () => {
    sdkMocks.rows[0] = { ...sdkMocks.rows[0], permissions: ["delete"] };
    sdkMocks.mutate.mockImplementation(async ({ id }: { id: string }) => previewFor(id, id));
    const model = RECORD_METADATA.types.RecordType!;
    const metadata = withTestResourceInventory({ types: { RecordType: { ...model,
      fields: { ...model.fields, permissions: { name: "permissions", kind: "list", scalar: "String" } },
    } } });
    render(<TestUrlState><ModelMetadataProvider metadata={metadata}>
      <ListView resource="example.Record" columns={columns} />
    </ModelMetadataProvider></TestUrlState>);
    for (const checkbox of await screen.findAllByRole("checkbox", { name: "Select row" })) fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete 1 record?")).toBeTruthy();
    expect(sdkMocks.mutate).toHaveBeenCalledExactlyOnceWith({ id: "record-1", confirm: false });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(sdkMocks.mutate).toHaveBeenCalledWith({ id: "record-1", confirm: true }));
    await screen.findByText("1 selected");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sdkMocks.mutate).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
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

  test.each([false, true, null, undefined])("trashable bulk Delete admits only an explicitly trashed row (%s)", async (removed) => {
    sdkMocks.rows = [
      { id: "record-1", title: "First record", removed },
      { id: "record-2", title: "Second record", removed: false },
    ];
    sdkMocks.mutate.mockImplementation(async ({ id }: { id: string }) => previewFor(id, id));
    const model = RECORD_METADATA.types.RecordType!;
    const metadata = withTestResourceInventory({ types: { RecordType: { ...model,
      fields: { ...model.fields, removed: { name: "removed", kind: "scalar", scalar: "Boolean" } },
      resource: { ...model.resource, fields: [{ name: "removed", kind: "scalar", scalar: "Boolean",
        readable: true, trashable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }] },
    } } });
    render(<TestUrlState><ModelMetadataProvider metadata={metadata}>
      <ListView resource="example.Record" columns={columns} />
    </ModelMetadataProvider></TestUrlState>);
    for (const checkbox of await screen.findAllByRole("checkbox", { name: "Select row" })) fireEvent.click(checkbox);
    if (removed !== true) {
      expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
      expect(sdkMocks.mutate).not.toHaveBeenCalled();
      return;
    }
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete 1 record?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sdkMocks.mutate.mock.calls).toEqual([[{ id: "record-1", confirm: false }], [{ id: "record-1", confirm: true }]]);
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  test("Delete disables Clear throughout preview and confirmation", async () => {
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    sdkMocks.mutate.mockImplementation(async ({ id }: { id: string }) => { await pending; return previewFor(id, id); });
    render(<TestUrlState><ListView resource="example.Record" columns={columns} /></TestUrlState>);
    fireEvent.click((await screen.findAllByRole("checkbox", { name: "Select row" }))[0]!);
    const clear = screen.getByRole<HTMLButtonElement>("button", { name: "Clear" });
    const remove = screen.getByRole<HTMLButtonElement>("button", { name: "Delete" });
    fireEvent.click(remove);
    expect(clear.disabled).toBe(true);
    expect(remove.disabled).toBe(true);
    fireEvent.click(clear);
    fireEvent.click(remove);
    await act(async () => { finish(); });
    const dialog = await screen.findByRole("dialog");
    expect(sdkMocks.mutate).toHaveBeenCalledTimes(1);
    expect(clear.disabled).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(clear.disabled).toBe(false));
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  test("Delete reports refused and failed confirmations separately", async () => {
    sdkMocks.rows.push({ id: "record-3", title: "Third record" });
    sdkMocks.mutate.mockImplementation(async ({ id, confirm }: { id: string; confirm?: boolean }) => {
      if (confirm && id === "record-1") return { ...previewFor(id, id), has_blockers: true, refusals: ["Stop active work first."] };
      if (confirm && id === "record-2") throw new Error("Connection lost.");
      return previewFor(id, id);
    });
    render(<TestUrlState><ListView resource="example.Record" columns={columns} /></TestUrlState>);
    for (const checkbox of await screen.findAllByRole("checkbox", { name: "Select row" })) fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    await screen.findByText("1 refused: Stop active work first. 1 failed: Connection lost.");
    expect(screen.getByText("Deleted 1 record")).toBeTruthy();
    expect(screen.getByText("2 selected")).toBeTruthy();
    expect(sdkMocks.getList).toHaveBeenCalledTimes(2);
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
    expect(within(dialog).getByText("Delete 1 record?")).toBeTruthy();
    expect(
      within(dialog).getByRole("button", { name: "Delete" }).querySelector("svg"),
    ).toBeTruthy();
    expect(within(dialog).getByText("First record")).toBeTruthy();
    expect(within(dialog).getByRole("treeitem", { name: /2 line items/ })).toBeTruthy();
    expect(within(dialog).getByText("Line 1")).toBeTruthy();
  });

  test("bulk previews show a shared refusal once and prevent confirmation", async () => {
    sdkMocks.mutate.mockImplementation(async ({ id }: { id: string }) => ({
      ...previewFor(id, id),
      has_blockers: true,
      refusals: ["Stop active work first."],
    }));

    render(
      <TestUrlState>
        <ListView resource="example.Record" columns={columns} />
      </TestUrlState>,
    );

    for (const checkbox of await screen.findAllByRole("checkbox", { name: "Select row" })) {
      fireEvent.click(checkbox);
    }
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByText("Stop active work first.")).toHaveLength(1);
    expect(within(dialog).queryByText("2 Stop active work first.")).toBeNull();
    const button = within(dialog).getByRole<HTMLButtonElement>("button", { name: "Delete" });
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(sdkMocks.mutate).not.toHaveBeenCalledWith(expect.objectContaining({ confirm: true }));
  });

  test("partial refusals keep the blocked count and delete only allowed records", async () => {
    sdkMocks.mutate.mockImplementation(async ({ id }: { id: string }) => ({
      ...previewFor(id, id),
      has_blockers: id === "record-1",
      refusals: id === "record-1" ? ["Stop active work first."] : [],
    }));

    render(
      <TestUrlState>
        <ListView resource="example.Record" columns={columns} />
      </TestUrlState>,
    );

    for (const checkbox of await screen.findAllByRole("checkbox", { name: "Select row" })) {
      fireEvent.click(checkbox);
    }
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Stop active work first.")).toBeTruthy();
    expect(within(dialog).getByText("Delete 2 records?")).toBeTruthy();
    expect(within(dialog).getByText("1 selected record has deletion blockers.")).toBeTruthy();
    const button = within(dialog).getByRole<HTMLButtonElement>("button", { name: "Delete" });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);

    // A partial delete reports the refused record in a warning toast, so wait for
    // the confirmation dialog itself to close.
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /^Delete \d+ records\?$/ })).toBeNull());
    expect(sdkMocks.mutate).toHaveBeenCalledWith({ id: "record-2", confirm: true });
    expect(sdkMocks.mutate).not.toHaveBeenCalledWith({ id: "record-1", confirm: true });
    expect(screen.queryByText("2 selected")).toBeNull();
    expect(screen.getByText("1 selected")).toBeTruthy();
    const rows = screen.getAllByRole("checkbox", { name: "Select row" });
    expect(rows.map((row) => row.getAttribute("aria-checked"))).toEqual(["true", "false"]);
    expect(screen.getByText("1 refused: Stop active work first.")).toBeTruthy();
    expect(sdkMocks.getList).toHaveBeenCalledTimes(2);
  });

  test.each(["refused", "missing", "transport"])("a %s confirm outcome keeps that row selected", async (outcome) => {
    sdkMocks.mutate.mockImplementation(async ({ id, confirm }: { id: string; confirm?: boolean }) => {
      if (confirm && id === "record-1") {
        if (outcome === "transport") throw new Error("Delete refused.");
        if (outcome === "missing") return null;
        return { ...previewFor(id, id), has_blockers: true, refusals: ["Delete refused."] };
      }
      return previewFor(id, id);
    });
    render(<TestUrlState><ListView resource="example.Record" columns={columns} /></TestUrlState>);
    for (const checkbox of await screen.findAllByRole("checkbox", { name: "Select row" })) fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete 2 records?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete 2 records?" })).toBeNull());
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.getByText("Deleted 1 record")).toBeTruthy();
    expect(screen.getAllByRole("checkbox", { name: "Select row" }).map((row) => row.getAttribute("aria-checked")))
      .toEqual(["true", "false"]);
    expect(sdkMocks.getList).toHaveBeenCalledTimes(2);
    expect(screen.getAllByRole("button", { name: "Dismiss notification" })).toHaveLength(1);
  });

  test("a failed preview retains its row while the other row can still be deleted", async () => {
    sdkMocks.mutate.mockImplementation(async ({ id }: { id: string }) => {
      if (id === "record-1") throw new Error("Preview unavailable.");
      return previewFor(id, id);
    });
    render(<TestUrlState><ListView resource="example.Record" columns={columns} /></TestUrlState>);
    for (const checkbox of await screen.findAllByRole("checkbox", { name: "Select row" })) fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete 2 records?" });
    expect(within(dialog).getByText("Preview unavailable.")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete 2 records?" })).toBeNull());
    expect(sdkMocks.mutate).not.toHaveBeenCalledWith({ id: "record-1", confirm: true });
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  test("both preview and confirm stop at 25 selected rows and leave overflow selected", async () => {
    const ids = Array.from({ length: 27 }, (_, index) => `record-${index + 1}`);
    sdkMocks.mutate.mockImplementation(async ({ id }: { id: string }) => previewFor(id, id));
    render(<TestUrlState>
      <ResourceViewProvider resource="example.Record" scope="local" initialState={{ selectedIds: ids }}>
        <ListView resource="example.Record" scope="inherit" columns={columns} />
      </ResourceViewProvider>
    </TestUrlState>);
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete 25 records?" });
    expect(sdkMocks.mutate).toHaveBeenCalledTimes(25);
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete 25 records?" })).toBeNull());
    expect(sdkMocks.mutate).toHaveBeenCalledTimes(50);
    expect(sdkMocks.mutate).not.toHaveBeenCalledWith(expect.objectContaining({ id: "record-26" }));
    expect(sdkMocks.mutate).not.toHaveBeenCalledWith(expect.objectContaining({ id: "record-27" }));
    expect(screen.getByText("2 selected")).toBeTruthy();
    expect(screen.getByText("2 records were not processed and remain selected.")).toBeTruthy();
    expect(sdkMocks.getList).toHaveBeenCalledTimes(2);
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

    const group = screen.getByRole("treeitem", { name: /2 line items/ });
    fireEvent.click(within(group).getByRole("button", { name: "Collapse" }));

    expect(screen.queryByText("Line 1")).toBeNull();

    fireEvent.click(within(group).getByRole("button", { name: "Expand" }));

    expect(screen.getByText("Line 1")).toBeTruthy();
  });
});

// The wire payload `extractDeletePreview` parses is snake_case (the schema's
// Hasura naming); the views consume the camelCase shape it returns.
function previewFor(id: string, objectLabel: string) {
  return {
    total_deleted_count: 3,
    has_blockers: false,
    refusals: [],
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
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      <Provider metadata={RECORD_METADATA}>
        <OperationDocumentsProvider documents={RECORD_OPERATION_DOCUMENTS}>
          <ToastProvider>{children}</ToastProvider>
        </OperationDocumentsProvider>
      </Provider>
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
