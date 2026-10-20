// @vitest-environment happy-dom
import { ShellPageTestProviders } from "@angee/app/testing";
import { PaneToolbarHost, ResourceViewProvider, ToastProvider, useResourceView, type ResourceViewContextValue } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { InboxNavigatorPane } from "./Navigator";
import { InboxResultsPane } from "./Results";
import { InboxRelatedPane } from "./Related";
import { InboxAccounts, InboxConnections, InboxNavigator, InboxNavigatorGroups, InboxRelatedTarget, InboxResultGroups, InboxResults } from "./documents";
import { COLLECTION_INITIAL, RESULTS_INITIAL, useInboxNavigation } from "./state";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://nexus-panes", providerNames: [],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } } });
afterEach(() => { cleanup(); clearClients(); });

const message = {
  id: "msg-one", title: "A message", preview: "A preview", platform: "email", direction: "INBOUND",
  sent_at: "2026-10-01T12:00:00Z", created_at: "2026-10-01T12:00:00Z", channel: null,
  sender: { display_name: "Ada", value: "ada@example.com", party: null }, thread: { id: "thread-one", title: { text: "A conversation" } },
};

function fixture(pane: "navigator" | "results" | "related", initialSearch = "") {
  let view: ResourceViewContextValue;
  const dataProvider = { custom: vi.fn(async ({ meta }) => {
    const variables = meta?.gqlVariables ?? {};
    const document = meta?.gqlQuery;
    if (document === InboxAccounts) return { data: { inbox_accounts: [], inbox_platforms: ["email"], inbox_relation_kinds: [] } };
    if (document === InboxNavigator) return { data: { inbox_navigator: { count: 60, message_count: 60, rows: [{
      id: variables.parent ? "circle:child" : "party:ada", label: variables.parent ? "Child circle" : "Ada", count: 60,
      latest: message.sent_at, first: message.sent_at, preview: "A preview", handle: null, party: null, thread: null,
      circle: variables.options?.lens === "circles" ? { id: variables.parent ? "child" : "root", name: "Circle" } : null,
      parent_id: variables.parent ? "party:ada" : null, has_children: !variables.parent,
    }] } } };
    if (document === InboxNavigatorGroups || document === InboxResultGroups) {
      return { data: { [document === InboxNavigatorGroups ? "inbox_navigator_groups" : "inbox_result_groups"]: {
        count: 30, record_count: 60, message_count: 60,
        rows: [{ value: "thread:thread-one", label: `Conversation ${variables.page}`, count: 60, message_count: 60 }],
      } } };
    }
    if (document === InboxResults) return { data: { inbox_results: { count: 60, message_count: 60, rows: [{
      id: "result-one", latest: message.sent_at, count: 1, part_count: 0, message: { ...message, title: `Message page ${variables.page}` },
      part: null, file: null, fragment: null,
    }] } } };
    if (document === InboxRelatedTarget) return { data: { inbox_related_target: {
      id: "file:one", label: "Pinned file", count: 60, unit: "messages", source: null, part: null, file: null, fragment: null,
    } } };
    if (document === InboxConnections) return { data: { inbox_connections: {
      count: 60, message_count: 60, rows: [{ id: "connection-one", message, part: null, handle: null, src: null, dst: null }],
    } } };
    throw new Error("Unexpected inbox operation");
  }) } satisfies RefineTestDataProvider;
  function Pane() {
    view = useResourceView();
    const navigation = useInboxNavigation();
    return pane === "navigator" ? <InboxNavigatorPane coverage={{}} onCoverage={vi.fn()} timezone="UTC" navigation={navigation} />
      : pane === "results" ? <InboxResultsPane timezone="UTC" navigation={navigation} />
        : <PaneToolbarHost><InboxRelatedPane navigation={navigation} /></PaneToolbarHost>;
  }
  const namespace = { navigator: "s", results: "c", related: "r" }[pane];
  const root = createRootRoute({ component: () => <ResourceViewProvider namespace={namespace}
    initialState={pane === "results" ? RESULTS_INITIAL : COLLECTION_INITIAL}><Pane /></ResourceViewProvider> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [`/${initialSearch}`] }) });
  render(<Provider dataProvider={dataProvider}><ShellPageTestProviders><ToastProvider><RouterProvider router={router} /></ToastProvider></ShellPageTestProviders></Provider>);
  return { router, custom: dataProvider.custom, get view() { return view!; } };
}

test("navigator pane controls keep finder state in s and preserve the results scope", async () => {
  const f = fixture("navigator", "?message=retained&c.page=3");
  await screen.findByRole("button", { name: /Ada.*Results/ });
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  const order = await screen.findByRole("menuitemradio", { name: "Name, ascending" });
  expect(order.getAttribute("aria-checked")).toBe("false");
  fireEvent.click(order);
  await waitFor(() => expect(f.view.state.sorting).toEqual([{ id: "label", desc: false }]));
  expect(f.router.state.location.search).toMatchObject({ message: "retained", "c.page": 3 });
  expect(f.router.state.location.search).toHaveProperty("s.sort");
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.input(await screen.findByRole("combobox", { name: "Filter records" }), { target: { value: "Ada" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: /Search .* for: Ada/ }));
  await waitFor(() => expect(f.view.state.filter).toEqual({ text: { iContains: "Ada" } }));
  expect(f.router.state.location.search).toHaveProperty("s.filter");
  const scopes = screen.getAllByRole("button", { pressed: true });
  expect(scopes.some((button) => button.textContent?.includes("Ada"))).toBe(true);
});

test("result pane labels expand native groups and page members independently", async () => {
  const f = fixture("results");
  fireEvent.click(await screen.findByRole("button", { name: "Conversation 1 · 60" }));
  const row = await screen.findByRole("button", { name: /Message page 1.*Read/ });
  expect(screen.getAllByRole("button", { name: /Message page 1.*Read/ })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: /A conversation/ })).toBeNull();
  expect(row.closest("ul")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  const pager = screen.getByRole("navigation", { name: /Conversation 1/ });
  fireEvent.click(within(pager).getByRole("button", { name: /Next/ }));
  await screen.findByRole("button", { name: /Message page 2.*Read/ });
  expect(f.custom.mock.calls.some(([request]) => request.meta?.gqlQuery === InboxResults
    && request.meta.gqlVariables?.page === 2 && request.meta.gqlVariables?.size === 3)).toBe(true);
  expect(f.view.state.pagination.pageIndex).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(await within(await screen.findByRole("menu")).findByRole("menuitemradio", { name: /[Pp]latform/ }));
  await waitFor(() => expect(f.view.state.groupStack).toEqual([{ field: "by_platform" }]));
});

test("related pointers open their message while its pager stays in r", async () => {
  const f = fixture("related", "?related=file:one&sender=party:ada&c.page=3");
  const row = await screen.findByRole("button", { name: /A message.*Read/ });
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() => expect(f.view.state.pagination.pageIndex).toBe(1));
  expect(f.router.state.location.search).toMatchObject({ "r.page": 2, "c.page": 3, sender: "party:ada" });
  fireEvent.click(row);
  await waitFor(() => expect(f.router.state.location.search).toHaveProperty("message", "msg-one"));
  expect(f.router.state.location.search).toHaveProperty("related", "file:one");
});

test("circle labels leave focus and activation to the tree", async () => {
  const f = fixture("navigator", "?finder=circles");
  const tree = await screen.findByRole("treeitem", { name: /Ada/ });
  expect(within(tree).queryByRole("button", { name: /Ada.*Results/ })).toBeNull();
  tree.focus();
  fireEvent.keyDown(tree, { key: "Enter" });
  await waitFor(() => expect(f.router.state.location.search).toHaveProperty("circle", "root"));
});
