// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { AppRuntimeProvider, createRouteHref } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { InAppLinkProvider, routerNavigator } from "../../lib/in-app-link";
import { RecordReference, RecordReferenceRow } from "./RecordReference";

const resource = testDataResource("notes.Note", {
  recordRepresentation: "title",
  query: testResourceQuery({ fields: { id: testQueryField("id"), title: testQueryField("title") } }),
});
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://record-reference", resources: [resource, testDataResource("example.Unrouted", {
    recordRepresentation: "name", query: testResourceQuery({ fields: { id: testQueryField("id"), name: testQueryField("name") } }),
  })],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

const runtime = {
  routeHref: createRouteHref([{ name: "notes", path: "/notes" }, { name: "notes.record", path: "/notes/$id" }]),
  routesByResource: { "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } } },
};

/** Render inside a router, as every console surface is. */
function renderInRouter(ui: ReactNode) {
  const root = createRootRoute({ component: () => <InAppLinkProvider navigate={navigate}>{ui}</InAppLinkProvider> });
  const router = createRouter({
    routeTree: root.addChildren(["/home", "/notes/$id"].map((path) => createRoute({ getParentRoute: () => root, path }))),
    history: createMemoryHistory({ initialEntries: ["/home"] }),
  });
  const navigate = routerNavigator(router);
  render(<RouterProvider router={router} />);
  return router;
}

test("reads the metadata representation and follows the registered record route in-app", async () => {
  const getOne = vi.fn(async () => ({ data: { id: "note-1", title: "Review notes" } }));
  const router = renderInRouter(<Provider dataProvider={{ getOne }}><AppRuntimeProvider runtime={runtime}>
    <RecordReference model="notes.Note" id="note-1" />
  </AppRuntimeProvider></Provider>);
  const link = await screen.findByRole("link", { name: "Review notes" });
  expect(link.getAttribute("href")).toBe("/notes/note-1");
  expect(getOne).toHaveBeenCalledWith(expect.objectContaining({
    resource: "notes", id: "note-1", meta: expect.objectContaining({ fields: ["id", "title"] }),
  }));
  // The router follows the link; a plain anchor would reload the whole console.
  expect(fireEvent.click(link)).toBe(false);
  await waitFor(() => expect(router.state.location.pathname).toBe("/notes/note-1"));
});

test("keeps the identity readable when a record cannot be read or routed", async () => {
  const getOne = vi.fn(async () => ({ data: null }));
  renderInRouter(<Provider dataProvider={{ getOne }}><AppRuntimeProvider runtime={{}}>
    <RecordReference model="notes.Note" id="note-missing" />
  </AppRuntimeProvider></Provider>);
  await waitFor(() => expect(getOne).toHaveBeenCalledOnce());
  expect(screen.getByText("note-missing")).toBeTruthy();
  expect(screen.queryByRole("link")).toBeNull();
});

test("uses a supplied label without a record read and follows the exact tab and detail search", async () => {
  const getOne = vi.fn();
  const router = renderInRouter(<Provider dataProvider={{ getOne }}><AppRuntimeProvider runtime={runtime}>
    <RecordReference model="notes.Note" id="note-1" label="Retained note" tab="source" search={{ query: "passage", stale: null }} />
  </AppRuntimeProvider></Provider>);
  const link = await screen.findByRole("link", { name: "Retained note" });
  expect(link.getAttribute("href")).toBe("/notes/note-1?recordTab=source&query=passage");
  expect(fireEvent.click(link)).toBe(false);
  await waitFor(() => expect(router.state.location.href).toBe("/notes/note-1?recordTab=source&query=passage"));
  expect(getOne).not.toHaveBeenCalled();
  expect(screen.queryByRole("button")).toBeNull();
});

test("reference rows batch one bounded label read per model and preserve interleaved order", async () => {
  const getOne = vi.fn();
  const getList = vi.fn(async ({ resource }: { resource: string }) => ({
    data: resource === "notes" ? [{ id: "note-1", title: "First note" }, { id: "note-2", title: "Second note" }]
      : [{ id: "other-1", name: "Unrouted record" }], total: resource === "notes" ? 2 : 1,
  }));
  const references = [
    { model: "notes.Note", id: "note-2" }, { model: "example.Unrouted", id: "other-1" },
    { model: "notes.Note", id: "note-1" }, { model: "notes.Note", id: "note-2" },
  ];
  render(<Provider dataProvider={{ getList, getOne }}><AppRuntimeProvider runtime={runtime}>
    <ul>{references.map((record, index) => <li key={index}><RecordReferenceRow {...record} references={references} /></li>)}</ul>
  </AppRuntimeProvider></Provider>);
  await screen.findByText("Unrouted record");
  await screen.findByRole("link", { name: "First note" });
  expect(getList).toHaveBeenCalledTimes(2);
  expect(getOne).not.toHaveBeenCalled();
  expect(getList).toHaveBeenCalledWith(expect.objectContaining({
    resource: "notes", filters: [{ field: "id", operator: "in", value: ["note-1", "note-2"] }],
    pagination: expect.objectContaining({ pageSize: 2 }), meta: expect.objectContaining({ fields: ["id", "title"] }),
  }));
  expect(screen.getByText("Unrouted record").closest("a, button")).toBeNull();
  expect(screen.getAllByRole("listitem").map((row) => row.textContent)).toEqual(["Second note", "Unrouted record", "First note", "Second note"]);
});
