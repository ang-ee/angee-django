// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";

import { AppRuntimeProvider, createRouteHref } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { RecordReference } from "./RecordReference";

const resource = testDataResource("notes.Note", {
  recordRepresentation: "title",
  query: testResourceQuery({ fields: { id: testQueryField("id"), title: testQueryField("title") } }),
});
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://record-reference", resources: [resource],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

const runtime = {
  routeHref: createRouteHref([{ name: "notes", path: "/notes" }, { name: "notes.record", path: "/notes/$id" }]),
  routesByResource: { "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } } },
};

test("reads the metadata representation and follows the registered record route", async () => {
  const getOne = vi.fn(async () => ({ data: { id: "note-1", title: "Review notes" } }));
  render(<Provider dataProvider={{ getOne }}><AppRuntimeProvider runtime={runtime}>
    <RecordReference model="notes.Note" id="note-1" />
  </AppRuntimeProvider></Provider>);
  expect((await screen.findByRole("link", { name: "Review notes" })).getAttribute("href")).toBe("/notes/note-1");
  expect(getOne).toHaveBeenCalledWith(expect.objectContaining({
    resource: "notes", id: "note-1", meta: expect.objectContaining({ fields: ["id", "title"] }),
  }));
});

test("keeps the identity readable when a record cannot be read or routed", async () => {
  const getOne = vi.fn(async () => ({ data: null }));
  render(<Provider dataProvider={{ getOne }}><AppRuntimeProvider runtime={{}}>
    <RecordReference model="notes.Note" id="note-missing" />
  </AppRuntimeProvider></Provider>);
  await waitFor(() => expect(getOne).toHaveBeenCalledOnce());
  expect(screen.getByText("note-missing")).toBeTruthy();
  expect(screen.queryByRole("link")).toBeNull();
});

test("uses a supplied label without a record read and retains the owning surface's open action", () => {
  const getOne = vi.fn();
  const open = vi.fn();
  render(<Provider dataProvider={{ getOne }}><AppRuntimeProvider runtime={runtime}>
    <RecordReference model="notes.Note" id="note-1" label="Retained note" onOpen={open} />
  </AppRuntimeProvider></Provider>);
  fireEvent.click(screen.getByRole("button", { name: "Retained note" }));
  expect(open).toHaveBeenCalledOnce();
  expect(getOne).not.toHaveBeenCalled();
  expect(screen.queryByRole("link")).toBeNull();
});
