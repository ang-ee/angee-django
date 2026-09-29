// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { Filter, ModelMetadataProvider, ResourceQuery, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test } from "vitest";
import { AppRuntimeProvider } from "../../runtime";
import { ResourceViewSwitcher } from "../../toolbars/ResourceToolbar";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY } from "./resource-view-favorites";
import { favoriteFromResourceView, validateResourceViewPreset, type ResourceViewPreset } from "./model/favorites";
import { useResourceViewQueryFacts, useResourceViewTableState } from "./surface/table-state";

const resource = testDataResource("notes.Note", {
  query: ResourceQuery.forRows({ fields: { title: {}, status: {}, owner: {} } }).contract,
  fields: ["title", "status", "owner"].map((name) => ({ name, kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
});
const metadata = schemaFieldMetadataFromDataResources([resource]);
const model = metadata.labels[resource.modelLabel]!;
const columns = [{ field: "title" }, { field: "status" }, { field: "owner" }];
const open: ResourceViewPreset = {
  id: "desk.open", preset: "desk.open", label: "Open notes", resource: resource.modelLabel,
  fixedFilter: { status: { exact: "open" } }, filter: { owner: { exact: "me" } },
  groupStack: [{ field: "status" }], view: "board", columnVisibility: { owner: false },
  sort: { field: "title", dir: "desc" }, pageSize: 20,
};
const archived: ResourceViewPreset = {
  id: "desk.archived", preset: "desk.archived", label: "Archived notes", resource: resource.modelLabel,
  fixedFilter: { status: { exact: "archived" } }, view: "list", pageSize: 10,
};
const presets = { [open.id]: open, [archived.id]: archived };

afterEach(cleanup);

function fixture(entry = "/") {
  let view!: ResourceViewContextValue;
  let facts!: ReturnType<typeof useResourceViewQueryFacts>;
  let table!: ReturnType<typeof useResourceViewTableState>;
  function Probe() {
    view = useResourceView();
    facts = useResourceViewQueryFacts({ resourceView: view, columns, modelMetadata: model });
    table = useResourceViewTableState({ resourceView: view, columns, modelMetadata: model,
      groupStack: view.state.groupStack });
    return <ResourceViewSwitcher view={view.state.view} favorites={view.savedFavorites}
      onFavoriteSelect={view.applyFavorite} onViewChange={view.setView} />;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", validateSearch: (search) => search,
    component: () => <ResourceViewProvider resource="notes.Note"><Probe /></ResourceViewProvider> });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: [entry] }) });
  const rendered = render(<ModelMetadataProvider metadata={metadata}>
    <AppRuntimeProvider runtime={{ resourceViews: presets, defaultResourceView: open.id,
      userPreferences: { available: false, preferences: {
        [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: { version: 1, models: {
          "notes.Note": [{ id: "favorite:recent", label: "Recent notes", preset: open.id }],
        } },
      }, patchPreferences: async () => undefined } }}>
      <RouterProvider router={router} />
    </AppRuntimeProvider>
  </ModelMetadataProvider>);
  return { get view() { return view; }, get facts() { return facts; }, get table() { return table; }, router, ...rendered };
}

test("route presets seed native state; editable clears preserve the fixed query", async () => {
  const f = fixture();
  await waitFor(() => expect(f.view?.state.preset).toBe(open.id));
  expect(f.view.state).toMatchObject({ view: "board", pagination: { pageSize: 20 },
    groupStack: [{ field: "status" }], sorting: [{ id: "title", desc: true }], columnVisibility: { owner: false } });
  expect(f.facts.mergedFilter).toEqual(Filter.combine(open.fixedFilter, open.filter));
  expect(f.table.effectiveColumnVisibility.owner).toBe(false);
  expect(f.view.saveFavorite).toBeUndefined();
  expect(f.view.savedFavorites.map((favorite) => favorite.id)).toContain(open.id);
  expect(f.view.savedFavorites.map((favorite) => favorite.id)).toContain("favorite:recent");

  act(() => f.view.resetQuery());
  await waitFor(() => expect(f.view.state.filter).toEqual({}));
  expect(f.facts.mergedFilter).toEqual(open.fixedFilter);
  expect(f.router.state.location.search).toMatchObject({ filter: "", group: "", sort: "" });
  expect(JSON.stringify(f.router.state.location.search)).not.toContain("status");
  act(() => f.view.setColumnVisibility({ owner: true, title: false }));
  await waitFor(() => expect(f.table.effectiveColumnVisibility.title).toBe(false));
  expect(favoriteFromResourceView(f.view.state, "Mine")).toMatchObject({ preset: open.id, columnVisibility: { owner: true, title: false } });
});

test("switcher applies shipped views without writable preferences and URLs restore them", async () => {
  const f = fixture();
  fireEvent.click(await screen.findByRole("combobox", { name: "Favorites" }));
  expect(await screen.findByRole("option", { name: "Recent notes" })).toBeTruthy();
  fireEvent.click(await screen.findByRole("option", { name: archived.label }));
  await waitFor(() => expect(f.view.state.preset).toBe(archived.id));
  expect(f.view.state).toMatchObject({ view: "list", pagination: { pageSize: 10 }, groupStack: [], filter: {}, columnVisibility: {} });
  expect(f.facts.mergedFilter).toEqual(archived.fixedFilter);
  expect(f.router.state.location.search).toMatchObject({ preset: archived.id });
  const href = f.router.state.location.href;
  f.unmount();
  const restored = fixture(href);
  await waitFor(() => expect(restored.view?.state.preset).toBe(archived.id));
  expect(restored.facts.mergedFilter).toEqual(archived.fixedFilter);
  expect(restored.view.state.pagination.pageSize).toBe(10);
});

test("unknown URL presets produce a repairable error", async () => {
  const f = fixture("/?preset=missing");
  await waitFor(() => expect(f.view?.state.queryError).toBeInstanceOf(Error));
  act(() => f.view.resetQuery());
  await waitFor(() => expect(f.view.state.queryError).toBeFalsy());
  expect(f.view.state.preset).toBe(open.id);
});

test.each([
  { fixedFilter: { absent: { exact: "x" } } },
  { filter: { status: { absent: "x" } } },
  { groupStack: [{ field: "absent" }] },
  { sort: { field: "absent", dir: "asc" as const } },
  { columnVisibility: { absent: false } },
  { pageSize: 0 },
])("shipped view validation rejects unsupported query intent: %j", (invalid) => {
  expect(() => validateResourceViewPreset({ ...open, ...invalid }, model)).toThrow();
});
