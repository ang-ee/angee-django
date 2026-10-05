// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { AppRuntimeProvider, baseIcons, defaultWidgets, ModalsHost, ToastProvider } from "@angee/ui";
import { createRouteHref, routeSearchString } from "@angee/ui/runtime";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeAll, expect, test, vi } from "vitest";

import { InboxPage } from "./InboxPage";
import decisions from "./index";
import { decisionResourceFixture as resource, decisionRecordFixture } from "./testing";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://decisions-inbox",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
beforeAll(() => { Element.prototype.getAnimations ??= () => []; });
afterEach(() => { cleanup(); clearClients(); });

function fixture(initialEntry = "/decisions", authenticated = true) {
  const getList = vi.fn(async (_params: GetListParams) => ({ data: [{
    id: "decision-1", kind: "review", kind_label: "Review",
    requester: { display_name: "River" }, verdict: null,
  }], total: 1 }));
  const root = createRootRoute({
    validateSearch: (search: Record<string, unknown>) => search,
    component: () => <AppRuntimeProvider runtime={{
      widgets: defaultWidgets, icons: baseIcons,
      resourceViews: Object.fromEntries((decisions.resourceViews ?? []).map((view) => [view.id, view])),
      defaultResourceView: "decisions.inbox",
      menuResourceViewIds: decisions.resourceViews?.map((view) => view.id),
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
  render(<Provider resources={[resource, decisionRecordFixture]} dataProvider={{ getList, getOne }}><RouterProvider router={router} /></Provider>);
  return { getList, router };
}

test("queries the open inbox and links the loaded decision", async () => {
  const { getList } = fixture();
  expect(await screen.findByText("River")).toBeTruthy();
  expect(getList.mock.calls[0]?.[0].meta?.gqlVariables?.where).toEqual({
    is_open: { _eq: true },
  });
  expect(screen.getByRole("link", { name: "Open Review" }).getAttribute("href")).toMatch(/^\/decisions\/decision-1\?recordNav=/);
  expect(screen.queryByRole("button", { name: /New Decision/ })).toBeNull();
});

test("the native filter box edits personal predicates and preserves unrelated search", async () => {
  const { getList, router } = fixture("/decisions?preset=decisions.waiting&keep=external&page=3");
  await screen.findByText("River");
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(await screen.findByRole("button", { name: "I can answer" }));
  fireEvent.click(await screen.findByRole("button", { name: "Requested by me" }));
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ is_open: { _eq: true } }, { requester: { _eq: "user-1" } }],
  }));
  fireEvent.click(screen.getByRole("button", { name: "Answered" }));
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ is_open: { _eq: false } }, { requester: { _eq: "user-1" } }],
  }));
  expect(router.state.location.search).toMatchObject({ keep: "external" });
  expect(screen.queryByRole("combobox", { name: "Decisions" })).toBeNull();
});

test("finds assigned questions through the server authority filter", async () => {
  const { getList } = fixture();
  await screen.findByText("River");
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(await screen.findByRole("button", { name: "I can answer" }));
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({
    _and: [{ can_act: { _eq: true } }, { is_open: { _eq: true } }],
  }));
});

test("Assigned to me filters assignees independently of the Waiting on me authority preset", async () => {
  const { getList } = fixture("/decisions?preset=decisions.all");
  await screen.findByText("River");
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(await screen.findByRole("button", { name: "Assigned to me" }));
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({
    assignees: { _eq: "user-1" },
  }));
});

test.each([
  ["decisions.inbox", { is_open: { _eq: true } }],
  ["decisions.waiting", { _and: [{ can_act: { _eq: true } }, { is_open: { _eq: true } }] }],
  ["decisions.all", {}],
])("menu preset %s drives the native query", async (preset, where) => {
  const { getList } = fixture(`/decisions?preset=${preset}`);
  await screen.findByText("River");
  expect(getList.mock.calls[0]?.[0].meta?.gqlVariables?.where).toEqual(where);
});

test("does not issue an unscoped read while the current user resolves", async () => {
  const { getList } = fixture("/decisions", false);
  expect(await screen.findByRole("status")).toBeTruthy();
  expect(getList).not.toHaveBeenCalled();
});
