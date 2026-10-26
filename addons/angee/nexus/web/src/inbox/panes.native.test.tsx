// @vitest-environment happy-dom
import { ShellPageTestProviders } from "@angee/app/testing";
import { parseFlatSearch, stringifyFlatSearch } from "@angee/app";
import { PaneToolbarHost, ResourceViewProvider, ToastProvider, useResourceView, type ResourceViewContextValue } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { InboxNavigatorPane } from "./Navigator";
import { InboxResultsPane } from "./Results";
import { InboxRelatedPane } from "./Related";
import { InboxAccounts, InboxConnections, InboxNavigator, InboxNavigatorGroups, InboxRelatedTarget, InboxResultGroups, InboxResults, InboxSelection } from "./documents";
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
    if (document === InboxSelection) return { data: { inbox_selection: { id: "party:ada", label: "Ada" } } };
    if (document === InboxNavigator) return { data: { inbox_navigator: { count: 60, message_count: 60, rows: [{
      id: variables.parent ? "circle:child" : "party:ada", label: variables.parent ? "Child circle" : "Ada", count: 60,
      latest: message.sent_at, first: message.sent_at, preview: "A preview", handle: null, party: null,
      thread: variables.options?.lens === "groups" ? message.thread : null,
      circle: variables.options?.lens === "circles" ? { id: variables.parent ? "child" : "root", name: "Circle" } : null,
      parent_id: variables.parent ? "party:ada" : null, has_children: !variables.parent,
    }] } } };
    if (document === InboxNavigatorGroups || document === InboxResultGroups) {
      return { data: { [document === InboxNavigatorGroups ? "inbox_navigator_groups" : "inbox_result_groups"]: {
        count: 30, record_count: 60, message_count: 60,
        rows: [{ value: "thread:thread-one", label: `Conversation ${variables.page}`, count: 60, message_count: 60 }],
      } } };
    }
    const content = variables.options?.lens === "attachments" || variables.options?.lens === "text";
    if (document === InboxResults) return { data: { inbox_results: { count: 60, message_count: 60, rows: [{
      id: content ? "file:attachment" : "result-one", latest: message.sent_at, count: 2, part_count: content ? 2 : 0,
      message: { ...message, title: `Message page ${variables.page}` },
      part: content ? { id: "part-one", name: "Report.pdf", role: "BODY" } : null,
      file: content ? { id: "attachment", filename: "Report.pdf", url: "/report.pdf", mime_type: { mime_type: "application/pdf" }, size_bytes: 20 } : null,
      fragment: null,
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
  const router = createRouter({ routeTree: root, parseSearch: parseFlatSearch, stringifySearch: stringifyFlatSearch,
    history: createMemoryHistory({ initialEntries: [`/${initialSearch}`] }) });
  render(<Provider dataProvider={dataProvider}><ShellPageTestProviders><ToastProvider><RouterProvider router={router} /></ToastProvider></ShellPageTestProviders></Provider>);
  return { router, custom: dataProvider.custom, get view() { return view!; } };
}

test("navigator pane controls keep finder state in s and preserve the results scope", async () => {
  const f = fixture("navigator", "?message=retained&c.page=3");
  await screen.findByRole("button", { name: /Ada/ });
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  const order = await screen.findByRole("menuitemradio", { name: "Name, ascending" });
  expect(order.getAttribute("aria-checked")).toBe("false");
  fireEvent.click(order);
  await waitFor(() => expect(f.view.state.sorting).toEqual([{ id: "label", desc: false }]));
  expect(f.router.state.location.search).toMatchObject({ message: "retained", "c.page": "3" });
  expect(f.router.state.location.search).toHaveProperty("s.sort");
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.input(await screen.findByRole("combobox", { name: "Filter records" }), { target: { value: "Ada" }, inputType: "insertText" });
  fireEvent.click(await screen.findByRole("option", { name: /Search .* for: Ada/ }));
  await waitFor(() => expect(f.view.state.filter).toEqual({ text: { iContains: "Ada" } }));
  expect(f.router.state.location.search).toHaveProperty("s.filter");
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBeTruthy();
});

test("result pane labels expand native groups and page members independently", async () => {
  const f = fixture("results");
  await screen.findByRole("button", { name: "Collapse Conversation 1" });
  const row = await screen.findByRole("button", { name: /Message page 1/ });
  expect(screen.getAllByRole("button", { name: /Message page 1/ })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: /A conversation/ })).toBeNull();
  expect(row.closest("ul")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(row);
  await waitFor(() => expect(f.router.state.location.search).toMatchObject({ thread: "thread-one", at: "message:msg-one" }));
  expect(f.router.state.location.search).not.toHaveProperty("message");
  fireEvent.click(screen.getByRole("button", { name: "Message details" }));
  await waitFor(() => expect(f.router.state.location.search).toHaveProperty("message", "msg-one"));
  const pager = screen.getByRole("navigation", { name: /Conversation 1/ });
  fireEvent.click(within(pager).getByRole("button", { name: /Next/ }));
  await screen.findByRole("button", { name: /Message page 2/ });
  expect(f.custom.mock.calls.some(([request]) => request.meta?.gqlQuery === InboxResults
    && request.meta.gqlVariables?.page === 2 && request.meta.gqlVariables?.size === 3)).toBe(true);
  expect(f.view.state.pagination.pageIndex).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "Collapse Conversation 1" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: /Message page 2/ })).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Conversation 1" }));
  await waitFor(() => expect(f.router.state.location.search).toHaveProperty("thread", "thread-one"));
});

test("related pointers open their message while its pager stays in r", async () => {
  const f = fixture("related", "?related=file:one&sender=party:ada&c.page=3");
  const row = await screen.findByRole("button", { name: /A message/ });
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() => expect(f.view.state.pagination.pageIndex).toBe(1));
  expect(f.router.state.location.search).toMatchObject({ "r.page": "2", "c.page": "3", sender: "party:ada" });
  fireEvent.click(row);
  await waitFor(() => expect(f.router.state.location.search).toHaveProperty("message", "msg-one"));
  expect(f.router.state.location.search).toHaveProperty("related", "file:one");
});

test("circle labels leave focus and activation to the tree", async () => {
  const f = fixture("navigator", "?finder=circles");
  const tree = await screen.findByRole("treeitem", { name: /Ada/ });
  expect(within(tree).queryByRole("button", { name: /Ada/ })).toBeNull();
  tree.focus();
  fireEvent.keyDown(tree, { key: "Enter" });
  await waitFor(() => expect(f.router.state.location.search).toHaveProperty("circle", "root"));
});

test.each(["senders", "recipients", "handles"])("%s selects a message scope without clearing either finder or Related", async (finder) => {
  const f = fixture("navigator", `?finder=${finder}&c.page=3&related=file:pinned&r.page=4&s.page=2`);
  fireEvent.click(await screen.findByRole("button", { name: /Ada/ }));
  await waitFor(() => expect(f.router.state.location.search).toMatchObject({ sender: "party:ada", related: "file:pinned", "r.page": "4", "s.page": "2" }));
  expect(f.router.state.location.search).not.toHaveProperty("c.page");
  expect(f.custom.mock.calls.some(([request]) => request.meta?.gqlQuery === InboxNavigator && request.meta.gqlVariables?.options?.lens === finder)).toBe(true);
});

test("group navigator opens the full conversation while retaining the selected results", async () => {
  const f = fixture("navigator", "?finder=groups&sender=party:retained&lens=attachments&c.page=3");
  fireEvent.click(await screen.findByRole("button", { name: /Ada/ }));
  await waitFor(() => expect(f.router.state.location.search).toMatchObject({ thread: "thread-one", sender: "party:retained", lens: "attachments", "c.page": "3" }));
  fireEvent.click(screen.getByRole("button", { name: "All conversations" }));
  await waitFor(() => expect(f.router.state.location.search).not.toHaveProperty("sender"));
  expect(f.router.state.location.search).not.toHaveProperty("thread");
  expect(f.router.state.location.search).toHaveProperty("lens", "attachments");
});

test("result lenses retain filters, selection and pinned content while resetting their own grouping and page", async () => {
  const filter = { text: { iContains: "renewal" }, platform: { exact: "email" } };
  const f = fixture("results", `?sender=party:ada&c.page=3&related=file:pinned&r.page=4&c.filter=${encodeURIComponent(JSON.stringify(filter))}`);
  await screen.findByRole("button", { name: /Message page 1/ });
  for (const [label, lens, field] of [["Timeline", "timeline", "by_day"], ["Attachments", "attachments", undefined], ["Shared text", "text", undefined], ["Conversations", "conversations", "by_conversation"]] as const) {
    fireEvent.click(screen.getByRole("combobox", { name: "Show results as" }));
    const option = await screen.findByRole("option", { name: label });
    fireEvent.pointerDown(option, { pointerType: "mouse", button: 0 });
    fireEvent.click(option);
    await waitFor(() => expect(f.router.state.location.search).toMatchObject({ lens, sender: "party:ada", related: "file:pinned", "r.page": "4" }));
    expect(f.view.state.pagination.pageIndex).toBe(0);
    expect(f.view.state.groupStack).toEqual(field ? [{ field }] : []);
    expect(f.view.state.filter).toEqual(filter);
    await waitFor(() => expect(f.custom.mock.calls.some(([request]) => request.meta?.gqlQuery === InboxResults
      && request.meta.gqlVariables?.options?.lens === lens && request.meta.gqlVariables?.sender === "party:ada"
      && request.meta.gqlVariables?.search?.text === "renewal")).toBe(true));
  }
});

test("an attachment previews, reads its source and pins its uses through separate actions", async () => {
  const f = fixture("results", "?lens=attachments&c.group=&sender=party:ada&r.page=4");
  fireEvent.click(await screen.findByRole("button", { name: /Report.pdf/ }));
  expect(await screen.findByRole("dialog", { name: "Report.pdf" })).toBeTruthy();
  expect(f.router.state.location.search).not.toHaveProperty("message");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("button", { name: "Read source message" }));
  await waitFor(() => expect(f.router.state.location.search).toMatchObject({ message: "msg-one", part: "part-one", sender: "party:ada" }));
  fireEvent.click(screen.getByRole("button", { name: "2 matching uses" }));
  await waitFor(() => expect(f.router.state.location.search).toMatchObject({ related: "file:attachment", relatedFrom: "msg-one", message: "msg-one", lens: "attachments" }));
  expect(f.router.state.location.search).not.toHaveProperty("r.page");
});

test("closing Related clears only its target and native collection state", async () => {
  const f = fixture("related", "?related=file:one&relatedFrom=msg-one&r.page=3&sender=party:ada&message=msg-one&c.page=4&s.page=2");
  fireEvent.click(await screen.findByRole("button", { name: "Close links" }));
  await waitFor(() => expect(f.router.state.location.search).not.toHaveProperty("related"));
  expect(f.router.state.location.search).not.toHaveProperty("relatedFrom");
  expect(f.router.state.location.search).not.toHaveProperty("r.page");
  expect(f.router.state.location.search).toMatchObject({ sender: "party:ada", message: "msg-one", "c.page": "4", "s.page": "2" });
});
