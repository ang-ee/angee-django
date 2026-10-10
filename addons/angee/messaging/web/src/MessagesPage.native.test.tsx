// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { ResourceQuery, type DataResourceFieldMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import {
  ActionSelectionLimit, AppRuntimeProvider, ModalsHost, ToastProvider, ResourceViewProvider, useResourceView,
  type ResourceViewContextValue,
} from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { MessagesPage, type MessageListRow } from "./MessagesPage";

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

function fixture({ held = false, count = 2, fail = false, transportFailure = false } = {}) {
  let view!: ResourceViewContextValue;
  const rows: MessageListRow[] = Array.from({ length: count }, (_, index) => ({
    id: String(index + 1), title: `Message ${index + 1}`, preview: "A held reply",
    created_at: "2026-10-01T00:00:00Z", channel: { ...{ id: "channel", display_name: "Discussion" } },
    revision: index + 3, permissions: ["read", "delete", "send_held"],
  }));
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    if (meta?.gqlQuery === ActionSelectionLimit) return { data: { action_selection_limit: 100 } };
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
  function Inbox() {
    view = useResourceView();
    return <MessagesPage />;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/messages", validateSearch: (search) => search,
    component: () => <ResourceViewProvider resource="messaging.Message" initialState={{
      filter: held ? { is_held: true } : {}, groupStack: [{ field: "channel" }],
    }}><Inbox /></ResourceViewProvider> });
  const record = createRoute({ getParentRoute: () => route, path: "$id", component: () => null });
  const router = createRouter({ routeTree: root.addChildren([route.addChildren([record])]), history: createMemoryHistory({ initialEntries: ["/messages"] }) });
  render(<Provider dataProvider={{ getList, custom }}><AppRuntimeProvider runtime={{}}>
    <OperationDocumentsProvider documents={{ console: { groups: { "messaging.Message":
      "query MessageGroups { messages_groups { key { channel_id channel_name } aggregate { count } } totalCount }",
    } } }}><ModalsHost><ToastProvider><RouterProvider router={router} /></ToastProvider></ModalsHost></OperationDocumentsProvider>
  </AppRuntimeProvider></Provider>);
  return { get view() { return view; }, rows, custom, getList, router,
    mutations: () => custom.mock.calls.filter(([request]) => request.meta?.gqlMutation) };
}

test("the ordinary grouped route retains selection and the shared delete action", async () => {
  fixture();
  await screen.findByText("Message 1");
  fireEvent.click(screen.getAllByRole("checkbox", { name: "Select row" })[0]!);
  expect(await screen.findByRole("button", { name: /Delete/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
});

test("route edits and browser back clear selection while preserving grouping", async () => {
  const f = fixture({ held: true });
  await screen.findByText("Message 1");
  act(() => f.view.toggleSelectedId("1", true));
  await screen.findByRole("button", { name: "Send now" });
  act(() => f.view.setFilter({ is_held: true, title: { iContains: "Message" } }));
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({}));
  await waitFor(() => expect(f.router.state.location.search).toHaveProperty("filter"));
  const editedFilter = f.view.state.filter;
  await act(async () => { await f.router.navigate({ to: "/messages", search: {} }); });
  await waitFor(() => expect(f.view.state.filter).toEqual({ is_held: { exact: true } }));
  act(() => f.view.toggleSelectedId("2", true));
  await act(async () => { f.router.history.back(); });
  await waitFor(() => expect(f.view.state.filter).toEqual(editedFilter));
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.view.state.groupStack).toEqual([{ field: "channel" }]);
});

test.each([false, true])("successful held actions discarding=%s clear selection and report a plural outcome", async (discarding) => {
  const f = fixture({ held: true });
  await screen.findByText("Message 1");
  act(() => f.view.setRowSelection({ "1": true, "2": true }));
  const name = discarding ? "Discard" : "Send now";
  const action = await screen.findByRole("button", { name });
  await waitFor(() => expect(action.hasAttribute("disabled")).toBe(false));
  fireEvent.click(action);
  fireEvent.click(action);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name }));
  await waitFor(() => expect(f.mutations()).toHaveLength(1));
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({}));
  expect(await screen.findByText(discarding ? "Discarded 2 held drafts." : "Queued 2 held drafts.")).toBeTruthy();
});

test.each([false, true])("a grouped held action discarding=%s sends selected revisions and drops refused ids for reselection", async (discarding) => {
  const f = fixture({ held: true, fail: true });
  await screen.findByText("Message 1");
  act(() => f.view.setRowSelection({ "1": true, "2": true }));
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
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({}));
  expect(await screen.findByText(`${discarding ? "Discarded one held draft." : "Queued one held draft."}; Revision changed`, { selector: "h2" })).toBeTruthy();
  // Publishing invalidates the loaded rows. Reselecting captures the on-screen revision.
  await waitFor(() => expect(f.getList.mock.calls.length).toBeGreaterThan(1));
  act(() => f.view.toggleSelectedId("2", true));
  const retry = await screen.findByRole("button", { name: discarding ? "Discard" : "Send now" });
  await waitFor(() => expect(retry.hasAttribute("disabled")).toBe(false));
  fireEvent.click(retry);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: discarding ? "Discard" : "Send now" }));
  await waitFor(() => expect(f.mutations()).toHaveLength(2));
  expect(f.mutations()[1]![0].meta?.gqlVariables).toEqual({ selection: [{ id: "2", expected_revision: 5 }] });
});

test("a transport refusal keeps selection and permits another attempt", async () => {
  const f = fixture({ held: true, transportFailure: true });
  await screen.findByText("Message 1");
  act(() => f.view.toggleSelectedId("1", true));
  const action = await screen.findByRole("button", { name: "Send now" });
  await waitFor(() => expect(action.hasAttribute("disabled")).toBe(false));
  fireEvent.click(action);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Send now" }));
  await screen.findByText("Publish unavailable", { selector: "h2" });
  expect(f.view.state.rowSelection).toEqual({ "1": true });
  await waitFor(() => expect(action.hasAttribute("disabled")).toBe(false));
});

test("both held verbs disable above the server's selection bound", async () => {
  const f = fixture({ held: true, count: 101 });
  await screen.findByText("Message 1");
  act(() => f.view.setRowSelection(Object.fromEntries(f.rows.map((row) => [row.id, true]))));
  expect((await screen.findByRole("button", { name: "Send now" })).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Discard" }).hasAttribute("disabled")).toBe(true);
  const reason = await screen.findByText("Select at most 100 held drafts.");
  expect(screen.getByRole("button", { name: "Send now" }).getAttribute("aria-describedby")).toBe(reason.id);
  expect(screen.getByRole("button", { name: "Discard" }).getAttribute("aria-describedby")).toBe(reason.id);
  expect(f.mutations()).toHaveLength(0);
});

test("an unavailable selected row explains both disabled verbs without a loaded-row policy projection", async () => {
  const f = fixture({ held: true });
  await screen.findByText("Message 1");
  act(() => f.view.toggleSelectedId("unloaded", true));
  const reason = await screen.findByText("Select readable held drafts with permission to send or discard them.");
  for (const name of ["Send now", "Discard"]) {
    const button = screen.getByRole("button", { name });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.getAttribute("aria-describedby")).toBe(reason.id);
  }
  expect(f.mutations()).toHaveLength(0);
});
