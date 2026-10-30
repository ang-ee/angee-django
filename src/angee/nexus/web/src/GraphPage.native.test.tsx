// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { ChatterTabsTestHost, ShellPageTestProviders } from "@angee/app/testing";
import { useChatter } from "@angee/ui";
import { NexusGraphParties } from "./documents";
import { GraphPage } from "./GraphPage";
import { NetworkPane } from "./NetworkPane";

// The real hooks return stable references; the published inspector depends on them.
const state = vi.hoisted(() => ({
  navigate: vi.fn(),
  search: {},
  routeHref: Object.assign(() => undefined, { maybe: () => undefined }),
  recordHref: () => undefined,
  error: null as Error | null,
}));
const graph = { party_graph: { nodes: [
  { id: "root", kind: "root", title: "Alpha", meta: { model: "parties.Party" } },
  { id: "other", kind: "party", title: "Beta", meta: { model: "parties.Party" } },
], edges: [{ id: "link", source: "root", target: "other", kind: "tie", label: "Known" }], truncated: false } };
vi.mock("@angee/refine", async (original) => ({
  ...await original<typeof import("@angee/refine")>(),
  useAuthoredQuery: (document: unknown) => ({ data: document === NexusGraphParties
    ? { parties: [{ id: "root", display_name: "Alpha" }], circles: [] } : graph,
    isFetching: false, error: state.error }),
}));
vi.mock("@tanstack/react-router", async (original) => ({
  ...await original<typeof import("@tanstack/react-router")>(), useNavigate: () => state.navigate,
}));
vi.mock("@angee/ui", async (original) => ({
  ...await original<typeof import("@angee/ui")>(),
  useRouteSearch: () => state.search, useRouteHref: () => state.routeHref,
  useResourceRecordHrefLookup: () => state.recordHref,
}));
beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { cleanup(); state.error = null; });

function ActiveTab() {
  const { activeTab } = useChatter();
  return <output data-testid="active-tab">{activeTab}</output>;
}

test("the relationship explorer keeps its declared horizontal graph and inspects the native selection in the chatter", async () => {
  render(<ShellPageTestProviders><GraphPage /><ChatterTabsTestHost /><ActiveTab /></ShellPageTestProviders>);
  const alpha = await screen.findByTestId("rf__node-root");
  expect(alpha.querySelector(".react-flow__handle-right")).toBeTruthy();
  expect(screen.getByTestId("rf__node-other").querySelector(".react-flow__handle-left")).toBeTruthy();
  const inspector = await screen.findByTestId("tab-graph-inspector");
  expect(within(inspector).queryByText("Alpha")).toBeNull();
  fireEvent.click(alpha);
  expect(await within(inspector).findByText("Alpha")).toBeTruthy();
  fireEvent.click(within(inspector).getByRole("button", { name: "Open timeline" }));
  expect(screen.getByTestId("active-tab").textContent).toBe("timeline.rail");
});

test("the record network pane uses the shared graph with its existing horizontal layout", async () => {
  render(<ShellPageTestProviders><NetworkPane partyId="root" /></ShellPageTestProviders>);
  const alpha = await screen.findByTestId("rf__node-root");
  expect(alpha.querySelector(".react-flow__handle-right")).toBeTruthy();
  expect(screen.getByTestId("rf__node-other").querySelector(".react-flow__handle-left")).toBeTruthy();
});

test("the network pane surfaces read errors", async () => {
  state.error = new Error("Network unavailable");
  render(<ShellPageTestProviders><NetworkPane partyId="root" /></ShellPageTestProviders>);
  expect((await screen.findByRole("alert")).textContent).toContain("Network unavailable");
  expect(screen.queryByText("No actor-visible network neighbours yet.")).toBeNull();
});
