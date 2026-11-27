// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/metadata/testing";
import { refineFieldsFromPaths } from "@angee/refine";
import { CONNECT_RECORD_FIELDS } from "@angee/integrate";
import { containerChildren } from "@angee/app/testing";
import { AppRuntimeProvider, FORM_CONTAINERS, ModalsHost, ToastProvider, containersFromChildren, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { feedForm } from "./FeedForm";
import posts from "./index";

const names = ["id", "display_name", "backend_class", "feed_backend_class", "external_id", "handle", "lifecycle", "runtime_status",
  "reply_hold", "config", "sync_stage", "last_sync_completed_at", "last_sync_items", "sync_progress", "sync_error",
  ...CONNECT_RECORD_FIELDS];
// The feed's two implementation selectors as the server's metadata states them: create-only, and on the
// update input, so the model's save decides (a repair only while a stored key is not registered).
const selectors = [
  { name: "backend_class", kind: "enum" as const, widget: "select", readable: true, creatable: false, updatable: true,
    createOnly: true, aggregatable: false, requiredOnCreate: false, values: [
      { value: "EMAIL", description: "Email" }, { value: "FEED", description: "Feed" }, { value: "IMAP", description: "IMAP" },
      { value: "MANUAL", description: "Manual" }, { value: "SLACK", description: "Slack" },
      { value: "WEBFORM", description: "Public Webform" },
    ] },
  { name: "feed_backend_class", kind: "enum" as const, widget: "select", readable: true, creatable: true, updatable: true,
    createOnly: true, aggregatable: false, requiredOnCreate: false, values: [{ value: "MANUAL", description: "Manual" }] },
];
const resource = testDataResource("posts.Feed", {
  implFields: ["backend_class", "feed_backend_class"],
  fields: [...selectors, ...[...new Set(names)].filter((name) => !name.endsWith("backend_class")).map((name) => ({
    name, kind: "scalar" as const, scalar: name === "reply_hold" ? "Float"
      : name === "config" || name === "sync_progress" ? "JSON" : "String",
    readable: true, creatable: name === "display_name",
    updatable: name === "reply_hold", nullable: name === "reply_hold", aggregatable: false, requiredOnCreate: false }))],
});
const { Provider, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://feeds",
  queryClientConfig: { defaultOptions: { queries: { retry: false } } } });
afterEach(() => { cleanup(); clearClients(); });

function fixture({ replyHold = null, connectable = false, create = false, retired = false }: {
  replyHold?: number | null; connectable?: boolean; create?: boolean; retired?: boolean;
} = {}) {
  const row = {
    id: "feed", display_name: "Public discussion", backend_class: "FEED", feed_backend_class: retired ? null : "MANUAL",
    reply_hold: replyHold,
    external_id: "account", config: {}, sync_progress: {}, credential_status: "valid",
    can_connect: connectable, is_reconnect_required: false, discovery_ready: true, discovery_pending: false,
    permissions: ["read", "write"],
  };
  const getOne = vi.fn(async (request: { meta?: Record<string, unknown> }) => {
    for (const field of request.meta?.fields as ReturnType<typeof refineFieldsFromPaths>) {
      if (typeof field !== "string" || !names.includes(field)) throw new Error(`Unknown feed selection: ${JSON.stringify(field)}`);
    }
    return { data: row };
  });
  const custom = vi.fn(async (request: { meta?: Record<string, unknown> }) => {
    const variables = (request.meta?.gqlVariables ?? {}) as Record<string, unknown>;
    // The repair read: what choosing each feed implementation sets on this feed, the config it carries over.
    if (variables.record != null) return { data: { impl_choices: [
      { key: "manual", category: "", defaults: { config: { quota_limit: 10000 } }, config_schema: null },
    ] } };
    return { data: { connect_integration: {
      attached: true, authorize_url: "", error: null, mode: "redirect", state: "", redirect_uri: "",
    } } };
  });
  const update = vi.fn(async ({ variables }: { variables: Record<string, unknown> }) => ({ data: { ...row, ...variables } }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/feeds/feed"] }) });
  render(<Provider dataProvider={{ getOne, custom, update }}><RouterContextProvider router={router}>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets,
      containers: containersFromChildren(FORM_CONTAINERS, Object.fromEntries(Object.keys(posts.containers ?? {})
        .map((address) => [address, Object.fromEntries(containerChildren(posts, address))]))),
    }}><ModalsHost><ToastProvider>
      <feedForm.Component resource="posts.Feed" {...(create ? {} : { id: "feed" })} />
    </ToastProvider></ModalsHost></AppRuntimeProvider>
  </RouterContextProvider></Provider>);
  return { getOne, custom, update };
}

test("discovery's name is read-only and connection fields come from the action contribution", async () => {
  const f = fixture();
  expect(await screen.findByRole("heading", { name: "Public discussion" })).toBeTruthy();
  expect(screen.queryByDisplayValue("Public discussion")).toBeNull();
  const selected = f.getOne.mock.calls[0]?.[0]?.meta?.fields;
  expect(selected).toEqual(expect.arrayContaining(refineFieldsFromPaths(CONNECT_RECORD_FIELDS)));
  expect(selected).not.toEqual(expect.arrayContaining([expect.objectContaining({ credential: expect.anything() })]));
  expect(screen.getByText("Leave blank for approval before sending; zero sends immediately.")).toBeTruthy();
});

test.each([null, 0])("reply_hold=%s retains its distinct editable value", async (replyHold) => {
  const f = fixture({ replyHold });
  await screen.findByRole("heading", { name: "Public discussion" });
  const input = screen.getByLabelText("Reply hold (hours)") as HTMLInputElement;
  expect(input.value).toBe(replyHold === null ? "" : "0");
  fireEvent.change(input, { target: { value: replyHold === null ? "0" : "" } });
  fireEvent.blur(input);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect(f.update.mock.calls[0]?.[0]).toEqual(expect.objectContaining({
    resource: "feeds", id: "feed", variables: { reply_hold: replyHold === null ? 0 : null },
  }));
});

test("the feed's backend is offered on create and locked on a stored feed whose keys are registered", async () => {
  fixture({ create: true });
  expect(await screen.findByRole("combobox", { name: "Backend" })).toBeTruthy();
  cleanup();
  clearClients();

  const stored = fixture();
  await screen.findByRole("heading", { name: "Public discussion" });
  expect(screen.queryByRole("combobox", { name: "Backend" })).toBeNull();
  // Registered keys offer no repair, so neither selector's record read is ever made.
  const asked = stored.custom.mock.calls.map(([request]) => (request?.meta?.gqlVariables ?? {}) as Record<string, unknown>);
  expect(asked.filter((variables) => variables.record != null)).toEqual([]);
});

test("a feed whose own implementation is retired is repaired by its selector alone: the feeds update writes no config", async () => {
  const f = fixture({ retired: true });
  const control = await screen.findByRole("combobox", { name: "Backend" });
  await waitFor(() => expect(control.getAttribute("aria-busy")).not.toBe("true"));
  fireEvent.click(control);
  const option = await screen.findByRole("option", { name: "Manual" });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.update).toHaveBeenCalledOnce());
  expect(f.update.mock.calls[0]?.[0]).toEqual(expect.objectContaining({ variables: { feed_backend_class: "MANUAL" } }));
});

test("a dirty feed keeps Connect visible but disabled", async () => {
  const f = fixture({ connectable: true });
  const button = await screen.findByRole("button", { name: "Connect" });
  expect(button.hasAttribute("disabled")).toBe(false);
  fireEvent.change(screen.getByLabelText("Reply hold (hours)"), { target: { value: "0" } });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(true));
  fireEvent.click(button);
  expect(f.custom).not.toHaveBeenCalled();
});

test("Connect returns to the current record page with the saved feed identity", async () => {
  const f = fixture({ connectable: true });
  fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  expect(f.custom.mock.calls[0]?.[0]).toEqual(expect.objectContaining({ meta: expect.objectContaining({ gqlVariables: {
    resource: "posts.Feed", id: "feed", redirectUri: `${window.location.origin}/integrate/oauth/callback`, next: "/feeds/feed",
  } }) }));
  expect(await screen.findByText("Connection saved", { selector: "h2" })).toBeTruthy();
});
