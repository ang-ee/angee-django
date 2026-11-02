// @vitest-environment happy-dom

import { act, cleanup, renderHook, screen, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { useList, useOne } from "@refinedev/core";
import gql from "graphql-tag";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { createUiTestProviders } from "../../testing";
import { ToastProvider } from "../../feedback";
import { useDeleteWithPreview } from "./resource-operations";

const resource = testDataResource("notes.Note", { roots: { deletePreview: "delete_note_preview" } });
const unrelated = testDataResource("storage.File");
const DELETE = gql`mutation DeleteNote($id: ID!, $confirm: Boolean!) {
  delete_note_preview(id: $id, confirm: $confirm) { total_deleted_count }
}`;
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource, unrelated], apiUrl: "test://delete-preview",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

test.each(["success", "refused", "blocked"] as const)("delete preview refreshes the native resource caches only after success (%s)", async (outcome) => {
  const refused = outcome === "refused";
  const completed = outcome === "success";
  let deleted = false;
  const getList = vi.fn(async () => ({ data: deleted ? [] : [{ id: "note-1", title: "Retained" }], total: deleted ? 0 : 1 }));
  const getOne = vi.fn(async ({ id }: { id?: string | number }) => ({
    data: id === "note-1" && deleted ? null : { id, title: "Retained" },
  }));
  const custom = vi.fn(async () => {
    if (refused) throw new Error("Deletion refused");
    deleted = completed;
    return { data: { delete_note_preview: {
      total_deleted_count: completed ? 1 : 0, deleted: [], updated: [], blocked: [], has_blockers: outcome === "blocked",
      root: { label: "notes.Note", object_label: "Retained", object_id: "note-1", children: [] },
    } } };
  });
  function Providers({ children }: { children: ReactNode }) {
    return <Provider dataProvider={{ getList, getOne, custom }}>
      <OperationDocumentsProvider documents={{ console: { deletePreviews: { "notes.Note": DELETE } } }}>
        <ToastProvider>{children}</ToastProvider>
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
    else await expect(mutation).resolves.toBe(completed);
  });
  await waitFor(() => expect(result.current.list.result.total).toBe(completed ? 0 : 1));
  if (completed) await waitFor(() => expect(result.current.detail.result).toBeNull());
  if (outcome === "blocked") expect(screen.getByRole("alert").textContent).toContain("Some related records block deletion.");
  expect(getList).toHaveBeenCalledTimes(completed ? 2 : 1);
  expect(getOne.mock.calls.filter(([params]) => params.id === "note-1")).toHaveLength(completed ? 2 : 1);
  expect(getOne.mock.calls.filter(([params]) => params.id === "file-1")).toHaveLength(1);
});

test.each([false, undefined] as const)("delete notification ownership preserves native auth handling (notification=%s)", async (errorNotification) => {
  const failure = new Error("Deletion refused");
  const notify = vi.fn();
  const onError = vi.fn(async () => ({}));
  function Providers({ children }: { children: ReactNode }) {
    return <Provider
      dataProvider={{ custom: async () => { throw failure; } }}
      notificationProvider={{ open: notify, close: vi.fn() }}
      authProvider={{ login: async () => ({ success: true }), logout: async () => ({ success: true }), check: async () => ({ authenticated: true }), onError }}
    >
      <ToastProvider>
        <OperationDocumentsProvider documents={{ console: { deletePreviews: { "notes.Note": DELETE } } }}>
          {children}
        </OperationDocumentsProvider>
      </ToastProvider>
    </Provider>;
  }
  const { result } = renderHook(() => useDeleteWithPreview(resource), { wrapper: Providers });
  await act(async () => {
    await expect(result.current.remove("note-1", { errorNotification })).rejects.toBe(failure);
  });
  await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
  expect(notify).toHaveBeenCalledTimes(errorNotification === false ? 0 : 1);
});
