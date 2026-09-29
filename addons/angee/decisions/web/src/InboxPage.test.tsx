// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { AppRuntimeProvider, baseIcons, defaultWidgets, ModalsHost, ToastProvider } from "@angee/ui";
import { createRouteHref, routeSearchString } from "@angee/ui/runtime";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeAll, expect, test, vi } from "vitest";

import { InboxPage } from "./InboxPage";
import { decisionGroupFixture, decisionResourceFixture as resource, decisionSubjectFixture } from "./testing";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://decisions-inbox",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(() => { cleanup(); clearClients(); });

function fixture(initialEntry = "/decisions", authenticated = true) {
  const getList = vi.fn(async (_params: GetListParams) => ({ data: [{
    id: "decision-1", kind: "review", kind_label: "Review", record_model_label: "notes.Note", record_public_id: "note-1",
    requester: { display_name: "River" }, expires_at: null, verdict: "PENDING",
  }], total: 1 }));
  const root = createRootRoute({
    validateSearch: (search: Record<string, unknown>) => search,
    component: () => <AppRuntimeProvider runtime={{
      widgets: defaultWidgets, icons: baseIcons,
      auth: { user: authenticated ? { id: "user-1", name: "Sky" } : null,
        status: authenticated ? "authenticated" : "resolving", hasRole: () => false },
      routeHref: createRouteHref([{ name: "decisions.inbox.record", path: "/decisions/$id" }]),
    }}><ModalsHost><ToastProvider><Outlet /></ToastProvider></ModalsHost></AppRuntimeProvider>,
  });
  const collection = createRoute({ getParentRoute: () => root, path: "/decisions", component: InboxPage });
  const record = createRoute({ getParentRoute: () => collection, path: "$id" });
  const router = createRouter({
    routeTree: root.addChildren([collection.addChildren([record])]), history: createMemoryHistory({ initialEntries: [initialEntry] }),
    parseSearch: (value) => Object.fromEntries(new URLSearchParams(value)),
    stringifySearch: (value) => { const query = routeSearchString(value); return query ? `?${query}` : ""; },
  });
  const getOne = vi.fn(async () => ({ data: { id: "note-1", display_name: "Review notes" } }));
  render(<Provider resources={[resource, decisionGroupFixture, decisionSubjectFixture]} dataProvider={{ getList, getOne }}><RouterProvider router={router} /></Provider>);
  return { getList, router };
}

test("queries only the current user's open seats and links the loaded decision", async () => {
  const { getList } = fixture();
  expect(await screen.findByText("River")).toBeTruthy();
  expect(getList.mock.calls[0]?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ assignees: { _eq: "user-1" } }, { is_open: { _eq: true } }],
  });
  expect(screen.getByRole("link", { name: "Open Review" }).getAttribute("href")).toMatch(/^\/decisions\/decision-1\?recordNav=/);
  expect(await screen.findByText("Review notes")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /New Decision/ })).toBeNull();
});

test("the native filter box edits personal predicates and preserves unrelated search", async () => {
  const { getList, router } = fixture("/decisions?keep=external&page=3");
  await screen.findByText("River");
  fireEvent.click(screen.getByRole("button", { name: "Remove Assigned to me" }));
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({ is_open: { _eq: true } }));
  fireEvent.click(screen.getByRole("button", { name: "Filter and group" }));
  fireEvent.click(await screen.findByRole("button", { name: "Requested by me" }));
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ is_open: { _eq: true } }, { requester: { _eq: "user-1" } }],
  }));
  fireEvent.click(screen.getByRole("button", { name: "Settled" }));
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ is_open: { _eq: false } }, { requester: { _eq: "user-1" } }],
  }));
  expect(router.state.location.search).toMatchObject({ keep: "external" });
  expect(screen.queryByRole("combobox", { name: "Decisions" })).toBeNull();
});

test("does not issue an unscoped read while the current user resolves", async () => {
  const { getList } = fixture("/decisions", false);
  expect(await screen.findByRole("status")).toBeTruthy();
  expect(getList).not.toHaveBeenCalled();
});
