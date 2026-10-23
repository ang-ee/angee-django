// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider, useSearch } from "@tanstack/react-router";
import { testDataResource } from "@angee/metadata/testing";
import { ChatterProvider, useChatter } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

const reads = vi.hoisted(() => vi.fn());
const attribution = vi.hoisted(() => ({ source: false }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAuthoredQuery: (_document: unknown, variables: { selected: string | null; offset: number }) => {
    reads(variables);
    const offset = variables.selected === "chg_old" ? 25 : variables.offset;
    const old = offset === 25;
    return { data: { history: {
      model: "example.Document", id: "doc_7", total: 26, offset,
      entries: [{
        id: old ? "chg_old" : "chg_latest", actor: null, actor_model: null, actor_id: null,
        source_kind: attribution.source ? "PERSON" : null, action: "UPDATE", activity: "MANUAL", outcome: "SUCCESS",
        change_reason: old ? "Original source" : "Latest edit", occurred_at: "2026-01-01T00:00:00Z",
        recorded_at: "2026-01-01T00:00:00Z", can_revert: false, statements: attribution.source ? [{
          id: "stmt_7", aspect: "reference", asserted_by: "Synthetic source", source_kind: "SYSTEM_OF_RECORD",
          snak: "VALUE", rank: "NORMAL", status: "ACTIVE", applied: true, confidence: 1,
          history_type: "CREATED", values: { reference: "Statement value" }, source: null, evidence: [],
        }] : [], merge: null,
        changes: [{ model: "example.Document", id: "doc_7", field: "reference", old: "Before", new: "Applied source" }],
      }],
    } }, isLoading: false, isFetching: false };
  },
}));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  useActionResultMutation: () => [vi.fn(), { fetching: false }],
}));
vi.mock("./documents.console", () => ({ RecordHistoryDocument: {} }));

import { RecordHistoryTimeline } from "./RecordHistory";

const { Provider, clearClients } = createUiTestProviders({
  resources: [testDataResource("example.Document", { history: true })],
});
afterEach(() => { cleanup(); clearClients(); reads.mockClear(); attribution.source = false; vi.restoreAllMocks(); });

test("history and statement source kinds render without Changeset resource metadata", async () => {
  attribution.source = true;
  const warn = vi.spyOn(console, "warn");
  render(<Provider><RecordHistoryTimeline model="example.Document" id="doc_7" selected="chg_old" /></Provider>);
  expect(await screen.findByText(/Person/)).toBeTruthy();
  expect(screen.getByText("System of record")).toBeTruthy();
  expect(warn).not.toHaveBeenCalledWith(expect.stringContaining('Unknown model spelling "provenance.Changeset"'));
});

test("a selected changeset beyond the first page opens its details while offset paging stays usable", async () => {
  const view = render(<Provider><RecordHistoryTimeline model="example.Document" id="doc_7" selected="chg_old" /></Provider>);
  expect(await screen.findByText(/Original source/)).toBeTruthy();
  expect(screen.getByText("Applied source")).toBeTruthy();
  expect(reads).toHaveBeenLastCalledWith(expect.objectContaining({ selected: "chg_old", offset: 0 }));
  fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
  await waitFor(() => expect(screen.getByText(/Latest edit/)).toBeTruthy());
  expect(reads).toHaveBeenLastCalledWith(expect.objectContaining({ selected: null, offset: 0 }));
  expect(screen.queryByText("Applied source")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText(/Original source/)).toBeTruthy();
  expect(reads).toHaveBeenLastCalledWith(expect.objectContaining({ selected: null, offset: 25 }));
  // A new subject/selection resolves independently of the previous record's manual page.
  view.rerender(<Provider><RecordHistoryTimeline model="example.Document" id="doc_8" selected="chg_other" /></Provider>);
  expect(reads).toHaveBeenLastCalledWith(expect.objectContaining({ selected: "chg_other", offset: 0 }));
});

test("paging clears a consumed Chatter target so the same source can reveal it again", async () => {
  function Pane() {
    const search: Record<string, unknown> = useSearch({ strict: false });
    const { setActiveTab } = useChatter();
    return <>
      <button onClick={() => setActiveTab("provenance.history", { target: "chg_old", focus: "changeset" })}>Reveal source</button>
      <RecordHistoryTimeline model="example.Document" id="doc_7"
        selected={typeof search.chatterTarget === "string" ? search.chatterTarget : undefined} />
    </>;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/record",
    component: () => <Provider><ChatterProvider defaultTab="provenance.history"><Pane /></ChatterProvider></Provider>,
  });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({
    initialEntries: ["/record?chatterTab=provenance.history&chatterTarget=chg_old&chatterFocus=changeset"],
  }) });
  render(<RouterProvider router={router} />);
  expect(await screen.findByText("Applied source")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
  await waitFor(() => expect(router.state.location.search).not.toHaveProperty("chatterTarget", "chg_old"));
  expect(screen.getByText(/Latest edit/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Reveal source" }));
  expect(await screen.findByText("Applied source")).toBeTruthy();
  expect(screen.getByText(/Original source/)).toBeTruthy();
  expect(router.state.location.search).toMatchObject({ chatterTarget: "chg_old", chatterFocus: "changeset" });
});
