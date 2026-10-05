// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { Filter, ModelMetadataProvider, ResourceQuery, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider } from "../../runtime";
import { ResourceToolbar, ResourceViewSwitcher } from "../../toolbars/ResourceToolbar";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { useResourceSearch } from "./search/use-resource-search";
import { useSearchCatalog } from "./search/catalog";
import { RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY } from "./resource-view-favorites";
import { favoriteFromResourceView, resourceViewPresetDefaults, validateResourceViewPreset, type ResourceViewPreset } from "./model/favorites";
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

beforeEach(() => vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1024, 80)));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function fixture(entry = "/", presetIds: readonly string[] = [archived.id], toolbar = false, menuResourceViewIds: readonly string[] = []) {
  let view!: ResourceViewContextValue;
  let facts!: ReturnType<typeof useResourceViewQueryFacts>;
  let table!: ReturnType<typeof useResourceViewTableState>;
  function Probe() {
    view = useResourceView();
    facts = useResourceViewQueryFacts({ resourceView: view, columns, modelMetadata: model });
    table = useResourceViewTableState({ resourceView: view, columns, modelMetadata: model,
      groupStack: view.state.groupStack });
    const catalog = useSearchCatalog({ resourceView: view, columns, rows: [], modelMetadata: model });
    const search = useResourceSearch({ resourceView: view, catalog });
    const controls = { search, pager: { total: 1, page: 1, pageSize: 20 }, view: view.state.view,
      availableViews: ["list", "board"] as const, onViewChange: view.setView, searchDeclaration: { box: "collapsed" as const } };
    if (toolbar) return <ResourceToolbar {...controls} />;
    return <ResourceViewSwitcher view={view.state.view} favorites={view.savedFavorites}
      onFavoriteSelect={view.applyFavorite} onViewChange={view.setView} />;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", validateSearch: (search) => search,
    component: () => <ResourceViewProvider resource="notes.Note" presetIds={presetIds}><Probe /></ResourceViewProvider> });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: [entry] }) });
  const rendered = render(<ModelMetadataProvider metadata={metadata}>
    <AppRuntimeProvider runtime={{ resourceViews: presets, defaultResourceView: open.id, menuResourceViewIds,
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
  expect(f.view.savedFavorites.map((favorite) => favorite.id)).not.toContain("favorite:recent");

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
  expect(screen.queryByRole("option", { name: "Recent notes" })).toBeNull();
  const archivedOption = await screen.findByRole("option", { name: archived.label });
  fireEvent.pointerDown(archivedOption, { pointerType: "mouse" });
  fireEvent.click(archivedOption);
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

test("a route offers only its declared presets even when another route uses the same model", async () => {
  const f = fixture("/", []);
  await waitFor(() => expect(f.view?.state.preset).toBe(open.id));
  expect(f.view.savedFavorites.map((favorite) => favorite.id)).not.toContain(archived.id);
  f.unmount();
  const crossRoute = fixture(`/?preset=${archived.id}`, []);
  await waitFor(() => expect(crossRoute.view?.state.queryError).toBeInstanceOf(Error));
});

test("a menu preset is admitted only for its target route", async () => {
  const f = fixture(`/?preset=${archived.id}`, [], false, [archived.id]);
  await waitFor(() => expect(f.view?.state.preset).toBe(archived.id));
  expect(f.view.state.queryError).toBeUndefined();
  expect(f.facts.mergedFilter).toEqual(archived.fixedFilter);
});

test("Clear ignores the route default and restores it after an edit", async () => {
  const f = fixture("/", [], true);
  await waitFor(() => expect(f.view?.state.preset).toBe(open.id));
  expect(f.view.queryDirty).toBe(false);
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  act(() => f.view.setFilter({ owner: { exact: "other" } }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Clear" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  await waitFor(() => expect(f.view.queryDirty).toBe(false));
  expect(f.view.state.preset).toBe(open.id);
  expect(f.view.state.filter).toEqual(open.filter);
  expect(f.view.state.groupStack).toEqual(open.groupStack);
  expect(f.facts.mergedFilter).toEqual(Filter.combine(open.fixedFilter, open.filter));
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
});

test("a local preset mount without presetIds keeps its initial view valid and unrestricted", () => {
  let view!: ResourceViewContextValue;
  const rendered = render(<ModelMetadataProvider metadata={metadata}>
    <AppRuntimeProvider runtime={{ resourceViews: presets }}>
      <ResourceViewProvider scope="local" resource="notes.Note"
        initialState={resourceViewPresetDefaults(undefined, archived)}>
        <LocalCapture onValue={(value) => { view = value; }} />
      </ResourceViewProvider>
    </AppRuntimeProvider>
  </ModelMetadataProvider>);
  expect(view.state.queryError).toBeUndefined();
  expect(view.state.preset).toBe(archived.id);
  expect(view.baseFilter).toEqual(archived.fixedFilter);
  expect(view.savedFavorites.map((favorite) => favorite.id)).toEqual([archived.id, open.id]);
  rendered.unmount();

  render(<ModelMetadataProvider metadata={metadata}>
    <AppRuntimeProvider runtime={{ resourceViews: presets }}>
      <ResourceViewProvider scope="local" resource="notes.Note" presetIds={[]}
        initialState={resourceViewPresetDefaults(undefined, archived)}>
        <LocalCapture onValue={(value) => { view = value; }} />
      </ResourceViewProvider>
    </AppRuntimeProvider>
  </ModelMetadataProvider>);
  expect(view.state.queryError).toBeUndefined();
  expect(view.savedFavorites.map((favorite) => favorite.id)).toEqual([archived.id]);
});

test("a shipped preset toggle shortcut removes its fixed scope in a local widget", () => {
  let view!: ResourceViewContextValue;
  function Widget() {
    view = useResourceView();
    const catalog = useSearchCatalog({ resourceView: view, columns, rows: [], modelMetadata: model });
    const search = useResourceSearch({ resourceView: view, catalog });
    const toolbar = { search, pager: { total: 1, page: 1, pageSize: 10 },
      view: "list" as const, searchDeclaration: { shortcuts: [{ kind: "toggle" as const, id: archived.id }] } };

    return <ResourceToolbar {...toolbar} />;
  }
  render(<ModelMetadataProvider metadata={metadata}>
    <AppRuntimeProvider runtime={{ resourceViews: presets }}>
      <ResourceViewProvider scope="local" resource="notes.Note"
        initialState={resourceViewPresetDefaults(undefined, archived)}>
        <Widget />
      </ResourceViewProvider>
    </AppRuntimeProvider>
  </ModelMetadataProvider>);
  const toggle = screen.getByRole("button", { name: archived.label });
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  fireEvent.click(toggle);
  expect(view.state.preset).toBe("");
  expect(view.baseFilter).toBeUndefined();
  expect(screen.getByRole("button", { name: "Clear" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(view.state.preset).toBe(archived.id);
  expect(view.baseFilter).toEqual(archived.fixedFilter);
});

test("clearing an active shipped preset removes its fixed filter", async () => {
  const f = fixture();
  await waitFor(() => expect(f.view.state.preset).toBe(open.id));
  act(() => f.view.clearPreset());
  await waitFor(() => expect(f.view.state.preset).toBe(""));
  expect(f.view.baseFilter).toBeUndefined();
  expect(f.router.state.location.search).toMatchObject({ preset: "" });
});

function LocalCapture({ onValue }: { onValue: (value: ResourceViewContextValue) => void }) {
  onValue(useResourceView());
  return null;
}

test.each([{ groupStack: [{ field: "status" }] }, { groupStack: [] }])("an explicit shipped preset stack $groupStack overrides per-view defaults", ({ groupStack }) => {
  let view!: ResourceViewContextValue;
  render(<ModelMetadataProvider metadata={metadata}>
    <AppRuntimeProvider runtime={{ resourceViews: presets }}>
      <ResourceViewProvider scope="local" resource="notes.Note" initialState={resourceViewPresetDefaults({
        groupStacks: { list: [{ field: "owner" }], board: [{ field: "owner" }] },
      }, { ...open, groupStack })}>
        <LocalCapture onValue={(value) => { view = value; }} />
      </ResourceViewProvider>
    </AppRuntimeProvider>
  </ModelMetadataProvider>);
  expect(view.state.groupStack).toEqual(groupStack);
  act(() => view.setGroupStack([{ field: "owner" }]));
  act(() => view.clearQuery());
  expect(view.state.groupStack).toEqual(groupStack);
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
