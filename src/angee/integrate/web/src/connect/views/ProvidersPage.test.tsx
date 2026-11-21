// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

// The page's own form renders standalone for one saved provider; only the collection
// page that stitches it in, and the connect flow's transports, are replaced.
vi.mock("@angee/ui", async (original) => {
  const ui = await original<typeof import("@angee/ui")>();
  return {
    ...ui,
    ResourceList: ({ children }: { children?: React.ReactNode }) => <>{React.Children.toArray(children)
      .filter((child): child is React.ReactElement<{ id?: string }> => React.isValidElement(child) && child.type === ui.Form)
      .map((form) => React.cloneElement(form, { id: "oac_1" }))}</>,
    useRecordActionMutation: () => [vi.fn(), { fetching: false, error: null }],
    useRouteHref: () => () => "/integrate/accounts",
  };
});
vi.mock("../ConnectAccountButton", () => ({ useConnectAccountStart: () => vi.fn() }));
vi.mock("../use-oauth-authorization", () => ({ useOAuthAuthorization: () => ({ authorize: vi.fn() }) }));

import { ProvidersPage } from "./ProvidersPage";

const MODEL = "integrate.OAuthClient";
const field = {
  kind: "scalar", scalar: "String", readable: true, aggregatable: false,
  creatable: true, updatable: true, requiredOnCreate: false,
} as const;
const resources = [testDataResource(MODEL, {
  modelName: "oauth_client",
  roots: { list: "oauth_clients", detail: "oauth_clients_by_pk" },
  fields: ["display_name", "slug", "discovery_url", "configuration_state"].map((name) => ({ ...field, name })),
})];
const { Provider, clearClients } = createUiTestProviders({ resources, apiUrl: "test://oauth-providers" });
afterEach(() => { cleanup(); clearClients(); });

type Selection = readonly (string | Record<string, Selection>)[];

/** Answer only what the read selects, as the server does. */
function selected(row: Record<string, unknown>, fields: Selection): Record<string, unknown> {
  return Object.fromEntries(fields.flatMap((entry) => typeof entry === "string"
    ? [[entry, row[entry]]]
    : Object.entries(entry).map(([name, nested]) => [name, selected(row[name] as Record<string, unknown>, nested)])));
}

function renderProvider(configurationState: string, discoveryUrl = ""): void {
  const provider = {
    id: "oac_1", display_name: "Google Gemini", slug: "gemini", discovery_url: discoveryUrl,
    configuration_state: configurationState, permissions: ["write"],
  };
  const getOne = vi.fn(async ({ meta }: { meta?: { fields?: unknown } }) => ({
    data: selected(provider, (meta?.fields ?? ["id"]) as Selection),
  }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Provider dataProvider={{ getOne }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ProvidersPage /></AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
}

describe("ProvidersPage", () => {
  test("a ready provider offers Connect account", async () => {
    renderProvider("ready");

    expect(await screen.findByRole("button", { name: "Connect account" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Connect account" })).toBeNull();
  });

  test("discovery stays in the menu beside a ready provider's Connect account", async () => {
    renderProvider("ready", "https://provider.example/.well-known/openid-configuration");
    expect(await screen.findByRole("button", { name: "Connect account" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Discover endpoints" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Discover endpoints" })).toBeNull();
  });

  test("a provider that still needs its client offers no connect", async () => {
    renderProvider("needs_client");

    expect(await screen.findByDisplayValue("gemini")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Connect account" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Connect account", hidden: true })).toBeNull();
  });
});
