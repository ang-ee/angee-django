// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { useList, useOne } from "@refinedev/core";
import gql from "graphql-tag";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { createUiTestProviders } from "../../testing";
import { useDeleteWithPreview } from "./resource-operations";

const resource = testDataResource("notes.Note", { roots: { deletePreview: "delete_note_preview" } });
const unrelated = testDataResource("storage.File");
const DELETE = gql`mutation DeleteNote($id: ID!, $confirm: Boolean!) {
  delete_note_preview(id: $id, confirm: $confirm) { totalDeletedCount }
}`;
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource, unrelated], apiUrl: "test://delete-preview",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

test.each([false, true])("delete preview refreshes the native resource caches only after success (refused=%s)", async (refused) => {
  let deleted = false;
  const getList = vi.fn(async () => ({ data: deleted ? [] : [{ id: "note-1", title: "Retained" }], total: deleted ? 0 : 1 }));
  const getOne = vi.fn(async ({ id }: { id?: string | number }) => ({
    data: id === "note-1" && deleted ? null : { id, title: "Retained" },
  }));
  const custom = vi.fn(async () => {
    if (refused) throw new Error("Deletion refused");
    deleted = true;
    return { data: { delete_note_preview: {
      totalDeletedCount: 1, deleted: [], updated: [], blocked: [], hasBlockers: false,
      root: { label: "notes.Note", objectLabel: "Retained", objectId: "note-1", children: [] },
    } } };
  });
  function Providers({ children }: { children: ReactNode }) {
    return <Provider dataProvider={{ getList, getOne, custom }}>
      <OperationDocumentsProvider documents={{ console: { deletePreviews: { "notes.Note": DELETE } } }}>
        {children}
      </OperationDocumentsProvider>
    </Provider>;
  }
  const { result } = renderHook(() => ({
    list: useList({ resource: resource.roots.list!, dataProviderName: "console" }),
    detail: useOne({ resource: resource.roots.list!, id: "note-1", dataProviderName: "console" }),
    unrelated: useOne({ resource: unrelated.roots.list!, id: "file-1", dataProviderName: "console" }),
    deletion: useDeleteWithPreview(resource),
  }), { wrapper: Providers });
  await waitFor(() => expect(result.current.list.result.total).toBe(1));
  await waitFor(() => expect(result.current.detail.result?.id).toBe("note-1"));
  await waitFor(() => expect(result.current.unrelated.result?.id).toBe("file-1"));

  await act(async () => {
    const mutation = result.current.deletion.remove("note-1");
    if (refused) await expect(mutation).rejects.toThrow("Deletion refused");
    else await mutation;
  });
  await waitFor(() => expect(result.current.list.result.total).toBe(refused ? 1 : 0));
  if (!refused) await waitFor(() => expect(result.current.detail.result).toBeNull());
  expect(getList).toHaveBeenCalledTimes(refused ? 1 : 2);
  expect(getOne.mock.calls.filter(([params]) => params.id === "note-1")).toHaveLength(refused ? 1 : 2);
  expect(getOne.mock.calls.filter(([params]) => params.id === "file-1")).toHaveLength(1);
});
