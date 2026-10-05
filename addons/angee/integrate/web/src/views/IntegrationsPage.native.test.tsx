// @vitest-environment happy-dom
import { createApp, resourcePageRoutes } from "@angee/app";
import { TEST_SCHEMAS } from "@angee/app/testing";
import { testDataResource } from "@angee/metadata/testing";
import { RecordReference, useResourceRoute } from "@angee/ui";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const read = vi.hoisted(() => ({
  target: { state: "AVAILABLE", resource: "example.RecordBridge", id: "bridge-1" } as {
    state: string; resource: string | null; id: string | null;
  },
  fetching: false,
  error: null as Error | null,
  missing: false,
  query: vi.fn(),
}));

// This workspace has no generated schema. Replace only its document boundary
// and unrelated credential form; routing, metadata and record links stay real.
vi.mock("../documents", () => ({
  IntegrationSyncStream: {},
  IntegrationRecordRedirectDocument: {},
}));
vi.mock("../connect/credential-form", () => ({ credentialCreateForm: undefined }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (...args: unknown[]) => {
    read.query(...args);
    return {
      data: read.fetching ? undefined : { integrations_by_pk: read.missing ? null : { id: "integration-1", concrete_target: read.target } },
      isFetching: read.fetching,
      error: read.error,
    };
  },
}));
vi.mock("./IntegrationsPage", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./IntegrationsPage")>()),
  // Inventory mechanics are outside this routing regression.
  IntegrationsPage: () => <div>Integration inventory</div>,
}));

import integrate from "../index";
import { concreteIntegrationHref } from "./IntegrationsPage";

const mounts: { root: Root; host: HTMLElement }[] = [];
beforeEach(() => {
  read.target = { state: "AVAILABLE", resource: "example.RecordBridge", id: "bridge-1" };
  read.fetching = false;
  read.error = null;
  read.missing = false;
  read.query.mockClear();
});
afterEach(() => {
  mounts.splice(0).forEach(({ root, host }) => { root.unmount(); host.remove(); });
});

function Links() {
  const collection = useResourceRoute("integrate.Integration");
  return <>
    <RecordReference model="integrate.Integration" id="integration-1" label="Integration subject" />
    <RecordReference model="notes.Note" id="note-1" label="Note subject" />
    <a href={collection}>Integrations</a>
  </>;
}

function mountApp(childRouted = true) {
  const resources = [
    "integrate.Integration", "integrate.Vendor", "integrate.WebhookSubscription", "integrate.OAuthClient",
    "integrate.ExternalAccount", "integrate.Credential", "example.RecordBridge", "notes.Note",
  ].map((model) => testDataResource(model));
  history.replaceState(null, "", "/start");
  const app = createApp({
    addons: [integrate, {
      id: "example",
      routes: [
        { name: "example.start", path: "/start", component: Links },
        ...resourcePageRoutes("example.notes", "/notes", () => <div>Note page</div>, "notes.Note"),
        ...(childRouted ? resourcePageRoutes("example.bridges", "/integrate/record-bridges", () => <div>Concrete integration page</div>, "example.RecordBridge") : []),
      ],
    }],
    layouts: { console: { requireAuth: false } },
    schemas: { ...TEST_SCHEMAS, console: { ...TEST_SCHEMAS.console, metadata: { angee: { resources } } } },
    defaultSchema: "console",
    home: "/start",
  });
  const host = document.createElement("div");
  document.body.append(host);
  mounts.push({ root: app.mount(host), host });
  return app;
}

test("a generic base-model integration link opens its concrete record and Back returns to the caller", async () => {
  const app = mountApp();
  const link = await screen.findByRole("link", { name: "Integration subject" });
  expect(link.getAttribute("href")).toBe("/integrate/integration-1");
  fireEvent.click(link);
  await waitFor(() => expect(app.router.state.location.pathname).toBe("/integrate/record-bridges/bridge-1"));
  expect(screen.getByText("Concrete integration page")).toBeTruthy();
  expect(read.query).toHaveBeenCalledWith(expect.anything(), { id: "integration-1" }, {
    models: ["integrate.Integration"], enabled: true,
  });
  app.router.history.back();
  await screen.findByRole("link", { name: "Integration subject" });
  expect(app.router.state.location.pathname).toBe("/start");
});

test("the resolver preserves search and breadcrumb history", async () => {
  const app = mountApp();
  await screen.findByRole("link", { name: "Integration subject" });
  const breadcrumbTrail = [{ label: "Run", href: "/start" }];
  await app.router.navigate({ href: "/integrate/integration-1?recordTab=streams", state: { breadcrumbTrail } });
  await waitFor(() => expect(app.router.state.location.pathname).toBe("/integrate/record-bridges/bridge-1"));
  expect(app.router.state.location.search).toEqual({ recordTab: "streams" });
  expect(app.router.state.location.state.breadcrumbTrail).toEqual(breadcrumbTrail);
});

test("collection links, concrete inventory row links, and other models keep their destinations", async () => {
  const app = mountApp();
  expect((await screen.findByRole("link", { name: "Integrations" })).getAttribute("href")).toBe("/integrate");
  expect(concreteIntegrationHref({ id: "integration-1", concrete_target: read.target }, (model, id) =>
    model === "example.RecordBridge" ? `/integrate/record-bridges/${id}` : undefined)).toBe("/integrate/record-bridges/bridge-1");
  fireEvent.click(screen.getByRole("link", { name: "Note subject" }));
  await waitFor(() => expect(app.router.state.location.pathname).toBe("/notes/note-1"));
  await app.router.navigate({ href: "/integrate" });
  expect(await screen.findByText("Integration inventory")).toBeTruthy();
  expect(read.query).not.toHaveBeenCalled();
});

test.each(["UNAVAILABLE", "AMBIGUOUS"])("a %s child target stays unavailable", async (state) => {
  read.target.state = state;
  const app = mountApp();
  fireEvent.click(await screen.findByRole("link", { name: "Integration subject" }));
  expect(await screen.findByRole("heading", { name: "Integration unavailable" })).toBeTruthy();
  expect(app.router.state.location.pathname).toBe("/integrate/integration-1");
  expect(concreteIntegrationHref({ id: "integration-1", concrete_target: read.target }, () => "/unexpected")).toBe("");
});

test.each(["missing record", "unrouted child"])("a %s offers no concrete page", async (reason) => {
  read.missing = reason === "missing record";
  const app = mountApp(reason !== "unrouted child");
  fireEvent.click(await screen.findByRole("link", { name: "Integration subject" }));
  expect(await screen.findByRole("heading", { name: "Integration unavailable" })).toBeTruthy();
  expect(app.router.state.location.pathname).toBe("/integrate/integration-1");
});

test("the parent read shows pending state before a target is known", async () => {
  read.fetching = true;
  const app = mountApp();
  fireEvent.click(await screen.findByRole("link", { name: "Integration subject" }));
  expect((await screen.findByRole("status")).textContent).toContain("Opening integration…");
  expect(screen.queryByRole("heading", { name: "Integration unavailable" })).toBeNull();
  expect(app.router.state.location.pathname).toBe("/integrate/integration-1");
});

test("a failed parent read shows the shared error surface without following a cached target", async () => {
  read.error = new Error("Could not read integration");
  const app = mountApp();
  fireEvent.click(await screen.findByRole("link", { name: "Integration subject" }));
  expect(await screen.findByText("Could not read integration")).toBeTruthy();
  expect(app.router.state.location.pathname).toBe("/integrate/integration-1");
});
