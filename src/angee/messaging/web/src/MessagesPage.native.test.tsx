// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { ResourceQuery, type DataResourceFieldMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { ActionSelectionLimit, AppRuntimeProvider, ModalsHost, ToastProvider, ResourceViewProvider } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import type { ReactElement } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { HeldDraftActions, MessagesPage, type MessageListRow } from "./MessagesPage";

const query = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, preview: { scalar: "String" },
  created_at: { scalar: "DateTime" }, sent_at: { scalar: "DateTime" }, starred: { scalar: "Boolean" },
  revision: { scalar: "Int" }, permissions: { scalar: "String" },
  is_held: { scalar: "Boolean" },
  channel: { kind: "relation", identityPath: "channel.id", labelPath: "channel.display_name" },
} }).contract;
query.fields.channel!.relation = { model: "messaging.Channel", identityPath: "channel.id", labelPath: "channel.display_name" };
query.fields.channel!.filter!.field = "channel.id";
query.axes.channel!.server = { input: "CHANNEL", key: "channel_id", labelInput: "CHANNEL__DISPLAY_NAME", labelKey: "channel_name" };
query.axes.channel!.drill = { kind: "identity", field: "channel", valueKey: "channel_id", nullMode: "isNull", valueMap: [] };
const fields: DataResourceFieldMetadata[] = Object.entries(query.fields).map(([name, field]) => ({
  name, kind: field.kind === "relation" ? "relation" : "scalar", scalar: field.scalar,
  readable: true, creatable: false, updatable: false, aggregatable: false, requiredOnCreate: false,
  ...(name === "channel" ? { relationModelLabel: "messaging.Channel", relationObject: true } : {}),
}));
const resource = testDataResource("messaging.Message", {
  roots: { groups: "messages_groups", aggregate: "messages_aggregate", deletePreview: "preview_delete_message", update: undefined, create: undefined },
  capabilities: ["list", "detail", "delete"], fields, query, recordRepresentation: "title",
  typeNames: { filter: "MessageBoolExp", order: "MessageOrderBy" },
});
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource, testDataResource("messaging.Thread")], apiUrl: "test://messages",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

// A cold grouped route chains router load, metadata, the action limit, the grouped
// query and the group's rows before its first row renders; a shared CI runner can
// exceed the 1 s default, so the fixture owns that one wait.
const FIRST_ROWS_TIMEOUT = 5000;

const HELD = { is_held: true } as const;

function rowCheckbox(title: string): HTMLElement {
  return within(screen.getByText(title).closest("tr")!).getByRole("checkbox", { name: "Select row" });
}

function checked(element: HTMLElement): boolean {
  return element.getAttribute("aria-checked") === "true";
}

function dataProvider({ count = 2, fail = false, transportFailure = false, limitFailure = false } = {}) {
  const rows: MessageListRow[] = Array.from({ length: count }, (_, index) => ({
    id: String(index + 1), title: `Message ${index + 1}`, preview: "A held reply",
    created_at: "2026-10-01T00:00:00Z", channel: { id: "channel", display_name: "Discussion" },
    revision: index + 3, permissions: ["read", "delete", "send_held"],
  }));
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    if (meta?.gqlQuery === ActionSelectionLimit) {
      if (limitFailure) throw new Error("Limit unavailable");
      return { data: { action_selection_limit: 100 } };
    }
    if (meta?.gqlMutation) {
      if (transportFailure) throw new Error("Publish unavailable");
      const { selection } = meta.gqlVariables as { selection: { id: string; expected_revision: number }[] };
      const results = selection.map((item, index) => ({ ...item, ok: !(fail && index === 1),
        code: fail && index === 1 ? "revision_conflict" : null, message: fail && index === 1 ? "Revision changed" : "Done" }));
      for (const result of results) if (!result.ok) {
        const index = rows.findIndex((row) => row.id === result.id);
        rows[index] = { ...rows[index]!, revision: result.expected_revision + 1 };
      }
      return { data: { send_held_drafts: results, discard_held_drafts: results } };
    }
    return { data: { messages_groups: [{ key: { channel_id: "channel", channel_name: "Discussion" }, aggregate: { count } }], totalCount: 1 } };
  });
  const getList = vi.fn(async () => ({ data: [...rows], total: count }));
  return { rows, custom, getList, mutations: () => custom.mock.calls.filter(([request]) => request.meta?.gqlMutation) };
}

/** Mount a component as the /messages route renders it: no ambient view above it. */
function mount(component: () => ReactElement, provider: ReturnType<typeof dataProvider>) {
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/messages", validateSearch: (search) => search, component });
  const record = createRoute({ getParentRoute: () => route, path: "$id", component: () => null });
  const router = createRouter({ routeTree: root.addChildren([route.addChildren([record])]), history: createMemoryHistory({ initialEntries: ["/messages"] }) });
  render(<Provider dataProvider={{ getList: provider.getList, custom: provider.custom }}><AppRuntimeProvider runtime={{}}>
    <OperationDocumentsProvider documents={{ console: { groups: { "messaging.Message":
      "query MessageGroups { messages_groups { key { channel_id channel_name } aggregate { count } } totalCount }",
    } } }}><ModalsHost><ToastProvider><RouterProvider router={router} /></ToastProvider></ModalsHost></OperationDocumentsProvider>
  </AppRuntimeProvider></Provider>);
  return router;
}

/** The messages route, whose ResourceList owns the one view the page and its held verbs read. */
async function fixture({ held = false, ...data }: { held?: boolean } & Parameters<typeof dataProvider>[0] = {}) {
  const provider = dataProvider(data);
  const router = mount(MessagesPage, provider);
  await screen.findByText("Message 1", {}, { timeout: FIRST_ROWS_TIMEOUT });
  /** Edit the route's filter, which the view reads from the URL as JSON text. */
  const showFilter = async (filter: object): Promise<void> => {
    await act(async () => { await router.navigate({ to: "/messages", search: { filter: JSON.stringify(filter) } }); });
    await screen.findByText("Message 1", {}, { timeout: FIRST_ROWS_TIMEOUT });
  };
  if (held) await showFilter(HELD);
  return { ...provider, router, showFilter };
}

test("the ordinary grouped route retains selection and the shared delete action", async () => {
  await fixture();
  fireEvent.click(screen.getAllByRole("checkbox", { name: "Select row" })[0]!);
  expect(await screen.findByRole("button", { name: /Delete/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
});

test("route edits and browser back clear selection while preserving grouping", async () => {
  const f = await fixture({ held: true });
  fireEvent.click(rowCheckbox("Message 1"));
  await screen.findByRole("button", { name: "Send now" });
  await f.showFilter({ ...HELD, title: { iContains: "Message" } });
  expect(checked(rowCheckbox("Message 1"))).toBe(false);
  fireEvent.click(rowCheckbox("Message 2"));
  await waitFor(() => expect(checked(rowCheckbox("Message 2"))).toBe(true));
  await act(async () => { f.router.history.back(); });
  await waitFor(() => expect(f.router.state.location.search).toHaveProperty("filter", JSON.stringify(HELD)));
  await waitFor(() => expect(checked(rowCheckbox("Message 2"))).toBe(false));
  expect(screen.getByRole("checkbox", { name: "Select the loaded rows in Discussion" })).toBeTruthy();
  // The restored held filter still offers its verbs over a fresh selection.
  fireEvent.click(rowCheckbox("Message 1"));
  expect(await screen.findByRole("button", { name: "Send now" })).toBeTruthy();
});

test.each([false, true])("successful held actions discarding=%s clear selection and report a plural outcome", async (discarding) => {
  const f = await fixture({ held: true });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all loaded rows" }));
  const name = discarding ? "Discard" : "Send now";
  const action = await screen.findByRole("button", { name });
  await waitFor(() => expect(action.hasAttribute("disabled")).toBe(false));
  fireEvent.click(action);
  fireEvent.click(action);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name }));
  await waitFor(() => expect(f.mutations()).toHaveLength(1));
  expect(f.mutations()[0]![0].meta?.gqlVariables).toEqual({ selection: [
    { id: "1", expected_revision: 3 }, { id: "2", expected_revision: 4 },
  ] });
  await waitFor(() => expect(checked(rowCheckbox("Message 1")) || checked(rowCheckbox("Message 2"))).toBe(false));
  expect(await screen.findByText(discarding ? "Discarded 2 held drafts." : "Queued 2 held drafts.")).toBeTruthy();
});

test.each([false, true])("a grouped held action discarding=%s sends selected revisions and drops refused ids for reselection", async (discarding) => {
  const f = await fixture({ held: true, fail: true });
  fireEvent.click(screen.getByRole("checkbox", { name: "Select the loaded rows in Discussion" }));
  const action = await screen.findByRole("button", { name: discarding ? "Discard" : "Send now" });
  await waitFor(() => expect(action.hasAttribute("disabled")).toBe(false));
  fireEvent.click(action);
  const dialog = await screen.findByRole("alertdialog");
  expect(f.mutations()).toHaveLength(0);
  fireEvent.click(within(dialog).getByRole("button", { name: discarding ? "Discard" : "Send now" }));
  await waitFor(() => expect(f.mutations()).toHaveLength(1));
  expect(f.mutations()[0]![0].meta?.gqlVariables).toEqual({ selection: [
    { id: "1", expected_revision: 3 }, { id: "2", expected_revision: 4 },
  ] });
  await waitFor(() => expect(checked(rowCheckbox("Message 1")) || checked(rowCheckbox("Message 2"))).toBe(false));
  expect(await screen.findByText(`${discarding ? "Discarded one held draft." : "Queued one held draft."}; Revision changed`, { selector: "h2" })).toBeTruthy();
  // Publishing invalidates the loaded rows. Reselecting captures the on-screen revision.
  await waitFor(() => expect(f.getList.mock.calls.length).toBeGreaterThan(1));
  fireEvent.click(rowCheckbox("Message 2"));
  const retry = await screen.findByRole("button", { name: discarding ? "Discard" : "Send now" });
  await waitFor(() => expect(retry.hasAttribute("disabled")).toBe(false));
  fireEvent.click(retry);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: discarding ? "Discard" : "Send now" }));
  await waitFor(() => expect(f.mutations()).toHaveLength(2));
  expect(f.mutations()[1]![0].meta?.gqlVariables).toEqual({ selection: [{ id: "2", expected_revision: 5 }] });
});

test("a transport refusal keeps selection and permits another attempt", async () => {
  const f = await fixture({ held: true, transportFailure: true });
  fireEvent.click(rowCheckbox("Message 1"));
  const action = await screen.findByRole("button", { name: "Send now" });
  await waitFor(() => expect(action.hasAttribute("disabled")).toBe(false));
  fireEvent.click(action);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Send now" }));
  await screen.findByText("Publish unavailable", { selector: "h2" });
  expect(f.mutations()[0]![0].meta?.gqlVariables).toEqual({ selection: [{ id: "1", expected_revision: 3 }] });
  expect(checked(rowCheckbox("Message 1"))).toBe(true);
  expect(checked(rowCheckbox("Message 2"))).toBe(false);
  await waitFor(() => expect(action.hasAttribute("disabled")).toBe(false));
});

test("both held verbs disable above the server's selection bound", async () => {
  const f = await fixture({ held: true, count: 101 });
  const all = screen.getByRole("checkbox", { name: "Select all loaded rows" });
  fireEvent.click(all);
  expect(checked(all)).toBe(true);
  expect((await screen.findByRole("button", { name: "Send now" })).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Discard" }).hasAttribute("disabled")).toBe(true);
  const reason = await screen.findByText("Select at most 100 held drafts.");
  expect(screen.getByRole("button", { name: "Send now" }).getAttribute("aria-describedby")).toBe(reason.id);
  expect(screen.getByRole("button", { name: "Discard" }).getAttribute("aria-describedby")).toBe(reason.id);
  expect(f.mutations()).toHaveLength(0);
});

test("an unavailable selected row explains both disabled verbs without a loaded-row policy projection", async () => {
  // A selected id whose row was never captured: only the verbs' own view can hold it.
  const f = dataProvider();
  mount(() => <ResourceViewProvider resource="messaging.Message" scope="local">
    <HeldDraftActions ids={new Set(["unloaded"])} rows={[]} />
  </ResourceViewProvider>, f);
  const reason = await screen.findByText("Select readable held drafts with permission to send or discard them.");
  for (const name of ["Send now", "Discard"]) {
    const button = screen.getByRole("button", { name });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.getAttribute("aria-describedby")).toBe(reason.id);
  }
  expect(f.mutations()).toHaveLength(0);
});

test("an action limit read error explains both disabled verbs instead of loading forever", async () => {
  const f = await fixture({ held: true, limitFailure: true });
  fireEvent.click(rowCheckbox("Message 1"));
  const reason = await screen.findByText("Could not load the action selection limit. Refresh to try again.");
  for (const name of ["Send now", "Discard"]) {
    const button = screen.getByRole("button", { name });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.getAttribute("aria-describedby")).toBe(reason.id);
  }
  expect(screen.queryByText("Loading the action selection limit.")).toBeNull();
  expect(f.mutations()).toHaveLength(0);
});
