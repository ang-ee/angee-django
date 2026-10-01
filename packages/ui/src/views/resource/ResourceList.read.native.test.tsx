// @vitest-environment happy-dom

import { useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { Row } from "@angee/metadata";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { ResourceList } from "./ResourceList";

const resource = testDataResource("notes.Note", {
  roots: { list: "notes", detail: "notes_by_pk", aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  query: testResourceQuery({ fields: { id: testQueryField("id"), title: testQueryField("title") } }),
});
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource], apiUrl: "test://resource-list-read",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

test.each(["missing", "failed"] as const)("an inline collection remains reachable during loading and after a %s record read", async (result) => {
  let resolve!: (value: { data: Row | null }) => void;
  let reject!: (error: Error) => void;
  const getOne = vi.fn(() => new Promise<{ data: Row | null }>((done, fail) => { resolve = done; reject = fail; }));
  const getList = vi.fn(async () => ({ data: [{ id: "note-1", title: "Retained note" }], total: 1 }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  function Collection() {
    const [recordId, setRecordId] = useState<string>();
    return <ResourceList resource={resource.modelLabel} scope="local" placement="inline" hideCreate
      recordId={recordId} onSelect={(id) => setRecordId(id ?? undefined)} onClose={() => setRecordId(undefined)}
      columns={[{ field: "title" }]} formFields={[{ name: "title", label: "Title", readOnly: true }]} />;
  }
  render(<Provider dataProvider={{ getOne, getList }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <Collection />
    </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);

  fireEvent.click(await screen.findByRole("button", { name: "Open Retained note" }));
  expect(await screen.findByRole("status")).toBeTruthy();
  expect(screen.getByRole("button", { name: "List view" })).toBeTruthy();
  await act(async () => { result === "missing" ? resolve({ data: null }) : reject(new Error("Connection interrupted")); });
  if (result === "missing") expect(await screen.findByRole("heading", { name: "Record not found" })).toBeTruthy();
  else expect((await screen.findByRole("alert")).textContent).toContain("Connection interrupted");
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "List view" }));
  expect(await screen.findByRole("button", { name: "Open Retained note" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Record not found" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(getOne).toHaveBeenCalledOnce();
});
