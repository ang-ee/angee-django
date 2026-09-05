// @vitest-environment happy-dom

import { act, cleanup, render, waitFor } from "@testing-library/react";
import { Refine, type DataProvider, type GetListParams } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources, type ModelMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { ToastProvider } from "@angee/ui/feedback/index";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "@angee/ui/views/resource-view-context";
import { resourceViewFavoritesFromJson, todayCalendarAnchor, type ResourceListOrder, type ResourceViewInitialState } from "@angee/ui/views/resource-view-model";
import { useResourceViewSurface, type ResourceViewSurface } from "@angee/ui/views/resource-view-surface";
import { afterEach, expect, test, vi } from "vitest";
import { parseFlatSearch, stringifyFlatSearch } from "../create-app";

const resource = testDataResource("notes.Note", { orderFields: ["title", "updated_at"], recordRepresentation: "title" });
const metadata = schemaFieldMetadataFromDataResources([resource]);
const model: ModelMetadata = { ...metadata.labels!["notes.Note"]!, fields: { id: { name: "id", kind: "scalar" }, title: { name: "title", kind: "scalar" }, updated_at: { name: "updated_at", kind: "scalar" } } };
const columns = [{ field: "title", sortable: true }, { field: "updated_at", sortable: true }];
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });

async function fixture({ total = 45, initialPath = "/?page=2&pageSize=20&keep=external", initialState = {}, order }: { total?: number; initialPath?: string; initialState?: ResourceViewInitialState; order?: ResourceListOrder } = {}) {
  const calls: GetListParams[] = [];
  const provider = {
    getApiUrl: () => "test://resource",
    getList: vi.fn(async (params: GetListParams) => {
      calls.push(params);
      return { data: [{ id: `row-${params.pagination?.currentPage}`, title: "Note" }], ...(total >= 0 ? { total } : {}) };
    }),
    getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  let view!: ResourceViewContextValue;
  let surface!: ResourceViewSurface;
  function Probe() {
    view = useResourceView();
    surface = useResourceViewSurface({ resource: "notes.Note", columns, order, resourceView: view, modelMetadata: model });
    return <output>{surface.rows.length}</output>;
  }
  const rootRoute = createRootRoute({ component: () => (
    <Refine dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
      <ModelMetadataProvider metadata={metadata}><ToastProvider>
        <ResourceViewProvider initialState={{ pageSize: 20, ...initialState }}><Probe /></ResourceViewProvider>
      </ToastProvider></ModelMetadataProvider>
    </Refine>
  ) });
  const history = createMemoryHistory({ initialEntries: [initialPath] });
  const router = createRouter({ routeTree: rootRoute, history, parseSearch: parseFlatSearch, stringifySearch: stringifyFlatSearch });
  render(<RouterProvider router={router} />);
  await waitFor(() => expect(surface?.rows.length).toBe(1));
  return { calls, provider, client, router, history, view: () => view, surface: () => surface, setTotal: (next: number) => { total = next; } };
}

test("native Table controls Refine requests and Router search without a second table state", async () => {
  const f = await fixture();
  expect(f.calls.at(-1)).toMatchObject({ resource: "notes", pagination: { currentPage: 2, pageSize: 20 } });
  expect(f.surface().table.getState().pagination).toEqual({ pageIndex: 1, pageSize: 20 });
  expect(f.surface().list.pageCount).toBe(3);
  act(() => f.surface().table.getRowModel().rows[0]!.toggleSelected(true));
  await act(async () => { f.surface().table.nextPage(); });
  await waitFor(() => expect(f.calls.at(-1)?.pagination?.currentPage).toBe(3));
  expect(f.view().state.rowSelection["row-2"]).toBe(true);
  expect(f.router.state.location.search).toMatchObject({ page: "3", keep: "external" });
  expect(f.history.length).toBe(1);

  for (const expected of ["asc", "desc", false] as const) {
    await act(async () => f.surface().table.getColumn("title")!.toggleSorting());
    await waitFor(() => expect(f.surface().table.getColumn("title")!.getIsSorted()).toBe(expected));
    expect(f.view().state.rowSelection).toEqual({});
    expect(f.view().state.pagination.pageIndex).toBe(0);
  }
  await act(async () => f.view().setFilter({ title: { iContains: "alpha" } }));
  await waitFor(() => expect(f.calls.at(-1)?.filters).toEqual([{ field: "title", operator: "contains", value: "alpha" }]));
  await act(async () => f.view().setFilter({}));
  expect(f.view().state.filter).toEqual({});
  await act(async () => f.surface().list.refetch());
  await waitFor(() => expect(f.calls.at(-1)?.filters).toEqual([]));
  await act(async () => f.surface().table.setPageSize(500));
  await waitFor(() => expect(f.calls.at(-1)?.pagination?.pageSize).toBe(100));
  expect((f.router.state.location.search as Record<string, unknown>).keep).toBe("external");
});

test("unknown totals retain disabled next/last controls despite native unknown page count", async () => {
  const f = await fixture({ total: -1, initialPath: "/" });
  expect(f.surface().table.getPageCount()).toBe(-1);
  expect(f.surface().list.pageCount).toBeUndefined();
  expect(f.surface().list.hasNext).toBe(false);
  await act(async () => f.surface().list.lastPage());
  expect(f.view().state.pagination.pageIndex).toBe(0);
});

test("a Notes descending declaration can clear and cycle without restoring the default", async () => {
  const order = { updated_at: "DESC" } as const;
  const f = await fixture({ initialPath: "/?keep=external", order });
  expect(f.calls.at(-1)?.sorters).toEqual([{ field: "updated_at", order: "desc" }]);

  for (const expected of [false, "asc", "desc", false] as const) {
    await act(async () => f.surface().table.getColumn("updated_at")!.toggleSorting());
    await waitFor(() => expect(f.surface().table.getColumn("updated_at")!.getIsSorted()).toBe(expected));
  }
  expect(f.router.state.location.search).toMatchObject({ sort: "", keep: "external" });
  const restored = await fixture({ initialPath: f.router.state.location.href, order });
  expect(restored.calls.at(-1)?.sorters).toEqual([]);
  expect(restored.surface().table.getColumn("updated_at")!.getIsSorted()).toBe(false);
});

test("an explicit URL sort takes precedence over a Notes descending declaration", async () => {
  const f = await fixture({ initialPath: "/?sort=title:asc&keep=external", order: { updated_at: "DESC" } });
  expect(f.calls.at(-1)?.sorters).toEqual([{ field: "title", order: "asc" }]);
  expect(f.surface().table.getColumn("title")!.getIsSorted()).toBe("asc");
});

test.each([0, 20])("a settled smaller total %i reconciles the native page and preserves unrelated search", async (total) => {
  const f = await fixture({ initialPath: "/?page=3&pageSize=20&keep=external" });
  f.setTotal(total);

  await act(async () => f.surface().list.refetch());

  await waitFor(() => expect(f.calls.at(-1)?.pagination?.currentPage).toBe(1));
  expect(f.view().state.pagination.pageIndex).toBe(0);
  expect(f.surface().list.pageCount).toBe(1);
  expect(f.router.state.location.search).toMatchObject({ keep: "external" });
});

test("a smaller cached total cannot clamp a pending or failed request", async () => {
  const f = await fixture({ initialPath: "/?page=3&pageSize=20&keep=external" });
  let reject!: (reason: Error) => void;
  vi.mocked(f.provider.getList).mockReturnValueOnce(new Promise((_resolve, rejectRead) => { reject = rejectRead; }));
  await act(async () => f.surface().list.refetch());
  await waitFor(() => expect(f.surface().list.fetching).toBe(true));
  const pending = f.client.getQueryCache().findAll({ fetchStatus: "fetching" });
  expect(pending).toHaveLength(1);
  const [query] = pending;
  act(() => f.client.setQueryData(query!.queryKey, { data: [], total: 20 }));
  await waitFor(() => expect(f.surface().list.total).toBe(20));
  expect(f.view().state.pagination.pageIndex).toBe(2);

  await act(async () => reject(new Error("read unavailable")));
  await waitFor(() => expect(f.surface().list.error).toBeTruthy());
  expect(f.view().state.pagination.pageIndex).toBe(2);
  expect(f.router.state.location.search).toMatchObject({ page: "3", keep: "external" });
});

test("unknown totals and pending or failed reads do not rewrite the requested page", async () => {
  const f = await fixture({ total: -1, initialPath: "/?page=3&pageSize=20&keep=external" });
  expect(f.view().state.pagination.pageIndex).toBe(2);
  let reject!: (reason: Error) => void;
  vi.mocked(f.provider.getList).mockReturnValueOnce(new Promise((_resolve, rejectRead) => { reject = rejectRead; }));

  await act(async () => f.surface().list.refetch());
  await waitFor(() => expect(f.surface().list.fetching).toBe(true));
  expect(f.view().state.pagination.pageIndex).toBe(2);
  await act(async () => reject(new Error("read unavailable")));
  await waitFor(() => expect(f.surface().list.error).toBeTruthy());
  expect(f.view().state.pagination.pageIndex).toBe(2);
  expect(f.router.state.location.search).toMatchObject({ page: "3", keep: "external" });
});

test("native Router preserves page-one and favorite clears with a later initial page", async () => {
  const f = await fixture({
    initialPath: "/?keep=external",
    initialState: { page: 3, sort: { field: "title", dir: "asc" }, filter: { title: { iContains: "alpha" } } },
  });
  expect(f.view().state.pagination.pageIndex).toBe(2);
  await act(async () => f.surface().table.firstPage());
  await waitFor(() => expect(f.calls.at(-1)?.pagination?.currentPage).toBe(1));
  expect(f.view().state.pagination.pageIndex).toBe(0);
  expect(f.router.state.location.search).toEqual({ page: "1", keep: "external" });

  await act(async () => f.surface().table.setPageIndex(2));
  expect(f.view().state.pagination.pageIndex).toBe(2);
  expect(f.router.state.location.search).toEqual({ keep: "external" });

  const [favorite] = resourceViewFavoritesFromJson(JSON.stringify([
    { id: "favorite:all", label: "All notes", pageSize: 20 },
  ]));
  await act(async () => f.view().applyFavorite(favorite!));
  expect(f.router.state.location.search).toEqual({ page: "1", sort: "", filter: "", keep: "external" });
  expect(f.view().state).toMatchObject({ pagination: { pageIndex: 0 }, sorting: [], filter: {} });
  await waitFor(() => expect(f.calls.at(-1)).toMatchObject({ pagination: { currentPage: 1 }, filters: [], sorters: [] }));
  expect(f.history.length).toBe(1);
});

test("native Router preserves calendar resets relative to page-owned defaults", async () => {
  const f = await fixture({
    initialPath: "/?keep=external",
    initialState: { page: 3, view: "calendar", mode: "week", anchor: "2000-01-01" },
  });
  const today = todayCalendarAnchor();
  await act(async () => f.view().setMode("month"));
  await act(async () => f.view().setAnchor(today));
  expect(f.view().state).toMatchObject({ mode: "month", anchor: today, pagination: { pageIndex: 2 } });
  expect(f.router.state.location.search).toEqual({ mode: "month", anchor: today, keep: "external" });

  await act(async () => f.view().applyFavorite({ id: "favorite:calendar", label: "Calendar", pageSize: 20, view: "calendar" }));
  expect(f.view().state).toMatchObject({ mode: "month", anchor: today, pagination: { pageIndex: 0 } });
  expect(f.router.state.location.search).toEqual({ page: "1", sort: "", mode: "month", anchor: today, keep: "external" });

  await act(async () => f.view().setMode("week"));
  await act(async () => f.view().setAnchor("2000-01-01"));
  expect(f.view().state).toMatchObject({ mode: "week", anchor: "2000-01-01" });
  expect(f.router.state.location.search).toEqual({ page: "1", sort: "", keep: "external" });
  expect(f.history.length).toBe(1);
});
