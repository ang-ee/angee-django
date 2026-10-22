// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { afterEach, expect, test } from "vitest";
import { AppRuntimeProvider } from "../../../runtime";
import { RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY } from "../resource-view-favorites";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue, type ResourceViewProviderScope } from "../resource-view-context";
import { SearchBox } from "./SearchBox";
import { searchFixture } from "./search-fixture.test-support";
import type { ResourceSearch } from "./types";
import { useResourceSearch } from "./use-resource-search";

afterEach(cleanup);
const favorite = { id: "by-company", label: "Bills by company", groupStack: [{ field: "company" }] };
const baseFilter = { kind: { exact: "VENDOR_BILL" } };
const defaults = { groupStack: [{ field: "status" }], filter: { title: { iContains: "baseline" } } };

function fixture(scope: ResourceViewProviderScope, entry = "/?keep=external&company=A") {
  let view!: ResourceViewContextValue;
  let search!: ResourceSearch;
  function Probe() {
    view = useResourceView();
    const catalog = searchFixture({ catalog: { favorites: view.savedFavorites,
      groups: [{ id: "company", label: "Company", group: { field: "company" } }] } }).catalog;
    search = useResourceSearch({ resourceView: view, catalog, groupingEnabled: true });
    return <SearchBox search={search} />;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", validateSearch: (input) => input,
    component: () => <ResourceViewProvider scope={scope} favoriteKey="bills" initialState={defaults} baseFilter={baseFilter}><Probe /></ResourceViewProvider> });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: [entry] }) });
  const rendered = render(<AppRuntimeProvider runtime={{ userPreferences: { available: true,
    preferences: { [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: { version: 1, models: { "collection:bills": [favorite] } } },
    patchPreferences: async () => undefined } }}><RouterProvider router={router} /></AppRuntimeProvider>);
  return { get view() { return view; }, get search() { return search; }, router, ...rendered };
}

test.each(["local", "route"] as const)("group-only favorite clears from the %s search and stays cleared after remount", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view?.state.groupStack).toEqual(defaults.groupStack));
  expect(screen.queryByRole("button", { name: "Remove Bills by company" })).toBeNull();
  act(() => f.search.applyFavorite(favorite.id));
  await waitFor(() => expect(f.view.state.groupStack).toEqual(favorite.groupStack));
  expect(await screen.findByRole("button", { name: "Remove Bills by company" })).toBeTruthy();
  act(() => f.view.setPage(3));
  await waitFor(() => expect(f.view.state.pagination.pageIndex).toBe(2));
  expect(screen.getByRole("button", { name: "Remove Bills by company" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Remove Bills by company" }));
  await waitFor(() => expect(f.view.state.groupStack).toEqual([]));
  expect(screen.queryByRole("button", { name: "Remove Bills by company" })).toBeNull();
  expect(f.view.baseFilter).toEqual(baseFilter);
  expect(f.router.state.location.search).toMatchObject({ keep: "external", company: "A" });
  const href = f.router.state.location.href;
  f.unmount();
  const restored = fixture(scope, href);
  await waitFor(() => expect(restored.view).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Remove Bills by company" })).toBeNull();
  expect(restored.view.baseFilter).toEqual(baseFilter);
});

test.each(["local", "route"] as const)("removing grouping preserves other filters; reset and Clear retain %s defaults and base scope", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view).toBeTruthy());
  act(() => f.search.applyFavorite(favorite.id));
  await screen.findByRole("button", { name: "Remove Bills by company" });
  act(() => f.search.setText("review"));
  await waitFor(() => expect(f.view.state.filter).toEqual({ title: { iContains: "review" } }));
  fireEvent.click(screen.getByRole("button", { name: "Remove Company" }));
  await waitFor(() => expect(f.view.state.groupStack).toEqual([]));
  expect(f.view.state.filter).toEqual({ title: { iContains: "review" } });
  act(() => f.search.applyFavorite(favorite.id));
  await screen.findByRole("button", { name: "Remove Bills by company" });
  act(() => f.search.resetQuery());
  await waitFor(() => expect(f.view.state).toMatchObject({ groupStack: [], filter: {}, sorting: [] }));
  expect(screen.queryByRole("button", { name: "Remove Bills by company" })).toBeNull();
  act(() => f.search.clearQuery());
  await waitFor(() => expect(f.view.state).toMatchObject(defaults));
  expect(f.search.queryDirty).toBe(false);
  expect(f.view.baseFilter).toEqual(baseFilter);
  expect(f.router.state.location.search).toMatchObject({ keep: "external", company: "A" });
});
