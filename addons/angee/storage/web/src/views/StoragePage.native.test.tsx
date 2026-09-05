// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources, type Row } from "@angee/metadata";
import { OperationDocumentsProvider } from "@angee/refine";
import { testDataResource } from "@angee/metadata/testing";
import { parseFlatSearch, stringifyFlatSearch } from "@angee/app";
import { installTestLocalStorage } from "@angee/app/testing";
import { AppRuntimeProvider, ConsoleLayout, ModalsHost, ToastProvider, baseIcons, createRouteHref, defaultWidgets, recordNavigationSearch } from "@angee/ui";
import { afterEach, expect, test, vi } from "vitest";

import storage from "../index";
import { StorageBackends, StorageDrives, StorageFileById, StorageFolderRoots } from "../data/documents";
import { StoragePage } from "./StoragePage";

const fileResource = testDataResource("storage.File", {
  recordRepresentation: "title", updateFields: ["title"], roots: { deletePreview: "delete_files_preview" },
  fields: ["title", "filename", "created_by_label", "upload_state", "created_at", "updated_at"].map((name) => ({
    name, kind: "scalar", scalar: "String", readable: true, filterable: false,
    sortable: false, aggregatable: false, groupable: false, creatable: false,
    updatable: name === "title", requiredOnCreate: false,
  })),
});
const resources = [fileResource, testDataResource("storage.Drive"), testDataResource("storage.Folder", { roots: { deletePreview: "delete_folders_preview" } })];
const metadata = schemaFieldMetadataFromDataResources(resources);
const scope = { filter: { drive: { exact: "drive-a" }, is_trashed: { exact: false } }, order: { updated_at: "DESC" as const }, page: 1, pageSize: 50 };
const drive = { id: "drive-a", slug: "assets", name: "Assets" };
function file(id: string, title: string) {
  return { id, title, filename: `${title}.bin`, url: "", drive: drive.id, folder: null,
    is_trashed: false, upload_state: "ready", size_bytes: 1, mime_type: null,
    created_by_label: "", updated_at: "2026-09-05T00:00:00Z" };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("cold Files navigation preserves the real shell, tree, pager and active Details tab while native reads run in parallel", async () => {
  installTestLocalStorage();
  Element.prototype.getAnimations ??= () => [];
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    matches: query === "(min-width: 64rem)", media: query, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true,
  }));
  const previews = new Map<string, ReturnType<typeof deferred<{ data: Row }>>>();
  const forms = new Map<string, ReturnType<typeof deferred<{ data: Row | null }>>>();
  const records = [file("file-a", "Alpha"), file("file-b", "Beta")];
  const getOne = vi.fn(({ id }: { id?: string | number }) => {
    const request = deferred<{ data: Row | null }>();
    forms.set(String(id), request);
    return request.promise;
  });
  const update = vi.fn();
  const custom = vi.fn(({ meta }: { meta?: Record<string, unknown> }) => {
    if (meta?.gqlQuery === StorageDrives) return Promise.resolve({ data: { drives: [drive] } });
    if (meta?.gqlQuery === StorageBackends) return Promise.resolve({ data: { backends: [] } });
    if (meta?.gqlQuery === StorageFolderRoots) return Promise.resolve({ data: { folders: [] } });
    if (meta?.gqlQuery === StorageFileById) {
      const request = deferred<{ data: Row }>();
      previews.set(String((meta.gqlVariables as { id: string }).id), request);
      return request.promise;
    }
    throw new Error("Unexpected authored request");
  });
  const provider = { getApiUrl: () => "test://files", getOne, custom, update,
    create: vi.fn(), deleteOne: vi.fn(), getList: vi.fn(async () => ({ data: records, total: records.length })),
  } as DataProvider;
  const root = createRootRoute({ component: () => <ConsoleLayout><Outlet /></ConsoleLayout> });
  const files = createRoute({ getParentRoute: () => root, path: "/storage", component: StoragePage });
  const detail = createRoute({ getParentRoute: () => files, path: "$id" });
  const router = createRouter({ routeTree: root.addChildren([files.addChildren([detail])]),
    history: createMemoryHistory({ initialEntries: [`/storage/file-a${stringifyFlatSearch(recordNavigationSearch({ folder: "" }, fileResource, scope))}`] }),
    parseSearch: parseFlatSearch, stringifySearch: stringifyFlatSearch,
  });
  const view = render(
    <Refine resources={[...refineResourcesFromDataResources(resources)]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } } } }}>
      <OperationDocumentsProvider documents={{ console: { deletePreviews: { "storage.File": "mutation DeleteFiles($id: ID!) { delete_files_preview(id: $id) { deleted } }", "storage.Folder": "mutation DeleteFolders($id: ID!) { delete_folders_preview(id: $id) { deleted } }" } } }}>
      <ModelMetadataProvider metadata={metadata}>
        <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets, routeHref: createRouteHref(storage.routes ?? []) }}>
          <ModalsHost><ToastProvider><RouterProvider router={router} /></ToastProvider></ModalsHost>
        </AppRuntimeProvider>
      </ModelMetadataProvider>
      </OperationDocumentsProvider>
    </Refine>,
  );
  const details = await screen.findByRole("tab", { name: "Details" });
  fireEvent.click(details);
  await waitFor(() => expect(forms.has("file-a") && previews.has("file-a")).toBe(true));
  await act(async () => {
    previews.get("file-a")!.resolve({ data: { files_by_pk: records[0] } });
    forms.get("file-a")!.resolve({ data: records[0]! });
  });
  await screen.findByDisplayValue("Alpha");
  const header = screen.getByRole("heading", { level: 2, name: "Alpha" });
  const frame = header.closest("header")?.parentElement ?? header.parentElement!.parentElement!;
  const tree = screen.getByRole("tree");
  const pager = screen.getByRole("navigation", { name: "Record navigation" });
  const trash = screen.getByRole("button", { name: "Trash" }) as HTMLButtonElement;
  const control = view.container.querySelector(".area-control");
  expect(control?.contains(trash)).toBe(true);
  await waitFor(() => expect((screen.getByRole("button", { name: "Next record" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Next record" }));
  await waitFor(() => expect(forms.has("file-b") && previews.has("file-b")).toBe(true));
  expect(router.state.location.pathname).toBe("/storage/file-b");
  expect(screen.queryByDisplayValue("Alpha")).toBeNull();
  expect(screen.getByRole("tab", { name: "Details" })).toBe(details);
  expect(details.getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("tree")).toBe(tree);
  expect(screen.getByRole("navigation", { name: "Record navigation" })).toBe(pager);
  expect(screen.getByRole("heading", { level: 2, name: "Loading file" })).toBe(header);
  expect(frame.isConnected).toBe(true);
  expect(screen.getByRole("button", { name: "Trash" })).toBe(trash);
  expect(trash.disabled).toBe(true);
  expect(control?.contains(trash)).toBe(true);
  expect(screen.queryByRole("textbox")).toBeNull();

  // A form response can arrive before its independent preview response.
  await act(async () => forms.get("file-b")!.resolve({ data: records[1]! }));
  await screen.findByDisplayValue("Beta");
  expect(trash.disabled).toBe(true);
  await act(async () => previews.get("file-b")!.resolve({ data: { files_by_pk: records[1] } }));
  await waitFor(() => expect(header.textContent).toBe("Beta"));
  expect(trash.disabled).toBe(false);
  expect(control?.contains(trash)).toBe(true);
  expect(update).not.toHaveBeenCalled();
  expect(getOne).toHaveBeenCalledTimes(2);

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
    await waitFor(() => expect(header.textContent).toBe(missing ? "File not found" : "Could not load this preview."));
    if (!missing) expect(await screen.findByText("Preview access denied")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Details" })).toBe(details);
    expect(details.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tree")).toBe(tree);
    expect(screen.getByRole("navigation", { name: "Record navigation" })).toBe(pager);
    expect(trash.disabled).toBe(true);
    expect(control?.contains(trash)).toBe(true);
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(trash);
    expect(update).not.toHaveBeenCalled();
    expect(provider.deleteOne).not.toHaveBeenCalled();
  }
});
