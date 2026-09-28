// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { AppRuntimeProvider, baseIcons, defaultWidgets, ModalsHost, ToastProvider } from "@angee/ui";
import { createRouteHref, routeSearchString } from "@angee/ui/runtime";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeAll, expect, test, vi } from "vitest";

import { InboxPage } from "./InboxPage";
import { decisionResourceFixture as resource, decisionSubjectFixture } from "./testing";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://decisions-inbox",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(() => { cleanup(); clearClients(); });

function fixture(initialEntry = "/", authenticated = true) {
  const getList = vi.fn(async (_params: GetListParams) => ({ data: [{
    id: "decision-1", kind: "review", record_model_label: "notes.Note", record_public_id: "note-1",
    requester: { display_name: "River" }, expires_at: null, verdict: "PENDING",
  }], total: 1 }));
  const root = createRootRoute({
    validateSearch: (search: Record<string, unknown>) => search,
    component: () => <AppRuntimeProvider runtime={{
      widgets: defaultWidgets, icons: baseIcons,
      auth: { user: authenticated ? { id: "user-1", name: "Sky" } : null,
        status: authenticated ? "authenticated" : "resolving", hasRole: () => false },
      routeHref: createRouteHref([{ name: "decisions.inbox.record", path: "/decisions/$id" }]),
    }}><ModalsHost><ToastProvider><InboxPage /></ToastProvider></ModalsHost></AppRuntimeProvider>,
  });
  const router = createRouter({
    routeTree: root, history: createMemoryHistory({ initialEntries: [initialEntry] }),
    parseSearch: (value) => Object.fromEntries(new URLSearchParams(value)),
    stringifySearch: (value) => { const query = routeSearchString(value); return query ? `?${query}` : ""; },
  });
  const getOne = vi.fn(async () => ({ data: { id: "note-1", display_name: "Review notes" } }));
  render(<Provider resources={[resource, decisionSubjectFixture]} dataProvider={{ getList, getOne }}><RouterProvider router={router} /></Provider>);
  return { getList, router };
}

test("queries only the current user's open seats and links the loaded decision", async () => {
  const { getList } = fixture();
  expect(await screen.findByText("River")).toBeTruthy();
  expect(getList.mock.calls[0]?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ assignees: { _eq: "user-1" } }, { is_open: { _eq: true } }],
  });
  expect(screen.getByRole("link", { name: /review/ }).getAttribute("href")).toBe("/decisions/decision-1");
  expect(await screen.findByText("Review notes")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /New Decision/ })).toBeNull();
});

test("scope and settled controls update the native query and preserve unrelated search", async () => {
  const { getList, router } = fixture("/?keep=external&page=3");
  await screen.findByText("River");
  fireEvent.click(screen.getByRole("combobox", { name: "Decisions" }));
  fireEvent.keyDown(await screen.findByRole("option", { name: "Requested by me" }), { key: "Enter" });
  await waitFor(() => expect(router.state.location.search).toMatchObject({ decisionScope: "requested", keep: "external" }));
  expect(router.state.location.search).not.toHaveProperty("page");
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toMatchObject({
    _and: [{ is_open: { _eq: true } }, { requester: { _eq: "user-1" } }],
  }));
  fireEvent.click(screen.getByRole("combobox", { name: "Decision state" }));
  fireEvent.keyDown(await screen.findByRole("option", { name: "Settled" }), { key: "Enter" });
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ is_open: { _eq: false } }, { requester: { _eq: "user-1" } }],
  }));
});

test("restores issued settled decisions from the URL", async () => {
  const { getList } = fixture("/?decisionScope=requested&decisionState=settled");
  await screen.findByText("River");
  expect(getList.mock.calls[0]?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ is_open: { _eq: false } }, { requester: { _eq: "user-1" } }],
  });
});

test("does not issue an unscoped read while the current user resolves", async () => {
  const { getList } = fixture("/", false);
  expect(await screen.findByRole("status")).toBeTruthy();
  expect(getList).not.toHaveBeenCalled();
});
