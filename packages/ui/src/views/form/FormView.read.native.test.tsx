// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { Row } from "@angee/metadata";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { FormView } from "./FormView";

const resource = testDataResource("notes.Note", {
  fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false }],
  query: testResourceQuery({ fields: { id: testQueryField("id"), title: testQueryField("title") } }),
});
const { Provider, createClient, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://form-read" });
afterEach(() => { cleanup(); clearClients(); });

function fixture(getOne: () => Promise<{ data: Row | null }>) {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const renderTab = vi.fn(() => <span>Related records</span>);
  const recordExtras = vi.fn(() => <span>Record extras</span>);
  render(<Provider dataProvider={{ getOne }} queryClient={client}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <FormView resource="notes.Note" id="note-1" fields={[{ name: "title", label: "Title" }]}
        actions={[{ id: "archive", label: "Archive", run: vi.fn() }]}
        recordTabs={[{ id: "related", label: "Related", render: renderTab }]}
        recordExtras={recordExtras} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
  return { client, renderTab, recordExtras };
}

test("a missing record replaces loading with the shared empty state and never mounts record actions or panels", async () => {
  let resolve!: (response: { data: Row | null }) => void;
  const getOne = vi.fn(() => new Promise<{ data: Row | null }>((done) => { resolve = done; }));
  const f = fixture(getOne);
  expect((await screen.findAllByRole("status")).length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  await act(async () => { resolve({ data: null }); });
  expect(await screen.findByRole("heading", { name: "Record unavailable" })).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(f.renderTab).not.toHaveBeenCalled();
  expect(f.recordExtras).not.toHaveBeenCalled();
  expect(getOne).toHaveBeenCalledOnce();
});

test("a failed record read exposes the shared retry and uses the same query to load the record", async () => {
  const getOne = vi.fn<() => Promise<{ data: Row | null }>>()
    .mockRejectedValueOnce(new Error("Connection interrupted"))
    .mockResolvedValue({ data: { id: "note-1", title: "Loaded" } });
  fixture(getOne);
  expect((await screen.findByRole("alert")).textContent).toContain("Connection interrupted");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect((screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement).value).toBe("Loaded"));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(getOne).toHaveBeenCalledTimes(2);
});

test("a failed refresh preserves the cached record and dirty draft while offering retry", async () => {
  const getOne = vi.fn<() => Promise<{ data: Row | null }>>()
    .mockResolvedValueOnce({ data: { id: "note-1", title: "Loaded" } })
    .mockRejectedValueOnce(new Error("Refresh interrupted"))
    .mockResolvedValue({ data: { id: "note-1", title: "Updated elsewhere" } });
  const f = fixture(getOne);
  const input = await screen.findByRole("textbox", { name: "Title" }) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "My draft" } });
  await act(async () => { await f.client.invalidateQueries(); });
  expect((await screen.findByRole("alert")).textContent).toContain("Refresh interrupted");
  expect(screen.getByRole("textbox", { name: "Title" })).toBe(input);
  expect(input.value).toBe("My draft");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(input.value).toBe("My draft");
  expect(getOne).toHaveBeenCalledTimes(3);
});
