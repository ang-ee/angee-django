// @vitest-environment happy-dom

import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { ResourceQuery, type Row } from "@angee/metadata";
import { OperationDocumentsProvider } from "@angee/refine";
import { testDataResource, testResourceQuery, testQueryField, testQueryAxis } from "@angee/metadata/testing";
import { composeAddons, parseFlatSearch, stringifyFlatSearch } from "@angee/app";
import { installTestLocalStorage } from "@angee/app/testing";
import {
  AppRuntimeProvider, ConsoleLayout, ModalsHost, ResourceViewProvider, ToastProvider, baseIcons, createRouteHref, defaultWidgets,
  recordNavigationSearch,
} from "@angee/ui";
import type { ReactElement } from "react";
import { afterEach, expect, test, vi } from "vitest";

import storage from "../index";
import { StorageFileById, StorageFoldersPage, StorageFolderById, StorageDriveById } from "../data/documents";
import { ALL_SCOPE, TRASH_SCOPE } from "../data/file-rows";
import { FilePreviewFrame } from "./FilePreviewFrame";
import type { StorageExplorerController } from "./StorageExplorer";
import { StorageListContent } from "./StorageListContent";
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
  recordRepresentation: "title", updateFields: ["title"], roots: { deletePreview: "delete_files_preview", aggregate: "files_aggregate" },
  fields: ["title", "filename", "created_by_label", "upload_state", "visibility", "created_at", "updated_at"].map((name) => ({
    name, kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false,
    updatable: name === "title", requiredOnCreate: false,
  })),
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
const resources = [fileResource, driveResource, folderResource];
const scope = { filter: { drive: { exact: "drive-a" }, is_trashed: { exact: false } }, order: { updated_at: "DESC" as const }, page: 1, pageSize: 50 };
const drive = { id: "drive-a", slug: "assets", name: "Assets" };
function file(id: string, title: string) {
  return { id, title, filename: `${title}.bin`, url: "", drive: drive.id, folder: null,
    is_trashed: false, upload_state: "ready", visibility: "inherited", size_bytes: 1, mime_type: null,
    created_by_label: "", created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-05T00:00:00Z" };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://files" });
afterEach(() => { cleanup(); clearClients(); vi.restoreAllMocks(); });

test("cold Files navigation preserves the real shell, tree, pager and the details form while native reads run in parallel", async () => {
  installTestLocalStorage();
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    matches: query === "(min-width: 64rem)", media: query, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true,
  }));
  const previews = new Map<string, ReturnType<typeof deferred<{ data: Row }>>>();
  const forms = new Map<string, ReturnType<typeof deferred<{ data: Row | null }>>>();
  const otherDrive = { id: "drive-b", slug: "archive", name: "Archive" };
  const records = [file("file-a", "Alpha"), file("file-b", "Beta"), { ...file("file-c", "Gamma"), drive: otherDrive.id }];
  const getOne = vi.fn(({ id, resource }: { id?: string | number; resource: string }) => {
    if (resource === "drives") return Promise.resolve({ data: id === otherDrive.id ? otherDrive : drive });
    const request = deferred<{ data: Row | null }>();
    forms.set(String(id), request);
    return request.promise;
  });
  const update = vi.fn();
  const custom = vi.fn(({ meta }: { meta?: Record<string, unknown> }) => {
    if (meta?.gqlQuery === StorageDriveById) return Promise.resolve({ data: { drives_by_pk: drive } });
    if (meta?.gqlQuery === StorageFoldersPage) return Promise.resolve({ data: { folders: [], folders_aggregate: { aggregate: { count: 0 } } } });
    if (meta?.gqlQuery === StorageFolderById) return Promise.resolve({ data: { folders_by_pk: null } });
    if (meta?.gqlQuery === StorageFileById) {
      const request = deferred<{ data: Row }>();
      previews.set(String((meta.gqlVariables as { id: string }).id), request);
      return request.promise;
    }
    throw new Error("Unexpected authored request");
  });
  const provider = { getOne, custom, update,
    deleteOne: vi.fn(), getList: vi.fn(async ({ resource }) => resource === "drives" ? ({ data: [drive], total: 1 }) : ({ data: records, total: records.length })),
  } satisfies RefineTestDataProvider;
  const root = createRootRoute({ component: () => <ConsoleLayout><Outlet /></ConsoleLayout> });
  const files = createRoute({ getParentRoute: () => root, path: "/storage", component: StoragePage });
  const detail = createRoute({ getParentRoute: () => files, path: "$id" });
  const router = createRouter({ routeTree: root.addChildren([files.addChildren([detail])]),
    history: createMemoryHistory({ initialEntries: [`/storage/file-a${stringifyFlatSearch(recordNavigationSearch({ folder: "" }, fileResource, scope))}`] }),
    parseSearch: parseFlatSearch, stringifySearch: stringifyFlatSearch,
  });
  const view = render(
    <Provider resources={resources} dataProvider={provider} options={{ reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } } } }}>
      <OperationDocumentsProvider documents={{ console: { deletePreviews: { "storage.File": "mutation DeleteFiles($id: ID!) { delete_files_preview(id: $id) { deleted } }", "storage.Folder": "mutation DeleteFolders($id: ID!) { delete_folders_preview(id: $id) { deleted } }" } } }}>
      <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets, routeHref: createRouteHref(storage.routes ?? []),
        // The composed containers: the record aside and its Related groups.
        containers: composeAddons([storage], { canonicalModelLabel: (model) => model }).containers }}>
        <ModalsHost><ToastProvider><RouterProvider router={router} /></ToastProvider></ModalsHost>
      </AppRuntimeProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  await waitFor(() => expect(forms.has("file-a") && previews.has("file-a")).toBe(true));
  await act(async () => {
    previews.get("file-a")!.resolve({ data: { files_by_pk: records[0] } });
    forms.get("file-a")!.resolve({ data: records[0]! });
  });
  await screen.findByDisplayValue("Alpha");
  const navigator = screen.getByRole("navigation", { name: "Files" });
  const navigatorToolbar = within(navigator).getByRole("toolbar", { name: "" });
  expect(navigator.firstElementChild?.contains(navigatorToolbar)).toBe(true);
  expect(navigatorToolbar.contains(await screen.findByRole("combobox", { name: "Drive: Assets" }))).toBe(true);
  expect(within(navigatorToolbar).getByRole("button", { name: "New" })).toBeTruthy();
  expect(within(navigator).getByText("All files")).toBeTruthy();
  expect(within(navigator).getByText("Trash")).toBeTruthy();
  expect(navigatorToolbar.contains(within(navigator).getByRole("combobox", { name: "Filter records" }))).toBe(false);
  const header = screen.getAllByRole("heading", { level: 1, name: "Alpha" })[0]!;
  const frame = header.closest("header")?.parentElement ?? header.parentElement!.parentElement!;
  const tree = screen.getAllByRole("tree")[0];
  const pager = screen.getByRole("navigation", { name: "Record navigation" });
  // The lifecycle verbs reach the control band once the desktop shell has mounted its host.
  await waitFor(() => expect(view.container.querySelector(".area-control")?.contains(screen.getByRole("button", { name: "Trash" }))).toBe(true));
  const control = view.container.querySelector(".area-control");
  const trash = screen.getByRole("button", { name: "Trash" }) as HTMLButtonElement;
  await waitFor(() => expect((screen.getByRole("button", { name: "Next record" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Next record" }));
  await waitFor(() => expect(forms.has("file-b") && previews.has("file-b")).toBe(true));
  expect(router.state.location.pathname).toBe("/storage/file-b");
  expect(screen.queryByDisplayValue("Alpha")).toBeNull();
  expect(screen.getAllByRole("tree")[0]).toBe(tree);
  expect(screen.getByRole("navigation", { name: "Record navigation" })).toBe(pager);
  expect(screen.getByRole("heading", { level: 1, name: "Loading file" })).toBe(header);
  expect(frame.isConnected).toBe(true);
  expect(screen.getByRole("button", { name: "Trash" })).toBe(trash);
  expect(trash.disabled).toBe(true);
  expect(control?.contains(trash)).toBe(true);
  expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();

  // A form response can arrive before its independent preview response.
  await act(async () => forms.get("file-b")!.resolve({ data: records[1]! }));
  await screen.findByDisplayValue("Beta");
  expect(trash.disabled).toBe(true);
  await act(async () => previews.get("file-b")!.resolve({ data: { files_by_pk: records[1] } }));
  await waitFor(() => expect(header.textContent).toBe("Beta"));
  expect(trash.disabled).toBe(false);
  expect(control?.contains(trash)).toBe(true);
  expect(update).not.toHaveBeenCalled();
  // The metadata form lives in the page body, so each record owns one form read.
  expect(getOne.mock.calls.filter(([request]) => request.resource !== "drives").map(([request]) => String(request.id))).toEqual(["file-a", "file-b"]);

  const folderSearch = screen.getByRole("combobox", { name: "Filter records" });
  const selectedDrive = screen.getByRole("combobox", { name: "Drive: Assets" });
  await act(async () => router.navigate({ to: "/storage/file-c", search: (current: Record<string, unknown>) => current }));
  await waitFor(() => expect(forms.has("file-c") && previews.has("file-c")).toBe(true));
  expect(screen.getByRole("combobox", { name: "Drive: Assets" })).toBe(selectedDrive);
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBe(folderSearch);
  expect(screen.queryByDisplayValue("Beta")).toBeNull();
  await act(async () => {
    forms.get("file-c")!.resolve({ data: records[2]! });
    previews.get("file-c")!.resolve({ data: { files_by_pk: records[2] } });
  });
  await screen.findByDisplayValue("Gamma");
  await screen.findByRole("combobox", { name: "Drive: Archive" });
  expect(screen.getByRole("combobox", { name: "Filter records" })).not.toBe(folderSearch);
  expect(screen.getAllByRole("tree")[0]).toBe(tree);
  await act(async () => router.navigate({ to: "/storage/file-b", search: (current: Record<string, unknown>) => current }));
  await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "Loading file" })).toBe(header));
  await act(async () => {
    forms.get("file-b")!.resolve({ data: records[1]! });
    previews.get("file-b")!.resolve({ data: { files_by_pk: records[1] } });
  });
  await screen.findByDisplayValue("Beta");
  await screen.findByRole("combobox", { name: "Drive: Assets" });

  for (const missing of [false, true]) {
    const id = missing ? "file-missing" : "file-denied";
    await act(async () => router.navigate({ to: `/storage/${id}`, search: (current: Record<string, unknown>) => current }));
    await waitFor(() => expect(forms.has(id) && previews.has(id)).toBe(true));
    expect(screen.queryByDisplayValue("Beta")).toBeNull();
    expect(trash.disabled).toBe(true);
    await act(async () => {
      if (missing) {
        previews.get(id)!.resolve({ data: { files_by_pk: null } });
        forms.get(id)!.resolve({ data: null });
      } else {
        previews.get(id)!.reject(new Error("Preview access denied"));
        forms.get(id)!.reject(new Error("Metadata access denied"));
      }
    });
    // The header names an unavailable record; the body's own surface says why.
    await waitFor(() => expect(header.textContent).toBe("Record unavailable"));
    expect(await screen.findByText(missing ? "File not found" : "Could not load this preview.")).toBeTruthy();
    if (!missing) expect(await screen.findByText("Preview access denied")).toBeTruthy();
    expect(screen.getAllByRole("tree")[0]).toBe(tree);
    expect(screen.getByRole("navigation", { name: "Record navigation" })).toBe(pager);
    expect(trash.disabled).toBe(true);
    expect(control?.contains(trash)).toBe(true);
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
    fireEvent.click(trash);
    expect(update).not.toHaveBeenCalled();
    expect(provider.deleteOne).not.toHaveBeenCalled();
  }
});

test("All files starts with server folder grouping and a clean URL; changing the folder scope changes its default", async () => {
  installTestLocalStorage();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1024, 768));
  const fields = { id: { scalar: "ID" }, title: { scalar: "String" }, filename: { scalar: "String" },
    created_by_label: { scalar: "String" }, upload_state: { scalar: "String" }, visibility: { scalar: "String" },
    updated_at: { scalar: "DateTime" }, size_bytes: { scalar: "Int" }, is_trashed: { scalar: "Boolean" },
    drive: { scalar: "ID" }, folder: { scalar: "ID" }, "mime_type.label": { scalar: "String" },
  };
  const query = ResourceQuery.forRows({ fields }).contract;
  query.axes.folder = testQueryAxis("folder", { identityPath: "folder", paths: ["folder"],
    server: { input: "FOLDER", key: "folder" },
    drill: { kind: "value", field: "folder", valueKey: "folder", nullMode: "isNull", valueMap: [] },
  });
  const groupedResource = testDataResource("storage.File", { query,
    roots: { groups: "files_groups", aggregate: "files_aggregate", delete: undefined },
    typeNames: { filter: "files_bool_exp", order: "files_order_by" },
    fields: Object.entries(fields).filter(([name]) => !name.includes(".")).map(([name, value]) => ({
      name, kind: "scalar", scalar: value.scalar, readable: true, nullable: name === "folder",
      aggregatable: name === "id", creatable: false, updatable: false, requiredOnCreate: false,
    })),
  });
  const record = { ...file("file-a", "Alpha"), folder: "folder-a" };
  const groupRequests = vi.fn();
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    if (meta?.gqlQuery === StorageDriveById) return Promise.resolve({ data: { drives_by_pk: drive } });
    if (meta?.gqlQuery === StorageFoldersPage) return { data: { folders: [], folders_aggregate: { aggregate: { count: 0 } } } };
    if (meta?.gqlQuery === StorageFolderById) return { data: { folders_by_pk: null } };
    groupRequests(meta?.gqlVariables);
    return { data: { files_groups: [{ key: { folder: "folder-a" }, aggregate: { count: 1 } }], totalCount: 1 } };
  });
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/storage", component: StoragePage });
  const router = createRouter({ routeTree: root.addChildren([route]),
    history: createMemoryHistory({ initialEntries: ["/storage"] }),
    parseSearch: parseFlatSearch, stringifySearch: stringifyFlatSearch,
  });
  render(<Provider resources={[groupedResource, ...resources.slice(1)]} dataProvider={{ custom,
    getOne: vi.fn(async () => ({ data: drive })),
    getList: vi.fn(async ({ resource }) => resource === "drives" ? ({ data: [drive], total: 1 }) : ({ data: [record], total: 1 })),
  }}><OperationDocumentsProvider documents={{ console: { groups: {
    "storage.File": "query Groups { files_groups { key } totalCount }",
  } } }}><AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets,
    routeHref: createRouteHref(storage.routes ?? []), containers: composeAddons([storage], { canonicalModelLabel: (model) => model }).containers,
  }}><ModalsHost><ToastProvider><RouterProvider router={router} /></ToastProvider></ModalsHost></AppRuntimeProvider></OperationDocumentsProvider></Provider>);
  expect(await screen.findByRole("button", { name: "folder-a" })).toBeTruthy();
  expect(groupRequests.mock.calls[0]?.[0]).toMatchObject({ group_by: [{ field: "FOLDER" }],
    where: ResourceQuery.from(groupedResource).toWhere({ drive: { exact: drive.id }, is_trashed: { exact: false } }),
  });
  expect(router.state.location.search).toEqual({});
  const reads = groupRequests.mock.calls.length;
  await act(async () => router.navigate({ to: "/storage", search: { folder: "trash" } }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "folder-a" })).toBeNull());
  expect(groupRequests).toHaveBeenCalledTimes(reads);
  expect(router.state.location.search).toEqual({ folder: "trash" });
});

// One storage world for the URL-owned navigator scope: two drives, a nested folder,
// a root file, a filed file and a trashed one, all served by the native provider.
const drives = [{ id: "drive-a", slug: "alpha", name: "Drive A" }, { id: "drive-b", slug: "beta", name: "Drive B" }];
function folder(id: string, name: string, driveId: string, parent: string | null = null) {
  return { id, name, description: "", is_virtual: false, drive: driveId, parent };
}
const folders = [folder("folder-a", "Folder A", "drive-a"), folder("folder-a-child", "Folder A Child", "drive-a", "folder-a"),
  folder("folder-b", "Folder B", "drive-b")];
const worldFiles = [{ ...file("file-a", "Alpha"), drive: "drive-a" }, { ...file("file-child", "Child"), drive: "drive-a", folder: "folder-a-child" },
  { ...file("file-old", "Old"), drive: "drive-a", is_trashed: true }];
type FolderRead = { data: { folders_by_pk: unknown } };

/** Hasura `where` over in-memory rows: the `_eq`, `_is_null` and `_and` terms storage reads send. */
function matches(row: Record<string, unknown>, where: unknown): boolean {
  if (!where || typeof where !== "object" || Array.isArray(where)) return true;
  return Object.entries(where as Record<string, unknown>).every(([field, lookup]) => {
    if (field === "_and") return (Array.isArray(lookup) ? lookup : [lookup]).every((part) => matches(row, part));
    if (!lookup || typeof lookup !== "object") return row[field] === lookup;
    const comparison = lookup as Record<string, unknown>;
    if ("_is_null" in comparison) return (row[field] == null) === comparison._is_null;
    return !("_eq" in comparison) || row[field] === comparison._eq;
  });
}

function folderWithAncestors(id: string): FolderRead {
  const row = folders.find((candidate) => candidate.id === id);
  const ancestors: typeof folders = [];
  for (let parent = row?.parent; parent;) {
    const ancestor = folders.find((candidate) => candidate.id === parent)!;
    ancestors.unshift(ancestor);
    parent = ancestor.parent;
  }
  return { data: { folders_by_pk: row ? { ...row, ancestors } : null } };
}

/** The Files page in the desktop shell at `path`, over the storage world. */
function mountStorage(path: string, { visibleDrives = drives, folderRead }: {
  visibleDrives?: readonly (typeof drives)[number][];
  folderRead?: (id: string) => Promise<FolderRead>;
} = {}) {
  installTestLocalStorage();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1024, 768));
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    matches: query === "(min-width: 64rem)", media: query, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true,
  }));
  const provider = {
    custom: vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
      const variables = (meta?.gqlVariables ?? {}) as { id: string; where?: unknown; limit: number; offset: number };
      if (meta?.gqlQuery === StorageDriveById) return { data: { drives_by_pk: visibleDrives.find((row) => row.id === variables.id) ?? null } };
      if (meta?.gqlQuery === StorageFolderById) return folderRead ? folderRead(variables.id) : folderWithAncestors(variables.id);
      if (meta?.gqlQuery === StorageFoldersPage) {
        const rows = folders.filter((row) => matches(row, variables.where));
        return { data: { folders: rows.slice(variables.offset, variables.offset + variables.limit),
          folders_aggregate: { aggregate: { count: rows.length } } } };
      }
      if (meta?.gqlQuery === StorageFileById) return { data: { files_by_pk: worldFiles.find((row) => row.id === variables.id) ?? null } };
      return { data: { files_groups: [], totalCount: 0 } };
    }),
    getOne: vi.fn(async ({ id }: { id?: string | number }) => ({ data: worldFiles.find((row) => row.id === id) ?? null })),
    getList: vi.fn(async ({ resource, meta }) => {
      if (resource === "drives") return { data: [...visibleDrives], total: visibleDrives.length };
      const rows = worldFiles.filter((row) => matches(row, meta?.gqlVariables?.where));
      return { data: rows, total: rows.length };
    }),
  } satisfies RefineTestDataProvider;
  const root = createRootRoute({ component: () => <ConsoleLayout><Outlet /></ConsoleLayout> });
  const page = createRoute({ getParentRoute: () => root, path: "/storage", component: StoragePage });
  const detail = createRoute({ getParentRoute: () => page, path: "$id" });
  const router = createRouter({ routeTree: root.addChildren([page.addChildren([detail])]),
    history: createMemoryHistory({ initialEntries: [path] }), parseSearch: parseFlatSearch, stringifySearch: stringifyFlatSearch });
  render(<Provider resources={resources} dataProvider={provider} options={{ reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } } } }}>
    <OperationDocumentsProvider documents={{ console: { groups: { "storage.File": "query Groups { files_groups { key } totalCount }" } } }}>
      <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets, routeHref: createRouteHref(storage.routes ?? []),
        containers: composeAddons([storage], { canonicalModelLabel: (model) => model }).containers }}>
        <ModalsHost><ToastProvider><RouterProvider router={router} /></ToastProvider></ModalsHost>
      </AppRuntimeProvider>
    </OperationDocumentsProvider>
  </Provider>);
  return router;
}

const TREE_LABELS = ["All files", "Trash", ...folders.map((row) => row.name)];
/** A navigator row's own label; a parent row also contains its children's labels. */
function treeLabel(row: HTMLElement): string | undefined {
  return TREE_LABELS.find((label) => within(row).queryByText(label, { exact: true }));
}
const treeLabels = () => screen.getAllByRole("treeitem").map(treeLabel);
const selectedTreeLabel = () => {
  const row = screen.queryByRole("treeitem", { selected: true });
  return row ? treeLabel(row) : undefined;
};
const treeRow = (label: string) => screen.getAllByRole("treeitem").find((row) => treeLabel(row) === label)!;

test("a `?folder=` deep link selects its folder, reveals its readable ancestors and scopes the list", async () => {
  mountStorage("/storage?folder=folder-a-child");
  await waitFor(() => expect(selectedTreeLabel()).toBe("Folder A Child"));
  expect(treeLabels()).toEqual(["All files", "Trash", "Folder A", "Folder A Child"]);
  expect(await screen.findByText("Child")).toBeTruthy();
  expect(screen.queryByText("Alpha")).toBeNull();
});

test("leaving Trash for All files drops the folder scope, and switching drives resets a folder scope", async () => {
  const router = mountStorage("/storage?folder=trash");
  await waitFor(() => expect(selectedTreeLabel()).toBe("Trash"));
  expect(await screen.findByText("Old")).toBeTruthy();
  fireEvent.click(treeRow("All files"));
  await waitFor(() => expect(router.state.location.search).toEqual({}));
  await waitFor(() => expect(treeLabels()).toContain("Folder A"));
  fireEvent.click(treeRow("Folder A"));
  await waitFor(() => expect(router.state.location.search).toEqual({ folder: "folder-a" }));
  fireEvent.click(await screen.findByRole("combobox", { name: "Drive: Drive A" }));
  fireEvent.click(await screen.findByRole("option", { name: "Drive B" }));
  await waitFor(() => expect(router.state.location.search).toEqual({}));
  await waitFor(() => expect(treeLabels()).toEqual(["All files", "Trash", "Folder B"]));
});

test.each(["pending", "missing", "denied"] as const)("a %s deep-linked folder selects nothing and offers no upload target", async (state) => {
  const pending = deferred<FolderRead>();
  const router = mountStorage("/storage?folder=folder-b", { folderRead: (id) => state === "pending" ? pending.promise
    : state === "missing" ? Promise.resolve({ data: { folders_by_pk: null } }) : Promise.reject(new Error(`Folder ${id} access denied`)) });
  const navigator = await screen.findByRole("navigation", { name: "Files" });
  const create = () => within(navigator).getByRole("button", { name: "New" }) as HTMLButtonElement;
  await waitFor(() => expect(create().disabled).toBe(true));
  expect(selectedTreeLabel()).toBeUndefined();
  expect(router.state.location.search).toEqual({ folder: "folder-b" });
  if (state !== "pending") return;
  await act(async () => pending.resolve(folderWithAncestors("folder-b")));
  expect(await within(navigator).findByRole("combobox", { name: "Drive: Drive B" })).toBeTruthy();
  await waitFor(() => expect(create().disabled).toBe(false));
  await waitFor(() => expect(selectedTreeLabel()).toBe("Folder B"));
});

test("a folder scope's collection header carries the drive picker, New and the folder verbs", async () => {
  mountStorage("/storage?folder=folder-a");
  const toolbar = within(await screen.findByRole("navigation", { name: "Files" })).getByRole("toolbar", { name: "" });
  expect(await within(toolbar).findByRole("combobox", { name: "Drive: Drive A" })).toBeTruthy();
  expect(within(toolbar).getByRole("button", { name: "New" })).toBeTruthy();
  await waitFor(() => expect(selectedTreeLabel()).toBe("Folder A"));
  fireEvent.click(within(toolbar).getByRole("button", { name: "More" }));
  expect(await screen.findByRole("menuitem", { name: "Rename folder" })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Delete folder" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "Folder name, ascending" })).toBeTruthy();
});

test("a directly shared file opens when no drive is visible", async () => {
  mountStorage("/storage/file-a", { visibleDrives: [] });
  expect((await screen.findAllByRole("heading", { level: 1, name: "Alpha" }))[0]).toBeTruthy();
  expect(screen.queryByText("No drives are available to you.")).toBeNull();
});

/** Mount a storage view outside the shell, acting through the given file verbs. */
function renderView(view: ReactElement, rows: readonly Row[]) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "/storage", component: () => view });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/storage"] }),
    parseSearch: parseFlatSearch, stringifySearch: stringifyFlatSearch });
  render(<Provider resources={resources} dataProvider={{
    custom: vi.fn(async () => ({ data: { files_by_pk: rows[0] ?? null } })),
    getOne: vi.fn(async () => ({ data: rows[0] ?? null })),
    getList: vi.fn(async () => ({ data: [...rows], total: rows.length })),
  }} options={{ reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false, gcTime: 0 } } } } }}>
    <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets, routeHref: createRouteHref(storage.routes ?? []) }}>
      <ModalsHost><ToastProvider><RouterProvider router={router} /></ToastProvider></ModalsHost>
    </AppRuntimeProvider>
  </Provider>);
}

function fileActions(completed: boolean) {
  const settle = vi.fn(async () => completed);
  return { busy: false, trash: settle, restore: settle, move: settle, trashMany: settle, restoreMany: settle };
}

// The file verbs settle their own refusals (data/actions.test.tsx); the views act on the resolved outcome.
test.each(["blocked", "cancel", "success"] as const)("the open file closes only after its confirmed trash completes (%s)", async (outcome) => {
  const actions = fileActions(outcome === "success");
  const close = vi.fn();
  renderView(<FilePreviewFrame id="file-a" fileNavigation={null} closeDetail={close} fileActions={actions} />, [file("file-a", "Alpha")]);
  const trash = await screen.findByRole("button", { name: "Trash" }) as HTMLButtonElement;
  await waitFor(() => expect(trash.disabled).toBe(false));
  fireEvent.click(trash);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: outcome === "cancel" ? "Cancel" : "Trash" }));
  if (outcome === "cancel") await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  else await waitFor(() => expect(actions.trash).toHaveBeenCalledWith("file-a"));
  if (outcome === "success") await waitFor(() => expect(close).toHaveBeenCalledOnce());
  else expect(close).not.toHaveBeenCalled();
  if (outcome === "cancel") expect(actions.trash).not.toHaveBeenCalled();
});

test.each([
  { scope: ALL_SCOPE, verb: "Trash", completed: true },
  { scope: ALL_SCOPE, verb: "Trash", completed: false },
  { scope: TRASH_SCOPE, verb: "Restore", completed: false },
])("a bulk $verb keeps the selection unless it completed (completed=$completed)", async ({ scope, verb, completed }) => {
  const actions = fileActions(completed);
  const controller = { rootId: "drive-a", selectedId: scope, uploadTarget: null } as StorageExplorerController;
  renderView(<ResourceViewProvider resource="storage.File">
    <StorageListContent controller={controller} fileResource={undefined} navigationScope={null} fileActions={actions}
      uploads={{ tasks: [], upload: vi.fn(), retry: vi.fn(), clearFinished: vi.fn() }} onListStateChange={() => undefined} />
  </ResourceViewProvider>, [{ ...file("file-a", "Alpha"), is_trashed: scope === TRASH_SCOPE }]);
  const select = () => within(screen.getByText("Alpha").closest("tr")!).getByRole("checkbox", { name: "Select row" });
  await screen.findByText("Alpha");
  fireEvent.click(select());
  fireEvent.click(await screen.findByRole("button", { name: verb }));
  await waitFor(() => expect(scope === TRASH_SCOPE ? actions.restoreMany : actions.trashMany).toHaveBeenCalledWith(new Set(["file-a"])));
  await waitFor(() => expect(select().getAttribute("aria-checked")).toBe(String(!completed)));
});
