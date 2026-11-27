// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, FORM_CONTAINERS, ModalsHost, ToastProvider, containersFromChildren, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { channelForm } from "./ChannelForm";
import { CHANNEL_MODEL } from "./documents";

const names = ["id", "display_name", "lifecycle", "credential_status", "runtime_status", "config",
  "sync_stage", "last_sync_completed_at", "last_sync_items", "sync_progress", "sync_error"];
// The channel's selector as the server's metadata states it: chosen by connecting, never on the create
// input; on the update input and create-only, so the model's save decides.
const backend = { name: "backend_class", kind: "enum" as const, widget: "select", readable: true, creatable: false,
  updatable: true, createOnly: true, aggregatable: false, requiredOnCreate: false,
  values: [{ value: "IMAP", description: "IMAP" }, { value: "MANUAL", description: "Manual" }] };
const resource = testDataResource(CHANNEL_MODEL, {
  implFields: ["backend_class"],
  fields: [backend, ...names.map((name) => ({
    name, kind: "scalar" as const, scalar: name === "config" || name === "sync_progress" ? "JSON" : "String",
    readable: true, creatable: name === "display_name", updatable: name === "display_name",
    aggregatable: false, requiredOnCreate: false }))],
});
const { Provider, clearClients } = createUiTestProviders({ resources: [resource], apiUrl: "test://channels",
  queryClientConfig: { defaultOptions: { queries: { retry: false } } } });
afterEach(() => { cleanup(); clearClients(); });

test("a channel whose implementation is retired says so; its form shows the selector read-only and never asks for a repair", async () => {
  const row = { id: "channel", display_name: "Support inbox", backend_class: null, config: {}, sync_progress: {},
    permissions: ["read", "write"] };
  const getOne = vi.fn(async () => ({ data: row }));
  const custom = vi.fn(async () => ({ data: { impl_choices: [] } }));
  const update = vi.fn(async ({ variables }: { variables: Record<string, unknown> }) => ({ data: { ...row, ...variables } }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/channels/channel"] }) });
  render(<Provider dataProvider={{ getOne, custom, update }}><RouterContextProvider router={router}>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers: containersFromChildren(FORM_CONTAINERS, {}) }}>
      <ModalsHost><ToastProvider><channelForm.Component resource={CHANNEL_MODEL} id="channel" /></ToastProvider></ModalsHost>
    </AppRuntimeProvider>
  </RouterContextProvider></Provider>);

  expect(await screen.findByLabelText("Not registered: Backend Class")).toBeTruthy();
  // The form declares the selector read-only: the record's other field saves, and the selector is never sent.
  expect(screen.getByText("Its implementation cannot run. It cannot be changed here.")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Backend Class" })).toBeNull();
  expect(custom).not.toHaveBeenCalled();
  const name = await screen.findByDisplayValue("Support inbox");
  fireEvent.change(name, { target: { value: "Support" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  expect(update.mock.calls[0]?.[0]).toEqual(expect.objectContaining({ variables: { display_name: "Support" } }));
});
