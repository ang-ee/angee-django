// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { ShellPageTestProviders } from "@angee/app/testing";
import { NexusGraphParties } from "./documents";
import { GraphPage } from "./GraphPage";
import { NetworkPane } from "./NetworkPane";

const state = vi.hoisted(() => ({ navigate: vi.fn() }));
const graph = { party_graph: { nodes: [
  { id: "root", kind: "root", title: "Alpha", meta: { model: "parties.Party" } },
  { id: "other", kind: "party", title: "Beta", meta: { model: "parties.Party" } },
], edges: [{ id: "link", source: "root", target: "other", kind: "tie", label: "Known" }], truncated: false } };
vi.mock("@angee/refine", async (original) => ({
  ...await original<typeof import("@angee/refine")>(),
  useAuthoredQuery: (document: unknown) => ({ data: document === NexusGraphParties
    ? { parties: [{ id: "root", display_name: "Alpha" }], circles: [] } : graph,
    isFetching: false, error: null }),
}));
vi.mock("@tanstack/react-router", async (original) => ({
  ...await original<typeof import("@tanstack/react-router")>(), useNavigate: () => state.navigate,
}));
vi.mock("@angee/ui", async (original) => ({
  ...await original<typeof import("@angee/ui")>(),
  useRouteSearch: () => ({}), useRouteHref: () => Object.assign(() => undefined, { maybe: () => undefined }),
  useResourceRecordHrefLookup: () => () => undefined,
}));
beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(cleanup);

test("the relationship explorer keeps its declared horizontal graph and native selection", async () => {
  render(<ShellPageTestProviders><GraphPage /></ShellPageTestProviders>);
  const alpha = await screen.findByTestId("rf__node-root");
  expect(alpha.querySelector(".react-flow__handle-right")).toBeTruthy();
  expect(screen.getByTestId("rf__node-other").querySelector(".react-flow__handle-left")).toBeTruthy();
  fireEvent.click(alpha);
  expect(screen.getAllByText("Alpha").length).toBeGreaterThan(1);
});

test("the record network pane uses the shared graph with its existing horizontal layout", async () => {
  render(<ShellPageTestProviders><NetworkPane partyId="root" /></ShellPageTestProviders>);
  const alpha = await screen.findByTestId("rf__node-root");
  expect(alpha.querySelector(".react-flow__handle-right")).toBeTruthy();
  expect(screen.getByTestId("rf__node-other").querySelector(".react-flow__handle-left")).toBeTruthy();
});
