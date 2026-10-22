// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const sdk = vi.hoisted(() => ({ query: vi.fn(), navigate: vi.fn() }));
vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: sdk.query,
}));
import { AppRuntimeProvider, CHATTER_CONTAINERS, InAppLinkProvider, RelatedPane, containersFromChildren, createRouteHref, type ChatterViewContext } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { StorageFileAttachments } from "./data/documents";
import { fileAttachedToGroup } from "./FileAttachedToPane";

const { Provider, clearClients } = createUiTestProviders({
  resources: [testDataResource("example.Record", {
    recordRepresentation: "title", query: testResourceQuery({ fields: { id: testQueryField("id"), title: testQueryField("title") } }),
  })],
});
afterEach(() => { cleanup(); clearClients(); });

const context: ChatterViewContext = {
  pathname: "/storage/fil_1", params: { id: "fil_1" },
  route: { name: "storage.file", path: "/storage/$id", viewType: "storage/file", modelLabel: "storage.File" },
  view: { kind: "record", type: "storage/file", sqid: "fil_1" },
};

function Group() {
  return <Provider dataProvider={{ getList: async () => ({ data: [{ id: "rec_1", title: "Record rec_1" }], total: 1 }) }}>
    <InAppLinkProvider navigate={sdk.navigate}><AppRuntimeProvider runtime={{
      routeHref: createRouteHref([{ name: "records", path: "/records" }, { name: "record", path: "/records/$id" }]),
      routesByResource: { "example.Record": { collection: "records", record: { name: "record", param: "id" } } },
      containers: containersFromChildren(CHATTER_CONTAINERS, { "storage.File#groups": { "storage.attached-to": fileAttachedToGroup } }) }}>
    <RelatedPane context={context} />
  </AppRuntimeProvider></InAppLinkProvider></Provider>;
}

test("reverse attachments count only readable targets and use their shared reference labels and hrefs", async () => {
  sdk.query.mockReturnValue({ data: { file_attachments: [
    { id: "fat_1", model_label: "example.Record", record_id: "rec_1" },
    { id: "fat_hidden", model_label: null, record_id: null },
  ] } });
  const view = render(<Group />);
  expect(sdk.query).toHaveBeenCalledWith(StorageFileAttachments, { file: "fil_1" }, { enabled: true, models: ["storage.FileAttachment"] });
  expect(screen.getByText("Attached to").closest("li")?.textContent).toBe("Attached to· 1");
  const row = await screen.findByRole("link", { name: "Record rec_1" });
  expect(row.getAttribute("href")).toBe("/records/rec_1");
  fireEvent.click(row);
  expect(sdk.navigate).toHaveBeenCalledWith("/records/rec_1");
  sdk.query.mockReturnValue({ data: { file_attachments: [] } });
  view.rerender(<Group />);
  expect(screen.queryByText("Attached to")).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
});

test("the reverse read shows loading and errors while its count is unknown", () => {
  sdk.query.mockReturnValue({ data: undefined, isPending: true });
  const view = render(<Group />);
  expect(screen.getByRole("status").textContent).toContain("Loading attached records");
  sdk.query.mockReturnValue({ data: undefined, error: new Error("Read failed") });
  view.rerender(<Group />);
  expect(screen.getByRole("alert").textContent).toContain("Read failed");
});
