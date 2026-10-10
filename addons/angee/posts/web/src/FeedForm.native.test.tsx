// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/metadata/testing";
import { refineFieldsFromPaths } from "@angee/refine";
import { CONNECT_RECORD_FIELDS } from "@angee/integrate";
import { AppRuntimeProvider, FORM_CONTAINERS, ModalsHost, ToastProvider, containersFromChildren, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { feedForm } from "./FeedForm";
import posts from "./index";

const names = ["id", "display_name", "feed_backend_class", "external_id", "handle", "lifecycle", "runtime_status",
  "reply_hold", "config", "sync_stage", "last_sync_completed_at", "last_sync_items", "sync_progress", "sync_error",
  ...CONNECT_RECORD_FIELDS];
const resource = testDataResource("posts.Feed", {
  fields: [...new Set(names)].map((name) => ({ name, kind: "scalar", scalar: name === "reply_hold" ? "Float"
    : name === "config" || name === "sync_progress" ? "JSON" : "String",
    readable: true, creatable: name === "display_name" || name === "feed_backend_class",
    updatable: name === "reply_hold", nullable: name === "reply_hold", aggregatable: false, requiredOnCreate: false })),
});
const { Provider, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://feeds",
  queryClientConfig: { defaultOptions: { queries: { retry: false } } } });
afterEach(() => { cleanup(); clearClients(); });

function fixture({ replyHold = null, connectable = false }: { replyHold?: number | null; connectable?: boolean } = {}) {
  const row = {
    id: "feed", display_name: "Public discussion", feed_backend_class: "manual", reply_hold: replyHold,
    external_id: "account", config: {}, sync_progress: {}, credential_status: "valid",
    can_connect: connectable, is_reconnect_required: false, binding_ready: true, binding_pending: false,
    permissions: ["read", "write"],
  };
  const getOne = vi.fn(async (request: { meta?: Record<string, unknown> }) => {
    for (const field of request.meta?.fields as ReturnType<typeof refineFieldsFromPaths>) {
      if (typeof field !== "string" || !names.includes(field)) throw new Error(`Unknown feed selection: ${JSON.stringify(field)}`);
    }
    return { data: row };
  });
  const custom = vi.fn(async (_request: { meta?: Record<string, unknown> }) => ({ data: { connect_integration: {
    attached: true, authorize_url: "", error: null, mode: "redirect", state: "", redirect_uri: "",
  } } }));
  const update = vi.fn(async ({ variables }: { variables: Record<string, unknown> }) => ({ data: { ...row, ...variables } }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Provider dataProvider={{ getOne, custom, update }}><RouterContextProvider router={router}>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets,
      containers: containersFromChildren(FORM_CONTAINERS, posts.containers ?? {}),
      routeHref: () => "/resolved/feeds",
    }}><ModalsHost><ToastProvider>
      <feedForm.Component resource="posts.Feed" id="feed" />
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

test("a dirty feed keeps Connect visible but disabled", async () => {
  const f = fixture({ connectable: true });
  const button = await screen.findByRole("button", { name: "Connect" });
  expect(button.hasAttribute("disabled")).toBe(false);
  fireEvent.change(screen.getByLabelText("Reply hold (hours)"), { target: { value: "0" } });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(true));
  fireEvent.click(button);
  expect(f.custom).not.toHaveBeenCalled();
});

test("Connect uses the route owner's href and the saved feed identity", async () => {
  const f = fixture({ connectable: true });
  fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
  await waitFor(() => expect(f.custom).toHaveBeenCalledOnce());
  expect(f.custom.mock.calls[0]?.[0]).toEqual(expect.objectContaining({ meta: expect.objectContaining({ gqlVariables: {
    resource: "posts.Feed", id: "feed", redirectUri: `${window.location.origin}/integrate/oauth/callback`, next: "/resolved/feeds",
  } }) }));
  expect(await screen.findByText("Connection saved", { selector: "h2" })).toBeTruthy();
});
