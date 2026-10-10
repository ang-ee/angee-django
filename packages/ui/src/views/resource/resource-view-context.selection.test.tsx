// @vitest-environment happy-dom

import { act, cleanup, render, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode, useState } from "react";
import { afterEach, expect, test } from "vitest";
import { ModelMetadataProvider, ResourceQuery, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider } from "../../runtime";
import type { ResourceViewPreset } from "./model/favorites";
import { ResourceViewProvider, useResourceView, withResourceViewScope, type ResourceViewContextValue, type ResourceViewProviderProps } from "./resource-view-context";

afterEach(cleanup);

const preset: ResourceViewPreset = {
  id: "notes.open", label: "Open", resource: "notes.Note", fixedFilter: { status: { exact: "open" } },
};
const metadata = schemaFieldMetadataFromDataResources(["notes.Note", "tasks.Task"].map((model) => testDataResource(model, {
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, title: {}, status: {} } }).contract,
})));

function fixture(scope: "local" | "route") {
  let view!: ResourceViewContextValue;
  const renders: ResourceViewContextValue[] = [];
  function Probe() {
    view = useResourceView();
    renders.push(view);
    return null;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", validateSearch: (search) => search,
    component: () => <ResourceViewProvider scope="route" resource="notes.Note" presetIds={[preset.id]}><Probe /></ResourceViewProvider> });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const tree = (props: Omit<ResourceViewProviderProps, "children"> = {}, fixedFilter = preset.fixedFilter) => (
    <StrictMode><ModelMetadataProvider metadata={metadata}><AppRuntimeProvider runtime={{ resourceViews: { [preset.id]: { ...preset, fixedFilter } } }}>
      {scope === "route" ? <RouterProvider router={router} />
        : <ResourceViewProvider scope="local" resource="notes.Note" presetIds={[preset.id]} {...props}><Probe /></ResourceViewProvider>}
    </AppRuntimeProvider></ModelMetadataProvider></StrictMode>
  );
  const mounted = render(tree());
  return { get view() { return view; }, router, renders,
    rerender: (props: Omit<ResourceViewProviderProps, "children">, fixedFilter = preset.fixedFilter) => mounted.rerender(tree(props, fixedFilter)) };
}

test.each(["local", "route"] as const)("%s selection survives ordinary and grouped pagination", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.toggleSelectedId("note-1", true));
  act(() => {
    f.view.setPage(2);
    f.view.setPageSize(50);
    f.view.setPaginationByScope({ group: { pageIndex: 2, pageSize: 10 } });
  });
  await waitFor(() => expect(f.view.state.pagination.pageSize).toBe(50));
  expect(f.view.state.rowSelection).toEqual({ "note-1": true });
});

test.each(["local", "route"] as const)("%s selection made after query setters in one batch belongs to the new query", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => {
    f.view.toggleSelectedId("old", true);
    f.view.setSorting([{ id: "title", desc: true }]);
    f.view.setFilter({ status: { exact: "open" } });
    f.view.setGroupStack([{ field: "status" }]);
    f.view.setView("board");
    f.view.toggleSelectedId("new", true);
  });
  await waitFor(() => expect(f.view.state.filter).toEqual({ status: { exact: "open" } }));
  expect(f.view.state.sorting).toEqual([{ id: "title", desc: true }]);
  expect(f.view.state.groupStack).toEqual([{ field: "status" }]);
  expect(f.view.state.view).toBe("board");
  expect(f.view.state.rowSelection).toEqual({ new: true });
  act(() => f.view.resetQuery());
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({}));
});

test.each([
  { sort: "title:desc" },
  { filter: JSON.stringify({ status: { exact: "open" } }) },
  { group: "status" },
  { view: "board" },
  { preset: preset.id },
])("route edits and history discard selection before children render (%j)", async (search) => {
  const f = fixture("route");
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.toggleSelectedId("note-1", true));
  const start = f.renders.length;
  await act(async () => { await f.router.navigate({ to: "/", search }); });
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.renders.slice(start).filter((value) => value.state !== f.renders[start - 1]?.state)
    .every((value) => Object.keys(value.state.rowSelection).length === 0)).toBe(true);
  // No intervening selection is needed to invalidate the old query's ids.
  await act(async () => { f.router.history.back(); });
  expect(f.view.state.rowSelection).toEqual({});
});

test("selection made after a direct Router edit or history change in one batch belongs to the new query", async () => {
  const f = fixture("route");
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.toggleSelectedId("old", true));
  await act(async () => {
    const navigation = f.router.navigate({ to: "/", search: { sort: "title:desc" } });
    f.view.toggleSelectedId("new", true);
    await navigation;
  });
  expect(f.view.state.sorting).toEqual([{ id: "title", desc: true }]);
  expect(f.view.state.rowSelection).toEqual({ new: true });
  await act(async () => {
    f.router.history.back();
    f.view.toggleSelectedId("restored", true);
  });
  expect(f.view.state.sorting).toBeUndefined();
  expect(f.view.state.rowSelection).toEqual({ restored: true });
});

test.each([
  { resource: "tasks.Task" },
  { baseFilter: { status: { exact: "open" } } },
])("a changed collection identity clears local selection (%j)", (props) => {
  const f = fixture("local");
  act(() => f.view.toggleSelectedId("note-1", true));
  f.rerender(props);
  expect(f.view.state.rowSelection).toEqual({});
  f.rerender({});
  expect(f.view.state.rowSelection).toEqual({});
});

test("a changed preset fixed filter clears selection even with the same preset id", () => {
  const f = fixture("local");
  act(() => f.view.applyFavorite({ id: preset.id, label: preset.label, preset: preset.id }));
  act(() => f.view.toggleSelectedId("note-1", true));
  f.rerender({}, { status: { exact: "closed" } });
  expect(f.view.state.rowSelection).toEqual({});
});

test("a pending destination loader cannot change the mounted source view's search", async () => {
  let view!: ResourceViewContextValue;
  let release!: () => void;
  let started!: () => void;
  const loading = new Promise<void>((resolve) => { started = resolve; });
  const pending = new Promise<void>((resolve) => { release = resolve; });
  function Probe() { view = useResourceView(); return null; }
  const root = createRootRoute();
  const source = createRoute({ getParentRoute: () => root, path: "/list", validateSearch: (search) => search,
    component: () => <ResourceViewProvider resource="notes.Note"><Probe /></ResourceViewProvider> });
  const destination = createRoute({ getParentRoute: () => root, path: "/other", validateSearch: (search) => search,
    loader: async () => { started(); await pending; }, component: () => null });
  const router = createRouter({ routeTree: root.addChildren([source, destination]), defaultPendingMs: 100_000,
    history: createMemoryHistory({ initialEntries: ["/list?sort=title%3Aasc"] }) });
  render(<ModelMetadataProvider metadata={metadata}><AppRuntimeProvider runtime={{}}><RouterProvider router={router} /></AppRuntimeProvider></ModelMetadataProvider>);
  await waitFor(() => expect(view).toBeDefined());
  let navigation!: Promise<void>;
  await act(async () => { navigation = router.navigate({ to: "/other", search: { sort: "title:desc" } }); await loading; });
  expect(router.state.location.pathname).toBe("/other");
  expect(view.state.sorting).toEqual([{ id: "title", desc: false }]);
  await act(async () => { release(); await navigation; });
});

test("an inherited child's base filter scopes both selection reads and writes", () => {
  let child!: ResourceViewContextValue;
  let parent!: ResourceViewContextValue;
  let setFolder!: (folder: string) => void;
  function Probe() { child = useResourceView(); return null; }
  function Folder() {
    const [folder, change] = useState("A");
    setFolder = change;
    parent = useResourceView();
    return withResourceViewScope({ ambient: parent, baseFilter: { status: { exact: folder } }, children: () => <Probe /> });
  }
  render(<ModelMetadataProvider metadata={metadata}><AppRuntimeProvider runtime={{}}>
    <ResourceViewProvider resource="notes.Note" scope="local"><Folder /></ResourceViewProvider>
  </AppRuntimeProvider></ModelMetadataProvider>);
  act(() => child.toggleSelectedId("file-A", true));
  expect(child.state.rowSelection).toEqual({ "file-A": true });
  expect(parent.state.rowSelection).toEqual({});
  act(() => setFolder("B"));
  expect(child.state.rowSelection).toEqual({});
  act(() => child.toggleSelectedId("file-B", true));
  expect(child.state.rowSelection).toEqual({ "file-B": true });
  act(() => setFolder("A"));
  expect(child.state.rowSelection).toEqual({});
});
