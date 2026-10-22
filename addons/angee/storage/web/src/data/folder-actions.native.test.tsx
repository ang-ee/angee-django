// @vitest-environment happy-dom

import { createUiTestProviders } from "@angee/ui/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { testDataResource } from "@angee/metadata/testing";
import { useList, useOne } from "@refinedev/core";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useFolderActions } from "./use-folder-actions";

const folder = testDataResource("storage.Folder", { roots: { deletePreview: "delete_folder_preview" } });
const file = testDataResource("storage.File");
const unrelated = testDataResource("notes.Note");
const DELETE = { kind: "Document", definitions: [] };
const { Provider, clearClients } = createUiTestProviders({
  resources: [folder, file, unrelated], apiUrl: "test://folder-actions",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

test.each(["rename", "remove"] as const)("folder %s refreshes related file list and real-id detail caches", async (action) => {
  let folderName = "Before";
  const getList = vi.fn(async () => ({ data: [{ id: "file-1", folderName }], total: 1 }));
  const getOne = vi.fn(async ({ id }: { id?: string | number }) => ({ data: { id, folderName } }));
  const update = vi.fn(async () => { folderName = "Renamed"; return { data: { id: "folder-1", name: folderName } }; });
  const custom = vi.fn(async () => {
    folderName = "Drive root";
    return { data: { delete_folder_preview: {
      totalDeletedCount: 1, deleted: [], updated: [], blocked: [], hasBlockers: false,
      root: { label: "storage.Folder", objectLabel: "Before", objectId: "folder-1", children: [] },
    } } };
  });
  function Providers({ children }: { children: ReactNode }) {
    return <Provider dataProvider={{ getList, getOne, update, custom }}>
      <OperationDocumentsProvider documents={{ console: { deletePreviews: { "storage.Folder": DELETE } } }}>
        {children}
      </OperationDocumentsProvider>
    </Provider>;
  }
  const { result } = renderHook(() => ({
    list: useList({ resource: file.roots.list!, dataProviderName: "console" }),
    detail: useOne({ resource: file.roots.list!, id: "file-1", dataProviderName: "console" }),
    unrelated: useOne({ resource: unrelated.roots.list!, id: "note-1", dataProviderName: "console" }),
    actions: useFolderActions(),
  }), { wrapper: Providers });
  await waitFor(() => expect(result.current.list.result.data?.[0]?.folderName).toBe("Before"));
  await waitFor(() => expect(result.current.detail.result?.folderName).toBe("Before"));
  await waitFor(() => expect(result.current.unrelated.result?.id).toBe("note-1"));
  await act(async () => {
    if (action === "rename") await result.current.actions.rename("folder-1", "Renamed");
    else await result.current.actions.remove("folder-1");
  });
  const expected = action === "rename" ? "Renamed" : "Drive root";
  await waitFor(() => expect(result.current.list.result.data?.[0]?.folderName).toBe(expected));
  await waitFor(() => expect(result.current.detail.result?.folderName).toBe(expected));
  expect(getList).toHaveBeenCalledTimes(2);
  expect(getOne.mock.calls.filter(([params]) => params.id === "file-1")).toHaveLength(2);
  expect(getOne.mock.calls.filter(([params]) => params.id === "note-1")).toHaveLength(1);
});
