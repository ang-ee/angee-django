// @vitest-environment happy-dom

import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { type GetListParams } from "@refinedev/core";
import { testDataResource, testResourceQuery, testQueryField } from "@angee/metadata/testing";
import { ToastProvider, parseRecordNavigationScope, type ListViewNavigationScope } from "@angee/ui";
import { createRouteHref } from "@angee/ui/runtime";
import {
  ChatterTabsTestHost,
  PrimaryPaneTestHost,
  ShellPageTestProviders,
} from "@angee/app/testing";

// A reactive stand-in for the router's search store: `useSearch` reads it and a
// functional `navigate({ search })` writes it, so the URL-owned folder scope
// round-trips exactly as it does in the app.
const routerState = vi.hoisted(() => {
  let search: Record<string, unknown> = {};
  const listeners = new Set<() => void>();
  return {
    getSearch: (): Record<string, unknown> => search,
    setSearch: (next: Record<string, unknown>): void => {
      search = next;
      for (const listener of listeners) listener();
    },
    subscribe: (listener: () => void): (() => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    reset: (): void => {
      search = {};
    },
  };
});

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(
    (options?: {
      search?: (current: Record<string, unknown>) => Record<string, unknown>;
    }) => {
      if (options && typeof options.search === "function") {
        routerState.setSearch(options.search(routerState.getSearch()));
      }
    },
  ),
  params: {} as Record<string, string>,
  routeHref: vi.fn(),
}));

const sdkMocks = vi.hoisted(() => ({
  folderDrives: [] as string[],
  folderBatches: [] as Array<
    Array<{
      key: string;
      document: unknown;
      variables: { where: Record<string, unknown>; limit: number; offset: number };
    }>
  >,
  useAuthoredQuery: vi.fn(),
  useAuthoredQueryBatch: vi.fn(),
  invalidateAuthoredModels: vi.fn(),
  useBreadcrumbLeafLabel: vi.fn(), refetch: {
    backends: vi.fn(async () => undefined), drives: vi.fn(async () => undefined), file: vi.fn(async () => undefined), folders: vi.fn(async () => undefined), }, }));

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  const { useSyncExternalStore } = await import("react");
  const useSearchStore = (): Record<string, unknown> =>
    useSyncExternalStore(
      routerState.subscribe,
      routerState.getSearch,
      routerState.getSearch,
    );
  return {
    ...actual,
    useNavigate: () => routerMocks.navigate,
    useParams: () => routerMocks.params,
    useSearch: () => useSearchStore(),
    useRouterState: ({
      select,
    }: {
      select: (state: { location: { searchStr: string } }) => unknown;
    }) => {
      const current = useSearchStore();
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(current)) {
        if (value == null || value === "") continue;
        params.set(key, String(value));
      }
      const query = params.toString();
      return select({ location: { searchStr: query ? `?${query}` : "" } });
    },
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: sdkMocks.useAuthoredQuery,
  useAuthoredQueryBatch: sdkMocks.useAuthoredQueryBatch,
  useInvalidateAuthoredModels: () => sdkMocks.invalidateAuthoredModels,
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  const { useCallback } = await import("react");
  // A stable confirm, like the real (memoized) `useConfirm` — an unstable one
  // would churn the published navigator's identity and loop the publish effect.
  const confirmAlways = async () => true;
  return {
    ...actual,
    useRelationOptions: () => ({ options: storageData.drives.map((drive) => ({ value: drive.id, label: drive.name })),
      list: { fetching: false } }),
    useRouteHref: () => routerMocks.routeHref,
    useRouteRecordId: () => routerMocks.params.id,
    // Mirror the real (memoized) translator so a published node keeps a stable
    // identity across renders — an unstable `t` would republish every commit.
    useNamespaceT: (_namespace: string, messages: Record<string, string>) =>
      useCallback(
        (key: string, vars?: Record<string, string>) => {
          let message = messages[key] ?? key;
          for (const [name, value] of Object.entries(vars ?? {})) {
            message = message.replace(`{${name}}`, value);
          }
          return message;
        }, [messages], ), EmptyState: ({ title }: { title: string }) => (
      <section data-testid="empty-state">{title}</section>
    ), Glyph: () => <span />, LoadingPanel: ({ message }: { message: string }) => (
      <section data-testid="loading">{message}</section>
    ), PreviewPane: ({ file }: { file: { name: string } }) => (
      <section data-testid="preview-pane">{file.name}</section>
    ), SelectionBarAction: ({ children }: { children: React.ReactNode }) => (
      <button type="button">{children}</button>
    ), useBreadcrumbLeafLabel: sdkMocks.useBreadcrumbLeafLabel, useConfirm: () => confirmAlways, };
});

vi.mock("../data/use-drive-picker", async () => {
  const { useMemo } = await import("react");
  return { useDrivePicker: (value: string | null) => useMemo(() => {
    const selectedId = value || storageData.drives[0]?.id || "";
    const roots = value && !storageData.drives.some((drive) => drive.id === value)
      ? [...storageData.drives, { id: value, slug: value, name: value }] : storageData.drives;
    return { selectedId, roots, initial: { fetching: false },
      picker: { options: roots.map((drive) => ({ value: drive.id, label: drive.name })), list: { fetching: false }, searchState: {} } };
  }, [value, storageData.drives]) };
});

// The explorer pane composes RelationPicker through its own module import, so
// the picker double mocks the subpath module (same resolved id), not the barrel.
vi.mock("@angee/ui/views/RelationPicker", () => ({
  RelationPicker: ({
    value, options, onChange, onCreated, "aria-label": ariaLabel, }: {
    value?: string | null;
    options: readonly { value: string; label: string }[];
    onChange?: (value: string) => void;
    onCreated?: (value: string) => void;
    "aria-label"?: string;
  }) => (
    <div>
      <select
        aria-label={ariaLabel}
        data-testid="root-picker"
        value={value ?? ""}
        onChange={(event) => onChange?.(event.currentTarget.value)}
      >
        {!value ? <option value="">Choose a drive</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        data-testid="create-root"
        onClick={() => {
          onChange?.("drive-created");
          onCreated?.("drive-created");
        }}
      >
        Create root
      </button>
    </div>
  ),
}));

vi.mock("../data/use-file-actions", () => ({
  useFileActions: () => ({
    busy: false, move: vi.fn(), restore: vi.fn(async () => undefined), restoreMany: vi.fn(async () => undefined), trash: vi.fn(async () => undefined), trashMany: vi.fn(async () => undefined), }), }));

vi.mock("../data/use-folder-actions", () => ({
  useFolderActions: () => ({
    busy: false, create: vi.fn(), remove: vi.fn(async () => undefined), rename: vi.fn(), }), }));

vi.mock("../data/use-upload", () => ({
  useStorageUpload: () => ({
    clearFinished: vi.fn(), tasks: [], upload: vi.fn(), }), }));

vi.mock("./FileBrowserContent", async () => {
  const React = await import("react");
  const { useResourceView } = await import("@angee/ui");
  return {
  FileBrowserContent: ({
    baseFilter, onListStateChange, rowHref, uploadTarget, }: {
    baseFilter: Record<string, { exact: string | boolean }>;
    onListStateChange: (state: Record<string, unknown>) => void;
    rowHref?: (row: { id: string }, scope?: ListViewNavigationScope) => string;
    uploadTarget: { driveId: string; folderId: string | null } | null;
  }) => {
    const resourceView = useResourceView();
    const rows = storageData.files
      .filter((row) => row.drive === baseFilter.drive?.exact)
      .filter((row) => row.is_trashed === baseFilter.is_trashed?.exact)
      .filter((row) =>
        baseFilter.folder ? row.folder === baseFilter.folder.exact : true,
      )
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
    const rowsKey = rows.map((row) => row.id).join(",");
    const filterKey = JSON.stringify(baseFilter);
    React.useEffect(() => {
      onListStateChange({
        rows,
        total: rows.length,
        page: 1,
        pageSize: 50,
        pageCount: 1,
        hasNext: false,
        hasPrev: false,
        fetching: false,
        navigationScope: {
          filter: baseFilter,
          order: { updated_at: "DESC" },
          page: 1,
          pageSize: 50,
        },
      });
    }, [filterKey, onListStateChange, rowsKey]);
    return (
    <section
      data-testid="file-list"
      data-row-ids={rows.map((row) => row.id).join(", ")}
      data-row-hrefs={rows.map((row) => rowHref?.(row, { filter: baseFilter, order: { updated_at: "DESC" }, page: 1, pageSize: 50 }) ?? "").join(", ")}
      data-group={resourceView.state.groupStack[0]?.field ?? ""}
      data-filter={JSON.stringify(baseFilter)}
      data-upload-drive={uploadTarget?.driveId ?? ""}
      data-upload-folder={uploadTarget?.folderId ?? ""}
      data-can-upload={String(uploadTarget !== null)}
    />
    );
  },
  };
});

vi.mock("./FileDetail", () => ({
  // The detail is the file's metadata form, rendered under the preview; the
  // pager and lifecycle verbs ride the control band.
  FileDetail: ({ id }: { id: string }) => (
    <section data-testid="file-detail" data-file-id={id} />
  ), }));

import { StorageFileById, StorageFoldersPage, StorageFolderById } from "../data/documents";
import storage from "../index";
import { StoragePage } from "./StoragePage";

const fileResource = testDataResource("storage.File", {
  query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID" }),
    drive: testQueryField("drive", { scalar: "ID", filter: { field: "drive", scalar: "ID", values: [], operators: ["exact"] } }),
    folder: testQueryField("folder", { scalar: "ID", filter: { field: "folder", scalar: "ID", values: [], operators: ["exact", "isNull"] } }),
    is_trashed: testQueryField("is_trashed", { scalar: "Boolean", filter: { field: "is_trashed", scalar: "Boolean", values: [], operators: ["exact"] } }),
    updated_at: testQueryField("updated_at", { scalar: "DateTime", filter: null, sort: { field: "updated_at" } }),
  } }),
  typeNames: { filter: "files_bool_exp", order: "files_order_by" },
  roots: { aggregate: "files_aggregate" },
});
const driveResource = testDataResource("storage.Drive", { recordRepresentation: "name" });
const folderResource = testDataResource("storage.Folder", {
  roots: { deletePreview: "delete_folders_preview" },
  query: testResourceQuery({ fields: {
    name: testQueryField("name", { sort: { field: "name" }, filter: { field: "name", scalar: "String", values: [], operators: ["contains"] } }),
    drive: testQueryField("drive", { scalar: "ID", filter: { field: "drive", scalar: "ID", values: [], operators: ["exact"] } }),
    parent: testQueryField("parent", { scalar: "ID", filter: { field: "parent", scalar: "ID", values: [], operators: ["exact", "isNull"] } }),
    is_virtual: testQueryField("is_virtual", { scalar: "Boolean", filter: { field: "is_virtual", scalar: "Boolean", values: [], operators: ["exact"] } }),
  } }),
});
const provider = {
  getList: async ({ meta, pagination }: GetListParams) => {
    const where = meta?.gqlVariables?.where;
    const rows = storageData.files.filter((row) => matchesNativeWhere(row, where))
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
    const size = pagination?.pageSize ?? 50;
    const start = ((pagination?.currentPage ?? 1) - 1) * size;
    return { data: rows.slice(start, start + size), total: rows.length };
  },
} satisfies RefineTestDataProvider;

function matchesNativeWhere(row: Record<string, unknown>, where: unknown): boolean {
  if (!where || typeof where !== "object" || Array.isArray(where)) return true;
  return Object.entries(where as Record<string, unknown>).every(([field, lookup]) => {
    if (field === "_and") {
      return (Array.isArray(lookup) ? lookup : [lookup]).every((part) => matchesNativeWhere(row, part));
    }
    if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) return row[field] === lookup;
    const comparison = lookup as Record<string, unknown>;
    if ("_is_null" in comparison) return (row[field] == null) === comparison._is_null;
    return !("_eq" in comparison) || row[field] === comparison._eq;
  });
}
function pageTree() {
  return (
    <Provider resources={[fileResource, driveResource, folderResource]} dataProvider={provider} options={{ reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false, gcTime: 0 } } } } }}>
      <ToastProvider><ShellPageTestProviders>
        <StoragePage />
        <PrimaryPaneTestHost />
        <ChatterTabsTestHost />
      </ShellPageTestProviders></ToastProvider>
    </Provider>
  );
}

function openListFile(id: string): void {
  const href = fileListAttribute("data-row-hrefs")!.split(", ").find((href) => href.startsWith(`/storage/${id}?`))!;
  routerState.setSearch(Object.fromEntries(new URL(href, "https://test.local").searchParams));
  routerMocks.params = { id };
}

let storageData = makeStorageData();

beforeEach(() => {
  storageData = makeStorageData();
  routerMocks.params = {};
  routerState.reset();
  routerMocks.navigate.mockClear();
  routerMocks.routeHref.mockReset();
  const routeHref = createRouteHref(storage.routes ?? []);
  routerMocks.routeHref.mockImplementation(routeHref);
  Object.assign(routerMocks.routeHref, { maybe: routeHref.maybe });
  sdkMocks.folderDrives.length = 0;
  sdkMocks.folderBatches.length = 0;
  sdkMocks.invalidateAuthoredModels.mockClear();
  sdkMocks.useBreadcrumbLeafLabel.mockClear();
  for (const refetch of Object.values(sdkMocks.refetch)) {
    refetch.mockClear();
  }
  sdkMocks.useAuthoredQuery.mockImplementation((document, variables) => {
    if (document === StorageFolderById) {
      const id = String((variables as { id?: string })?.id ?? "");
      return queryResult("folders", { folders_by_pk: storageData.folders.find((row) => row.id === id) ?? null });
    }
    if (document === StorageFileById) {
      const id = String((variables as { id?: string })?.id ?? "");
      return queryResult("file", {
        files_by_pk: storageData.files.find((row) => row.id === id) ?? null,
      });
    }
    throw new Error("Unexpected storage query document");
  });
  sdkMocks.useAuthoredQueryBatch.mockImplementation(folderBatch);

});

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://files" });
afterEach(() => {
  cleanup();
  clearClients();
});

describe("StoragePage explorer wiring", () => {
  test("renders a directly shared file when no drive is visible", () => {
    routerMocks.params = { id: "file-a" };
    storageData = { ...storageData, drives: [] };
    storageData.files[0]!.mime_type = {
      mime_type: "text/xml", category: "text", label: "XML", icon_key: "file",
    };

    render(pageTree());

    expect(screen.queryByTestId("empty-state")).toBeNull();
    expect(screen.getByTestId("file-detail").getAttribute("data-file-id")).toBe(
      "file-a",
    );
    expect(screen.getByTestId("preview-pane").textContent).toBe("alpha.txt");
    expect(screen.getByText(/^XML · 128 B · uploaded /)).toBeTruthy();
  });

  test("uses the open file drive for a direct link", () => {
    routerMocks.params = { id: "file-b" };

    render(pageTree());

    expect(rootPickerValue()).toBe("drive-b");
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, file-b, __trash__, folder-b",
    );
    expect(treeAttribute("data-selected")).toBe("file-b");
    expect(screen.getByTestId("file-detail").getAttribute("data-file-id")).toBe(
      "file-b",
    );
    expect(screen.getByTestId("preview-pane").textContent).toBe("beta.txt");
    expect(sdkMocks.useBreadcrumbLeafLabel).toHaveBeenLastCalledWith("beta.txt");
    expect(sdkMocks.folderDrives.at(-1)).toBe("drive-b");
  });

  test("detail navigation follows the List scope without mounting a hidden list", async () => {
    const view = render(pageTree());
    act(() => openListFile("file-a"));
    view.rerender(pageTree());

    await waitFor(() => expect(pagerText()).toBe("1 / 2"));
    expect(screen.queryByTestId("file-list")).toBeNull();
    expect(pagerPrev().disabled).toBe(true);
    expect(pagerNext().disabled).toBe(false);

    fireEvent.click(pagerNext());

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage/file-a-folder",
      search: expect.any(Function),
    });
  });

  test("detail navigation steps back and stops at the snapshot edge", async () => {
    const view = render(pageTree());
    act(() => openListFile("file-a-folder"));
    view.rerender(pageTree());

    await waitFor(() => expect(pagerText()).toBe("2 / 2"));
    // The last file in the scope has no next step.
    expect(pagerNext().disabled).toBe(true);

    fireEvent.click(pagerPrev());

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage/file-a",
      search: expect.any(Function),
    });
  });

  test("switching drives resets the folder scope and closes the detail route", () => {
    render(pageTree());

    expect(screen.queryByTestId("preview-pane")).toBeNull();
    expect(fileListAttribute("data-group")).toBe("folder");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-a" },
      is_trashed: { exact: false },
    });

    fireEvent.click(treeRow("folder-a"));

    expect(treeAttribute("data-selected")).toBe("folder-a");
    expect(fileListAttribute("data-row-ids")).toBe("file-a-folder");
    expect(fileListAttribute("data-group")).toBe("");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-a" },
      is_trashed: { exact: false },
      folder: { exact: "folder-a" },
    });

    fireEvent.change(screen.getByLabelText("Drive"), {
      target: { value: "drive-b" },
    });

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage",
      search: expect.any(Function),
    });
    const driveNavigation = routerMocks.navigate.mock.calls.at(-1)?.[0] as {
      search: (current: Record<string, unknown>) => Record<string, unknown>;
    };
    expect(
      driveNavigation.search({ group: "folder~folder~folder_id" }),
    ).toEqual({ group: "folder~folder~folder_id" });
    expect(rootPickerValue()).toBe("drive-b");
    expect(treeAttribute("data-selected")).toBe("__all__");
    expect(fileListAttribute("data-row-ids")).toBe("file-b");
    expect(fileListAttribute("data-group")).toBe("folder");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-b" },
      is_trashed: { exact: false },
    });
    expect(sdkMocks.folderDrives.at(-1)).toBe("drive-b");

    fireEvent.click(treeRow("__trash__"));

    expect(fileListAttribute("data-group")).toBe("");
    expect(fileListAttribute("data-can-upload")).toBe("false");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-b" },
      is_trashed: { exact: true },
    });
  });

  test("the folder collection shares its native header and overflow with drive and folder verbs", async () => {
    routerState.setSearch({ folder: "folder-a" });
    render(pageTree());
    const navigator = screen.getByRole("navigation", { name: "Files" });
    const toolbar = within(navigator).getByRole("toolbar", { name: "" });
    expect(toolbar.contains(screen.getByTestId("root-picker"))).toBe(true);
    expect(within(toolbar).getByRole("button", { name: "New" })).toBeTruthy();
    fireEvent.click(within(toolbar).getByRole("button", { name: "More" }));
    expect(await screen.findByRole("menuitem", { name: "Rename folder" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Delete folder" })).toBeTruthy();
    expect(screen.getByRole("menuitemradio", { name: "Folder name, ascending" })).toBeTruthy();
  });

  test("selects the folder scope from the `?folder=` URL param on load", () => {
    routerState.setSearch({ folder: "folder-a" });

    render(pageTree());

    expect(treeAttribute("data-selected")).toBe("folder-a");
    expect(fileListAttribute("data-row-ids")).toBe("file-a-folder");
    expect(fileListAttribute("data-group")).toBe("");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-a" },
      is_trashed: { exact: false },
      folder: { exact: "folder-a" },
    });
    const link = new URL(fileListAttribute("data-row-hrefs")!, "https://test.local");
    expect(link.pathname).toBe("/storage/file-a-folder");
    expect(link.searchParams.get("folder")).toBe("folder-a");
    expect(parseRecordNavigationScope(Object.fromEntries(link.searchParams), fileResource)).toEqual({
      filter: { drive: { exact: "drive-a" }, is_trashed: { exact: false }, folder: { exact: "folder-a" } },
      order: { updated_at: "DESC" }, page: 1, pageSize: 50,
    });
    expect(routerMocks.routeHref).toHaveBeenCalledWith(
      "storage.file",
      { id: "file-a-folder" },
      "?folder=folder-a",
    );
  });

  test.each(["pending", "missing", "denied"])("refuses a provisional upload target for a %s deep-linked folder", (state) => {
    routerState.setSearch({ folder: "folder-b" });
    const query = sdkMocks.useAuthoredQuery.getMockImplementation()!;
    sdkMocks.useAuthoredQuery.mockImplementation((document, variables) => document === StorageFolderById
      ? { data: state === "missing" ? { folders_by_pk: null } : undefined, isFetching: state === "pending",
        error: state === "denied" ? new Error("Folder access denied") : null, refetch: sdkMocks.refetch.folders }
      : query(document, variables));
    const view = render(pageTree());
    expect(routerState.getSearch().folder).toBe("folder-b");
    expect(screen.queryByRole("treeitem", { selected: true })).toBeNull();
    expect(rootPickerValue()).toBe("");
    expect(fileListAttribute("data-can-upload")).toBe("false");
    expect(fileListAttribute("data-upload-drive")).toBe("");
    expect(fileListAttribute("data-upload-folder")).toBe("");
    expect((screen.getByRole("button", { name: "New" }) as HTMLButtonElement).disabled).toBe(true);
    if (state === "pending") {
      sdkMocks.useAuthoredQuery.mockImplementation(query);
      view.rerender(pageTree());
      expect(rootPickerValue()).toBe("drive-b");
      expect(fileListAttribute("data-can-upload")).toBe("true");
      expect(fileListAttribute("data-upload-drive")).toBe("drive-b");
      expect(fileListAttribute("data-upload-folder")).toBe("folder-b");
    }
  });

  test("selects the Trash scope from the `?folder=trash` sentinel, keeping group", () => {
    routerState.setSearch({ folder: "trash", group: "folder~folder~folder_id" });

    render(pageTree());

    expect(treeAttribute("data-selected")).toBe("__trash__");
    expect(fileListFilter()).toEqual({
      drive: { exact: "drive-a" },
      is_trashed: { exact: true },
    });

    // Selecting All files drops the `folder` param while leaving `group` intact.
    fireEvent.click(treeRow("__all__"));

    expect(routerMocks.navigate).toHaveBeenLastCalledWith({
      to: "/storage",
      search: expect.any(Function),
    });
    const allNavigation = routerMocks.navigate.mock.calls.at(-1)?.[0] as {
      search: (current: Record<string, unknown>) => Record<string, unknown>;
    };
    expect(
      allNavigation.search({ folder: "trash", group: "folder~folder~folder_id" }),
    ).toEqual({ group: "folder~folder~folder_id" });
    expect(treeAttribute("data-selected")).toBe("__all__");
  });

  test("pins an inline-created drive immediately while its options refresh", () => {
    const view = render(pageTree());

    fireEvent.click(screen.getByTestId("create-root"));

    expect(rootPickerValue()).toBe("drive-created");

    storageData = {
      ...storageData,
      drives: [
        ...storageData.drives,
        { id: "drive-created", slug: "created", name: "Created Drive" },
      ],
      folders: [
        ...storageData.folders,
        folder("folder-created", "Created Folder", "drive-created"),
      ],
    };
    view.rerender(pageTree());

    expect(rootPickerValue()).toBe("drive-created");
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-created",
    );
    expect(treeAttribute("data-selected")).toBe("__all__");
  });

  test("loads a folder's children only when it is expanded", () => {
    render(pageTree());

    // Only the drive's top-level folders load up front; the nested child is not
    // fetched with the roots.
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-a");

    // Expanding folder-a fires its per-parent children query and appends the row.
    fireEvent.click(treeRow("folder-a").querySelector("button")!);

    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-a, folder-a-child",
    );
  });

  test("keeps tree-row identity stable across an unrelated URL-state rerender", () => {
    const folderQuery = queryResult("folders", { folders_by_pk: null });
    const rootPage = folderBatch([{ key: "root", document: StorageFoldersPage,
      variables: { where: { drive: { _eq: "drive-a" }, parent: { _is_null: true } }, offset: 0, limit: 50 } }]);
    const noBranches = new Map();
    sdkMocks.useAuthoredQueryBatch.mockImplementation((scopes: FolderScope[]) => scopes.length ? rootPage : noBranches);
    sdkMocks.useAuthoredQuery.mockImplementation((document) => document === StorageFolderById
      ? folderQuery : queryResult("file", { files_by_pk: null }));

    render(pageTree());

    const rowsBefore = screen.getAllByRole("treeitem");

    act(() => {
      routerState.setSearch({ group: "folder~folder~folder_id" });
    });

    // File-list grouping leaves the separately owned folder collection unchanged.
    expect(screen.getAllByRole("treeitem")).toEqual(rowsBefore);
  });

  test("starts simultaneous per-folder reads when two roots expand", () => {
    storageData = {
      ...storageData,
      folders: [
        ...storageData.folders,
        folder("folder-c", "Folder C", "drive-a"),
        folder("folder-c-child", "Folder C Child", "drive-a", "folder-c"),
      ],
    };
    render(pageTree());

    fireEvent.click(treeRow("folder-a").querySelector("button")!);
    fireEvent.click(treeRow("folder-c").querySelector("button")!);

    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-a, folder-a-child, folder-c, folder-c-child",
    );
    const activeBatch = (sdkMocks.folderBatches.at(-1) ?? []).filter((scope) => scope.key !== "root");
    expect(activeBatch).toHaveLength(2);
    expect(activeBatch.map((scope) => scope.document)).toEqual([
      StorageFoldersPage,
      StorageFoldersPage,
    ]);
    expect(activeBatch.map((scope) => nativeLookup(scope.variables.where, "parent"))).toEqual([
      "folder-a",
      "folder-c",
    ]);
  });

  test("keys native folder pages by drive and resets the local branch frontier on switching", () => {
    render(pageTree());

    fireEvent.click(treeRow("folder-a").querySelector("button")!);
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-a, folder-a-child",
    );

    // Only current-drive query entries feed the render.
    fireEvent.change(screen.getByLabelText("Drive"), {
      target: { value: "drive-b" },
    });
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-b");

    // Returning starts a fresh local branch frontier; its transport cache remains native.
    fireEvent.change(screen.getByLabelText("Drive"), {
      target: { value: "drive-a" },
    });
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, folder-a",
    );
  });

  test("separates identical folder ids across drives", () => {
    storageData = {
      ...storageData,
      folders: [
        folder("shared", "Shared A", "drive-a"),
        folder("child-a", "Child A", "drive-a", "shared"),
        folder("shared", "Shared B", "drive-b"),
        folder("child-b", "Child B", "drive-b", "shared"),
      ],
    };
    render(pageTree());

    fireEvent.click(treeRow("shared").querySelector("button")!);
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, shared, child-a",
    );
    fireEvent.change(screen.getByLabelText("Drive"), {
      target: { value: "drive-b" },
    });
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, shared");
    fireEvent.click(treeRow("shared").querySelector("button")!);
    expect(treeAttribute("data-row-ids")).toBe(
      "__all__, __trash__, shared, child-b",
    );
    expect(
      (sdkMocks.folderBatches.at(-1) ?? []).map((scope) => scope.variables),
    ).toEqual([
      expect.objectContaining({ where: expect.any(Object), limit: 50, offset: 0 }),
    ]);
  });

  test("a failed children entry retries through its query owner", () => {
    const denied = new Error("children fetch denied");
    let childrenShouldError = true;
    const retry = vi.fn(() => { childrenShouldError = false; });
    sdkMocks.useAuthoredQueryBatch.mockImplementation((scopes: FolderScope[]) => new Map(scopes.map((scope) => [
      scope.key, scope.key !== "root" && childrenShouldError
        ? { data: undefined, isFetching: false, error: denied, refetch: retry }
        : folderBatch([scope]).get(scope.key)!,
    ])));
    const view = render(pageTree());
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-a");
    fireEvent.click(treeRow("folder-a").querySelector("button")!);
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-a");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
    view.rerender(pageTree());
    expect(treeAttribute("data-row-ids")).toBe("__all__, __trash__, folder-a, folder-a-child");
  });

});

function rootPickerValue(): string {
  return (screen.getByLabelText("Drive") as HTMLSelectElement).value;
}

function treeRow(id: string): HTMLElement {
  const label = id === "__all__" ? "All files" : id === "__trash__" ? "Trash"
    : storageData.folders.find((row) => row.id === id && row.drive === rootPickerValue())?.name
      ?? storageData.files.find((row) => row.id === id)?.filename;
  if (!label) throw new Error(`Unknown fixture row ${id}`);
  return screen.getByText(label).closest<HTMLElement>('[role="treeitem"]')!;
}

function treeAttribute(name: string): string | null {
  const candidates = [
    { id: "__all__", label: "All files" }, { id: "__trash__", label: "Trash" },
    ...storageData.folders.map((row) => ({ id: row.id, label: row.name })),
    ...storageData.files.map((row) => ({ id: row.id, label: row.filename })),
  ];
  const identity = (row: HTMLElement) => candidates.find((candidate) => within(row).queryByText(candidate.label, { exact: true }))?.id;
  if (name === "data-selected") {
    const selected = screen.queryByRole("treeitem", { selected: true });
    return selected ? identity(selected) ?? null : null;
  }
  return screen.getAllByRole("treeitem").map(identity).filter(Boolean).join(", ");
}

function fileListAttribute(name: string): string | null {
  return screen.getByTestId("file-list").getAttribute(name);
}

function fileListFilter(): Record<string, unknown> {
  return JSON.parse(fileListAttribute("data-filter") ?? "{}") as Record<
    string,
    unknown
  >;
}

// The record pager rides the shell control band beside the open file's preview.
function pagerText(): string {
  return (
    screen
      .getByRole("navigation", { name: "Record navigation" })
      .textContent?.replace(/\s+/g, " ")
      .trim() ?? ""
  );
}

function pagerPrev(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: "Previous record",
  }) as HTMLButtonElement;
}

function pagerNext(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: "Next record",
  }) as HTMLButtonElement;
}

type FolderScope = { key: string; document: unknown; variables: { where: Record<string, unknown>; limit: number; offset: number } };
function nativeLookup(where: Record<string, unknown>, field: string): unknown {
  if (field in where) return (where[field] as { _eq?: unknown })._eq;
  for (const part of (where._and ?? []) as Record<string, unknown>[]) {
    const value = nativeLookup(part, field);
    if (value !== undefined) return value;
  }
  return undefined;
}
function folderBatch(scopes: readonly FolderScope[]) {
  const batch = [...scopes];
  if (batch.length) sdkMocks.folderBatches.push(batch);
  return new Map(batch.map((scope) => {
    const drive = String(nativeLookup(scope.variables.where, "drive") ?? "");
    sdkMocks.folderDrives.push(drive);
    const rows = storageData.folders.filter((row) => matchesNativeWhere(row, scope.variables.where));
    return [scope.key, queryResult("folders", { folders: rows.slice(scope.variables.offset, scope.variables.offset + scope.variables.limit),
      folders_aggregate: { aggregate: { count: rows.length } } })];
  }));
}

function queryResult(
  name: keyof typeof sdkMocks.refetch,
  data: Record<string, unknown>,
) {
  return {
    data,
    isFetching: false,
    error: null,
    refetch: sdkMocks.refetch[name],
  };
}

function makeStorageData() {
  return {
    drives: [
      { id: "drive-a", slug: "alpha", name: "Drive A" },
      { id: "drive-b", slug: "beta", name: "Drive B" },
    ],
    folders: [
      folder("folder-a", "Folder A", "drive-a"),
      // A nested folder to exercise lazy expansion: it loads only when
      // `folder-a` is expanded, never with the drive's top-level roots.
      folder("folder-a-child", "Folder A Child", "drive-a", "folder-a"),
      folder("folder-b", "Folder B", "drive-b"),
    ],
    files: [
      file("file-a", "alpha.txt", "drive-a", null, "2025-01-03T00:00:00Z"),
      file(
        "file-a-folder",
        "folder-alpha.txt",
        "drive-a",
        "folder-a",
        "2025-01-02T00:00:00Z",
      ),
      file("file-b", "beta.txt", "drive-b", null, "2025-01-01T00:00:00Z"),
    ],
    backends: [{ id: "backend", slug: "local", label: "Local" }],
  };
}

function folder(
  id: string,
  name: string,
  drive: string,
  parent: string | null = null,
) {
  return {
    id,
    name,
    description: "",
    is_virtual: false,
    drive,
    parent,
  };
}

function file(
  id: string,
  filename: string,
  drive: string,
  folderId: string | null,
  updatedAt: string,
) {
  return {
    id,
    filename,
    title: "",
    size_bytes: 128,
    content_hash: "hash",
    upload_state: "ready",
    visibility: "inherited",
    is_trashed: false,
    updated_at: updatedAt,
    created_by_label: "Alex",
    url: `/files/${id}`,
    drive,
    folder: folderId,
    mime_type: {
      mime_type: "text/plain",
      category: "text",
      label: "Text",
      icon_key: "file",
    },
  };
}
