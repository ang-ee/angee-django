// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, ChatterProvider, RecordChromeProvider, RecordFieldMarkControl, RecordFieldMarksProvider, useChatter } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const response = vi.hoisted(() => ({ changesetId: "chg_9" as string | null }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAuthoredQuery: () => ({ data: { field_provenance: [{
    field: "reference", authority: "SOURCE", origin: "Imported source",
    changeset_id: response.changesetId,
    statement_model: "example.DocumentStatement", statement_id: "stmt_9",
  }] } }),
}));
vi.mock("./documents.console", () => ({ FieldProvenanceDocument: {} }));

import { FieldProvenanceMarks } from "./FieldProvenanceMarks";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://field-provenance",
  resources: [testDataResource("example.Document")],
});

beforeEach(() => { response.changesetId = "chg_9"; });
afterEach(() => { cleanup(); clearClients(); vi.restoreAllMocks(); });

function PaneState() {
  const { activeTab, collapsed } = useChatter();
  return <output>{activeTab}:{collapsed ? "collapsed" : "expanded"}</output>;
}

async function renderMarks(history = true) {
  const root = createRootRoute();
  const route = createRoute({
    getParentRoute: () => root,
    path: "/records/$id",
    component: () => <Provider resources={[testDataResource("example.Document", { history })]}><AppRuntimeProvider runtime={{}}>
      <ChatterProvider defaultCollapsed defaultTab="timeline">
        <RecordChromeProvider value={{
          resource: "example.Document", canonicalResource: "example.Document", dataProviderName: "console",
          recordId: "doc_7", record: { id: "doc_7" }, formReadOnly: true,
        }}>
          <RecordFieldMarksProvider>
            <FieldProvenanceMarks />
            <RecordFieldMarkControl field="reference" label="Reference" />
            <PaneState />
          </RecordFieldMarksProvider>
        </RecordChromeProvider>
      </ChatterProvider>
    </AppRuntimeProvider></Provider>,
  });
  const router = createRouter({
    routeTree: root.addChildren([route]),
    history: createMemoryHistory({ initialEntries: ["/records/doc_7"] }),
  });
  // A loaded route mounts inside render's act, so the tooltip trigger's hover
  // listener, which Base UI attaches in an effect, is wired before a test hovers.
  await router.load();
  render(<RouterProvider router={router} />);
  return router;
}

test("a field's source mark reveals its exact changeset without routing a statement shadow model", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const router = await renderMarks();
  const mark = await screen.findByRole("button", { name: "Source: Reference" });
  fireEvent.click(mark);
  await waitFor(() => expect(router.state.location.search).toMatchObject({
    chatterTab: "provenance.history", chatterTarget: "chg_9", chatterFocus: "changeset",
  }));
  expect(screen.getByText("provenance.history:expanded")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Source" })).toBeNull();
  expect(warn.mock.calls.flat().join("\n")).not.toContain("example.DocumentStatement");
});

test("a historical statement without a changeset retains its source tooltip without a broken action", async () => {
  response.changesetId = null;
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  await renderMarks();
  // A plain mark with hover copy is itself the keyboard stop and the tooltip's trigger.
  const mark = await screen.findByLabelText("Source: Reference");
  fireEvent.mouseEnter(mark);
  expect(await screen.findByText("Imported source")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Source: Reference" })).toBeNull();
  expect(warn.mock.calls.flat().join("\n")).not.toContain("example.DocumentStatement");
});

test("a statement subject without native history keeps its origin without opening a missing pane", async () => {
  const router = await renderMarks(false);
  const mark = await screen.findByLabelText("Source: Reference");
  expect(screen.queryByRole("button", { name: "Source: Reference" })).toBeNull();
  expect(mark.tabIndex).toBe(0);
  fireEvent.focus(mark);
  expect(await screen.findByText("Imported source")).toBeTruthy();
  expect(router.state.location.search).not.toHaveProperty("chatterTab");
});
