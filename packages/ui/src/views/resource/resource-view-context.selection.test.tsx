// @vitest-environment happy-dom

import { act, cleanup, render, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode, useState, type ReactElement } from "react";
import { afterEach, expect, test } from "vitest";
import { ModelMetadataProvider, ResourceQuery, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider } from "../../runtime";
import { routeSearchString } from "../../runtime/route-href";
import type { ResourceViewPreset } from "./model/favorites";
import { ResourceViewProvider, useResourceView, withResourceViewScope, type ResourceViewContextValue, type ResourceViewProviderProps } from "./resource-view-context";

afterEach(cleanup);

const preset: ResourceViewPreset = {
  id: "notes.open", label: "Open", resource: "notes.Note", fixedFilter: { status: { exact: "open" } },
};
const metadata = schemaFieldMetadataFromDataResources(["notes.Note", "tasks.Task"].map((model) => testDataResource(model, {
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, owner: { scalar: "ID" }, title: {}, status: {} } }).contract,
})));

function fixture(scope: "local" | "route", options: {
  initialState?: ResourceViewProviderProps["initialState"];
  initialEntry?: string;
  content?: ReactElement;
} = {}) {
  let view!: ResourceViewContextValue;
  let rerenderProvider!: () => void;
  const renders: ResourceViewContextValue[] = [];
  function Probe() {
    view = useResourceView();
    renders.push(view);
    return options.content ?? null;
  }
  function Owner(props: ResourceViewProviderProps) {
    const [, setRender] = useState(0);
    rerenderProvider = () => setRender((current) => current + 1);
    return <ResourceViewProvider {...props} />;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", validateSearch: (search) => search,
    component: () => <Owner scope="route" resource="notes.Note" presetIds={[preset.id]} initialState={options.initialState}><Probe /></Owner> });
  const router = createRouter({
    routeTree: root.addChildren([route]),
    history: createMemoryHistory({ initialEntries: [options.initialEntry ?? "/"] }),
    // Match the app's flat search codec; TanStack's default parses filter JSON into an object.
    parseSearch: (value) => Object.fromEntries(new URLSearchParams(value)),
    stringifySearch: (value) => { const query = routeSearchString(value); return query ? `?${query}` : ""; },
  });
  const tree = (props: Omit<ResourceViewProviderProps, "children"> = {}, fixedFilter = preset.fixedFilter) => (
    <StrictMode><ModelMetadataProvider metadata={metadata}><AppRuntimeProvider runtime={{ resourceViews: { [preset.id]: { ...preset, fixedFilter } } }}>
      {scope === "route" ? <RouterProvider router={router} />
        : <Owner scope="local" resource="notes.Note" presetIds={[preset.id]} initialState={options.initialState} {...props}><Probe /></Owner>}
    </AppRuntimeProvider></ModelMetadataProvider></StrictMode>
  );
  const mounted = render(tree());
  return { get view() { return view; }, router, renders, rerenderProvider: () => rerenderProvider(),
    rerender: (props: Omit<ResourceViewProviderProps, "children">, fixedFilter = preset.fixedFilter) => mounted.rerender(tree(props, fixedFilter)) };
}

test.each(["local", "route"] as const)("%s selection survives pagination, page size, grouped pagination and view switches", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.toggleSelectedId("note-1", true));
  act(() => f.view.setPage(2));
  await waitFor(() => expect(f.view.state.pagination.pageIndex).toBe(1));
  expect(f.view.state.rowSelection).toEqual({ "note-1": true });
  act(() => f.view.setPageSize(50));
  await waitFor(() => expect(f.view.state.pagination.pageSize).toBe(50));
  expect(f.view.state.rowSelection).toEqual({ "note-1": true });
  act(() => f.view.setPaginationByScope({ group: { pageIndex: 2, pageSize: 10 } }));
  expect(f.view.state.rowSelection).toEqual({ "note-1": true });
  act(() => f.view.setPaginationByScope({ group: { pageIndex: 0, pageSize: 25 } }));
  expect(f.view.state.rowSelection).toEqual({ "note-1": true });
  act(() => f.view.setView("board"));
  await waitFor(() => expect(f.view.state.view).toBe("board"));
  expect(f.view.state.rowSelection).toEqual({ "note-1": true });
  expect(f.view.paginationByScope).toEqual({ group: { pageIndex: 0, pageSize: 25 } });
});

test.each(["local", "route"] as const)("%s a query change discards selection writes made before the new collection renders", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => {
    f.view.toggleSelectedId("old", true);
    f.view.setFilter({ status: { exact: "open" } });
    f.view.toggleSelectedId("new", true, { id: "new", revision: 1 });
  });
  await waitFor(() => expect(f.view.state.filter).toEqual({ status: { exact: "open" } }));
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.view.selectedRows).toEqual([]);
  act(() => f.view.toggleSelectedId("after-render", true));
  expect(f.view.state.rowSelection).toEqual({ "after-render": true });
});

test("a local query change clears a selection write in the same React batch", () => {
  const f = fixture("local");
  const start = f.renders.length;
  act(() => {
    f.view.setFilter({ status: { exact: "open" } });
    f.view.toggleSelectedId("same-batch", true, { id: "same-batch", revision: 1 });
  });
  expect(f.view.state.filter).toEqual({ status: { exact: "open" } });
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.view.selectedRows).toEqual([]);
  expect(f.renders.slice(start).every((value) => Object.keys(value.state.rowSelection).length === 0)).toBe(true);
});

test.each([
  { sort: "title:desc" },
  { filter: JSON.stringify({ status: { exact: "open" } }) },
  { group: "status" },
  { preset: preset.id },
])("route edits and history discard selection before children render (%j)", async (search) => {
  const f = fixture("route");
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.toggleSelectedId("note-1", true));
  const start = f.renders.length;
  await act(async () => { await f.router.navigate({ to: "/", search }); });
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.renders.slice(start).filter((value) => JSON.stringify(value.state.filter) !== "{}"
    || value.state.sorting !== undefined || value.state.groupStack.length > 0 || value.state.preset === preset.id)
    .every((value) => Object.keys(value.state.rowSelection).length === 0)).toBe(true);
  act(() => f.view.toggleSelectedId("destination", true));
  const beforeBack = f.renders.length;
  await act(async () => { f.router.history.back(); });
  await waitFor(() => expect(f.view.state).toMatchObject({ filter: {}, groupStack: [] }));
  expect(f.view.state.sorting).toBeUndefined();
  expect(f.view.state.preset).toBeUndefined();
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.renders.slice(beforeBack).filter((value) => JSON.stringify(value.state.filter) === "{}"
    && value.state.sorting === undefined && value.state.groupStack.length === 0 && value.state.preset === undefined)
    .every((value) => Object.keys(value.state.rowSelection).length === 0)).toBe(true);
  act(() => f.view.toggleSelectedId("back", true));
  const beforeForward = f.renders.length;
  await act(async () => { f.router.history.forward(); });
  await waitFor(() => expect(f.router.state.location.search).toEqual(search));
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.renders.slice(beforeForward).filter((value) => JSON.stringify(value.state.filter) !== "{}"
    || value.state.sorting !== undefined || value.state.groupStack.length > 0 || value.state.preset === preset.id)
    .every((value) => Object.keys(value.state.rowSelection).length === 0)).toBe(true);
});

test("a direct Router edit or history change discards writes made before the destination renders", async () => {
  const f = fixture("route");
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.toggleSelectedId("old", true));
  await act(async () => {
    const navigation = f.router.navigate({ to: "/", search: { sort: "title:desc" } });
    f.view.toggleSelectedId("new", true);
    await navigation;
  });
  expect(f.view.state.sorting).toEqual([{ id: "title", desc: true }]);
  expect(f.view.state.rowSelection).toEqual({});
  await act(async () => {
    f.router.history.back();
    f.view.toggleSelectedId("restored", true);
  });
  await waitFor(() => expect(f.view.state.sorting).toBeUndefined());
  expect(f.view.state.rowSelection).toEqual({});
});

test.each([
  { resource: "tasks.Task" },
  { baseFilter: { status: { exact: "open" } } },
])("a changed collection identity clears local selection (%j)", (props) => {
  const f = fixture("local");
  act(() => f.view.toggleSelectedId("note-1", true));
  f.rerender(props);
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.view.selectedRows).toEqual([]);
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

test.each(["local", "route"] as const)("%s a preset name or view change without a query change keeps selection and group state", async (scope) => {
  const f = fixture(scope, { initialState: { sorting: [] } });
  await waitFor(() => expect(f.view).toBeDefined());
  // The route has no preset selected; an explicit empty preset changes only its name.
  act(() => f.view.toggleSelectedId("one", true));
  const expansion = { axisKey: "status", collapsedKeys: new Set(["closed"]), explicitExpandedKeys: new Set<string>(), defaultExpandedKeys: new Set<string>() };
  act(() => f.view.setGroupExpansion(expansion));
  act(() => f.view.clearPreset());
  await waitFor(() => expect(f.view.state.preset).toBe(""));
  expect(f.view.state.rowSelection).toEqual({ one: true });
  expect(f.view.groupExpansion).toBe(expansion);
  act(() => f.view.setView("board"));
  await waitFor(() => expect(f.view.state.view).toBe("board"));
  expect(f.view.state.rowSelection).toEqual({ one: true });
  expect(f.view.groupExpansion).toBe(expansion);
});

test.each(["local", "route"] as const)("%s a view switch that installs different default groups clears selection", async (scope) => {
  const f = fixture(scope, { initialState: { groupStacks: { list: null, board: [{ field: "status" }] } } });
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.toggleSelectedId("one", true));
  act(() => f.view.setView("board"));
  await waitFor(() => expect(f.view.state.groupStack).toEqual([{ field: "status" }]));
  expect(f.view.state.rowSelection).toEqual({});
});

test.each(["local", "route"] as const)("%s returning to a previous collection does not revive group pagination or expansion", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.setPaginationByScope({ group: { pageIndex: 2, pageSize: 10 } }));
  act(() => f.view.setGroupExpansion({ axisKey: "status", collapsedKeys: new Set(["closed"]),
    explicitExpandedKeys: new Set<string>(), defaultExpandedKeys: new Set<string>() }));
  act(() => f.view.setFilter({ status: { exact: "open" } }));
  await waitFor(() => expect(f.view.state.filter).toEqual({ status: { exact: "open" } }));
  expect(f.view.paginationByScope).toEqual({});
  expect(f.view.groupExpansion).toBeNull();
  act(() => f.view.setFilter({}));
  await waitFor(() => expect(f.view.state.filter).toEqual({}));
  expect(f.view.paginationByScope).toEqual({});
  expect(f.view.groupExpansion).toBeNull();
});

test.each(["local", "route"] as const)("%s unchanged query resets and favorites keep selection", async (scope) => {
  const f = fixture(scope, { initialState: { sorting: [] } });
  await waitFor(() => expect(f.view).toBeDefined());
  const row = { id: "one", revision: 1 };
  act(() => f.view.toggleSelectedId("one", true, row));
  for (const reset of [() => f.view.resetQuery(), () => f.view.clearQuery(),
    () => f.view.applyFavorite({ id: "all", label: "All", filter: {}, sort: null, groupStack: [] })]) {
    act(() => f.view.setPage(2));
    await waitFor(() => expect(f.view.state.pagination.pageIndex).toBe(1));
    await act(async () => { reset(); });
    await waitFor(() => expect(f.view.state.pagination.pageIndex).toBe(0));
    expect(f.view.state.rowSelection).toEqual({ one: true });
    expect(f.view.selectedRows).toEqual([row]);
  }
});

test.each(["local", "route"] as const)("%s unchanged provider renders keep context and state identities", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view).toBeDefined());
  act(() => f.view.toggleSelectedId("one", true, { id: "one", revision: 1 }));
  const before = f.view;
  act(() => f.rerenderProvider());
  expect(f.view).toBe(before);
  expect(f.view.state).toBe(before.state);
  expect(f.view.selectedRows).toBe(before.selectedRows);
});

test.each(["local", "route"] as const)("%s empty clears and equivalent selection writes keep the owner unchanged", async (scope) => {
  const f = fixture(scope);
  await waitFor(() => expect(f.view).toBeDefined());
  const empty = f.view;
  const beforeClear = f.renders.length;
  act(() => f.view.clearSelectedIds());
  expect(f.view).toBe(empty);
  expect(f.renders).toHaveLength(beforeClear);
  const original = { id: "one", revision: 1 };
  act(() => f.view.toggleSelectedId("one", true, original));
  const selected = f.view;
  const beforeWrite = f.renders.length;
  act(() => f.view.setRowSelection({ one: true }, () => ({ id: "one", revision: 2 })));
  expect(f.view).toBe(selected);
  expect(f.renders).toHaveLength(beforeWrite);
  expect(f.view.selectedRows).toEqual([original]);
  act(() => f.view.toggleSelectedId("one", false));
  const deselected = f.view;
  const afterDeselect = f.renders.length;
  act(() => f.view.clearSelectedIds());
  expect(f.view).toBe(deselected);
  expect(f.renders).toHaveLength(afterDeselect);
  expect(f.view.selectedRows).toEqual([]);
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

test.each(["local", "route"] as const)("%s nested A → B → A never revives ids, even without a selection in B", async (scope) => {
  let child!: ResourceViewContextValue;
  let parent!: ResourceViewContextValue;
  let setFolder!: (folder: string) => void;
  const childRenders: ResourceViewContextValue[] = [];
  function Probe() { child = useResourceView(); childRenders.push(child); return null; }
  function Folder() {
    const [folder, change] = useState("A");
    setFolder = change;
    parent = useResourceView();
    return withResourceViewScope({ ambient: parent, baseFilter: { status: { exact: folder } }, children: () => <Probe /> });
  }
  const f = fixture(scope, { content: <Folder /> });
  await waitFor(() => expect(child).toBeDefined());
  act(() => child.toggleSelectedId("row-A", true, { id: "row-A", revision: 1 }));
  expect(child.state.rowSelection).toEqual({ "row-A": true });
  expect(parent.state.rowSelection).toEqual({});
  expect(parent.selectedRows).toEqual([]);
  const beforeB = childRenders.length;
  act(() => setFolder("B"));
  expect(child.state.rowSelection).toEqual({});
  expect(child.selectedRows).toEqual([]);
  expect(childRenders.slice(beforeB).every((value) => Object.keys(value.state.rowSelection).length === 0)).toBe(true);
  const beforeA = childRenders.length;
  act(() => setFolder("A"));
  expect(child.state.rowSelection).toEqual({});
  expect(child.selectedRows).toEqual([]);
  expect(childRenders.slice(beforeA).every((value) => Object.keys(value.state.rowSelection).length === 0)).toBe(true);
  act(() => child.toggleSelectedId("row-A", true));
  act(() => setFolder("B"));
  act(() => child.toggleSelectedId("row-B", true, { id: "row-B", revision: 2 }));
  expect(child.state.rowSelection).toEqual({ "row-B": true });
  expect(child.selectedRows).toEqual([{ id: "row-B", revision: 2 }]);
  expect(parent.state.rowSelection).toEqual({});
  act(() => setFolder("A"));
  expect(child.state.rowSelection).toEqual({});
  expect(child.selectedRows).toEqual([]);
  expect(f.renders.every((value) => Object.keys(value.state.rowSelection).length === 0)).toBe(true);
});

test.each(["local", "route"] as const)("%s nested X → unscoped → X never revives X's ids", async (scope) => {
  let child!: ResourceViewContextValue;
  let parent!: ResourceViewContextValue;
  let setScoped!: (scoped: boolean) => void;
  function Probe() { child = useResourceView(); return null; }
  function MaybeScoped() {
    const [scoped, change] = useState(true);
    setScoped = change;
    parent = useResourceView();
    return withResourceViewScope({
      ambient: parent, baseFilter: scoped ? { status: { exact: "X" } } : undefined, children: () => <Probe />,
    });
  }
  fixture(scope, { content: <MaybeScoped /> });
  await waitFor(() => expect(child).toBeDefined());
  act(() => child.toggleSelectedId("row-X", true, { id: "row-X", revision: 1 }));
  expect(child.state.rowSelection).toEqual({ "row-X": true });
  act(() => setScoped(false));
  expect(child.state.rowSelection).toEqual({});
  act(() => setScoped(true));
  expect(child.state.rowSelection).toEqual({});
  expect(child.selectedRows).toEqual([]);
  // The unscoped view owns the owner's own scope while it is active.
  act(() => setScoped(false));
  act(() => child.toggleSelectedId("row-all", true));
  expect(parent.state.rowSelection).toEqual({ "row-all": true });
  act(() => setScoped(true));
  expect(child.state.rowSelection).toEqual({});
});

test.each([
  { owner: { exact: 5 } },
  { OR: { owner: { exact: 5 } } },
])("a route filter normalized by validation allows nested scope writes (%j)", async (filter) => {
  let child!: ResourceViewContextValue;
  function Child() { child = useResourceView(); return null; }
  function Nested() {
    const ambient = useResourceView();
    return withResourceViewScope({ ambient, baseFilter: { status: { exact: "open" } }, children: () => <Child /> });
  }
  const f = fixture("route", { initialEntry: `/?filter=${encodeURIComponent(JSON.stringify(filter))}`, content: <Nested /> });
  await waitFor(() => expect(child).toBeDefined());
  expect(f.view.state.queryError).toBeFalsy();
  expect(f.view.state.filter).toEqual("OR" in filter
    ? { OR: [{ owner: { exact: "5" } }] } : { owner: { exact: "5" } });
  act(() => child.toggleSelectedId("selected", true));
  expect(child.state.rowSelection).toEqual({ selected: true });
  expect(f.view.state.rowSelection).toEqual({});
});

test.each(["local", "route"] as const)("%s a nested scope keeps ids and snapshots when remounted with the same key", async (scope) => {
  let child!: ResourceViewContextValue;
  let setVisible!: (visible: boolean) => void;
  const childRenders: ResourceViewContextValue[] = [];
  const baseFilter = { status: { exact: "A" } };
  function Probe() { child = useResourceView(); childRenders.push(child); return null; }
  function Folder() {
    const ambient = useResourceView();
    const [visible, change] = useState(true);
    setVisible = change;
    return visible ? withResourceViewScope({ ambient, baseFilter, children: () => <Probe /> }) : null;
  }
  const f = fixture(scope, { content: <Folder /> });
  await waitFor(() => expect(child).toBeDefined());
  const row = { id: "row-A", revision: 1 };
  act(() => child.toggleSelectedId(row.id, true, row));
  act(() => setVisible(false));
  const beforeRemount = childRenders.length;
  act(() => setVisible(true));
  expect(child.state.rowSelection).toEqual({ "row-A": true });
  expect(child.selectedRows[0]).toBe(row);
  expect(childRenders.slice(beforeRemount).every((value) => value.state.rowSelection["row-A"])).toBe(true);
  expect(f.view.state.rowSelection).toEqual({});
  expect(f.view.selectedRows).toEqual([]);
});

test.each(["local", "route"] as const)("%s a nested scope keeps context and state identities across unchanged renders", async (scope) => {
  let child!: ResourceViewContextValue;
  let rerenderFolder!: () => void;
  const baseFilter = { status: { exact: "A" } };
  function Probe() { child = useResourceView(); return null; }
  function Folder() {
    const ambient = useResourceView();
    const [, setRender] = useState(0);
    rerenderFolder = () => setRender((current) => current + 1);
    return withResourceViewScope({ ambient, baseFilter, children: () => <Probe /> });
  }
  const f = fixture(scope, { content: <Folder /> });
  await waitFor(() => expect(child).toBeDefined());
  act(() => child.toggleSelectedId("row-A", true, { id: "row-A", revision: 1 }));
  const before = child;
  act(() => rerenderFolder());
  expect(child).toBe(before);
  expect(child.state).toBe(before.state);
  act(() => f.rerenderProvider());
  expect(child).toBe(before);
  expect(child.state).toBe(before.state);
});

test.each(["local", "route"] as const)("%s late empty writes from an inactive scope leave the active selection intact", async (scope) => {
  let child!: ResourceViewContextValue;
  let setFolder!: (folder: string) => void;
  function Probe() { child = useResourceView(); return null; }
  function Folder() {
    const ambient = useResourceView();
    const [folder, change] = useState("A");
    setFolder = change;
    return withResourceViewScope({ ambient, baseFilter: { status: { exact: folder } }, children: () => <Probe /> });
  }
  const f = fixture(scope, { content: <Folder /> });
  await waitFor(() => expect(child).toBeDefined());
  act(() => child.toggleSelectedId("row-A", true));
  const previous = child;
  act(() => setFolder("B"));
  const row = { id: "row-B", revision: 1 };
  act(() => child.toggleSelectedId(row.id, true, row));
  const active = child;
  act(() => {
    previous.clearSelectedIds();
    previous.toggleSelectedId("row-A", false);
    f.view.clearSelectedIds();
  });
  expect(child).toBe(active);
  expect(child.state.rowSelection).toEqual({ "row-B": true });
  expect(child.selectedRows).toEqual([row]);
  // Owner writes replace the nested selection; an old child clear cannot flip it back.
  act(() => f.view.toggleSelectedId("owner", true));
  expect(f.view.state.rowSelection).toEqual({ owner: true });
  expect(child.state.rowSelection).toEqual({});
  act(() => previous.clearSelectedIds());
  expect(f.view.state.rowSelection).toEqual({ owner: true });
});

test.each(["local", "route"] as const)("%s snapshots follow selection-map order and are dropped on deselection", async (scope) => {
  const f = fixture(scope, { initialState: { selectedIds: ["initial"] } });
  await waitFor(() => expect(f.view).toBeDefined());
  expect(f.view.state.rowSelection).toEqual({ initial: true });
  expect(f.view.selectedRows).toEqual([]);
  const first = { id: "one", revision: 1 };
  const updated = { id: "one", revision: 2 };
  const second = { id: "two", revision: 3 };
  act(() => f.view.toggleSelectedId("one", true, first));
  act(() => f.view.setRowSelection((current) => ({ ...current, two: true, missing: true }), (id) => id === "two" ? second : undefined));
  expect(f.view.selectedRows).toEqual([first, second]);
  act(() => f.view.toggleSelectedId("one", true, updated));
  expect(f.view.selectedRows[0]).toBe(first);
  act(() => f.view.setRowSelection({ two: true, one: true, initial: true }, () => updated));
  expect(f.view.selectedRows).toEqual([second, first]);
  act(() => f.view.toggleSelectedId("one", false));
  expect(f.view.selectedRows).toEqual([second]);
  act(() => f.view.toggleSelectedId("one", true, updated));
  expect(f.view.selectedRows).toEqual([second, updated]);
  act(() => f.view.clearSelectedIds());
  expect(f.view.selectedRows).toEqual([]);
});

test("integer-like ids use object key order rather than selection chronology", () => {
  const f = fixture("local");
  const first = { id: "20", revision: 1 };
  const second = { id: "3", revision: 1 };
  act(() => f.view.toggleSelectedId(first.id, true, first));
  act(() => f.view.toggleSelectedId(second.id, true, second));
  expect(f.view.selectedRows).toEqual([second, first]);
});
