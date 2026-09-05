// @vitest-environment happy-dom
import * as React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { Refine, type DataProvider, type GetListParams } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import type { ResourceListSnapshot, ListViewNavigationScope } from "./resource-view-surface";
import { ResourceViewProvider, useResourceView } from "./resource-view-context";
import { useListRecordNavigation } from "./use-list-record-navigation";

const resource = testDataResource("notes.Note");
const scope: ListViewNavigationScope = { filter: { AND: [{ title: { iContains: "needle" } }, { status: { exact: "active" } }] }, order: { updated_at: "DESC" }, page: 1, pageSize: 2 };
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });
function fixture({ initialScope = scope, initialId = "b", total = 4, getPage }: { initialScope?: ListViewNavigationScope | null; initialId?: string | null; total?: number; getPage?: (page: number) => Promise<{ id: string }[]> } = {}) {
  const getList = vi.fn(async (params: GetListParams) => ({ data: await (getPage?.(params.pagination!.currentPage!) ?? Promise.resolve(params.pagination?.currentPage === 2 ? [{ id: "c" }, { id: "d" }] : [{ id: "a" }, { id: "b" }])), ...(total >= 0 ? { total } : {}) }));
  const provider = { getApiUrl: () => "test://notes", getList, getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  const onSelect = vi.fn();
  const wrapper = ({ children }: { children: React.ReactNode }) => <Refine resources={[...refineResourcesFromDataResources([resource, testDataResource("notes.Other")])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource, testDataResource("notes.Other")])}>{children}</ModelMetadataProvider></Refine>;
  const hook = renderHook(() => {
    const [id, setId] = React.useState(initialId);
    const [context, setContext] = React.useState(initialScope);
    const navigation = useListRecordNavigation<{ id: string }>({ resource: "notes.Note", recordId: id, navigationScope: context, onSelect: (next, nextScope) => { onSelect(next, nextScope); setId(next); setContext(nextScope ?? null); } });
    return { ...navigation, id, setId, setContext };
  }, { wrapper });
  return { ...hook, getList, onSelect, client, wrapper };
}

test("native Refine pages the clicked leaf without changing its filter/order or selecting early", async () => {
  let release!: (rows: { id: string }[]) => void;
  const pendingPage = new Promise<{ id: string }[]>((resolve) => { release = resolve; });
  const f = fixture({ getPage: async (page) => page === 2 ? pendingPage : [{ id: "a" }, { id: "b" }] });
  await waitFor(() => expect(f.result.current.navigation?.current).toBe(2));
  expect(f.getList.mock.calls[0]?.[0]).toMatchObject({ resource: "notes", pagination: { currentPage: 1, pageSize: 2 }, sorters: [{ field: "updated_at", order: "desc" }], filters: [{ operator: "and", value: [{ field: "title", operator: "contains", value: "needle" }, { field: "status", operator: "eq", value: "active" }] }], meta: { fields: ["id"] } });
  act(() => f.result.current.navigation?.onNext?.());
  await waitFor(() => expect(f.getList).toHaveBeenCalledTimes(2));
  expect(f.result.current.id).toBe("b");
  expect(f.result.current.navigationScope?.page).toBe(1);
  expect(f.result.current.navigation?.onNext).toBeUndefined();
  await act(async () => release([{ id: "c" }, { id: "d" }]));
  await waitFor(() => expect(f.result.current.id).toBe("c"));
  expect(f.onSelect).toHaveBeenLastCalledWith("c", { ...scope, page: 2 });
  expect(f.result.current.navigation?.current).toBe(3);
  act(() => f.result.current.navigation?.onPrev?.());
  await waitFor(() => expect(f.result.current.id).toBe("b"));
  expect(f.getList).toHaveBeenCalledTimes(2);
});

test("unknown totals allow loaded neighbors but never fabricate a next page or total", async () => {
  const f = fixture({ initialId: "a", total: -1 });
  await waitFor(() => expect(f.result.current.navigation?.current).toBe(1));
  expect(f.result.current.navigation?.total).toBeUndefined();
  act(() => f.result.current.navigation?.onNext?.());
  await waitFor(() => expect(f.result.current.id).toBe("b"));
  expect(f.result.current.navigation?.onNext).toBeUndefined();
  expect(f.result.current.navigation?.onPrev).toBeTypeOf("function");
  expect(f.getList).toHaveBeenCalledTimes(1);
});

test("a direct or invalid context performs no broad fallback list query", async () => {
  const f = fixture({ initialScope: null });
  expect(f.result.current.navigation).toBeNull();
  expect(f.getList).not.toHaveBeenCalled();
  act(() => f.result.current.setContext(scope));
  await waitFor(() => expect(f.result.current.navigation?.current).toBe(2));
  act(() => f.result.current.setContext(null));
  expect(f.result.current.navigation).toBeNull();
});

test("refresh removal disables neighbors without scanning unrelated pages", async () => {
  let rows = [{ id: "a" }, { id: "b" }];
  const f = fixture({ getPage: async () => rows });
  await waitFor(() => expect(f.result.current.navigation?.current).toBe(2));
  rows = [{ id: "a" }];
  await act(async () => { await f.client.invalidateQueries(); });
  await waitFor(() => expect(f.result.current.navigation?.current).toBeUndefined());
  expect(f.result.current.navigation?.onNext).toBeUndefined();
  expect(f.result.current.navigation?.onPrev).toBeUndefined();
  expect(f.getList.mock.calls.every(([params]) => params.pagination?.currentPage === 1)).toBe(true);
});

test("failed page transitions keep the current route and current authoritative page", async () => {
  const f = fixture({ getPage: async (page) => { if (page === 2) throw new Error("unavailable"); return [{ id: "a" }, { id: "b" }]; } });
  await waitFor(() => expect(f.result.current.navigation?.current).toBe(2));
  act(() => f.result.current.navigation?.onNext?.());
  await waitFor(() => expect(f.getList).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(f.result.current.navigation?.onNext).toBeTypeOf("function"));
  expect(f.result.current.id).toBe("b");
  expect(f.result.current.navigationScope).toEqual(scope);
});

test("local row models retain native table snapshots without invoking a server query", async () => {
  const f = fixture({ initialScope: null });
  const onSetPage = vi.fn();
  const onSelect = vi.fn();
  const local = renderHook(() => useListRecordNavigation<{ id: string }>({ resource: "notes.Note", recordId: "b", onSelect, onSetPage }), { wrapper: f.wrapper });
  const snapshot: ResourceListSnapshot<{ id: string }> = { rows: [{ id: "a" }, { id: "b" }], page: 1, pageSize: 2, total: 4, pageCount: 2, hasNext: true, hasPrev: false, fetching: false };
  act(() => local.result.current.onListStateChange(snapshot));
  expect(local.result.current.navigation?.current).toBe(2);
  act(() => local.result.current.navigation?.onNext?.());
  expect(onSetPage).toHaveBeenCalledWith(2);
  act(() => local.result.current.onListStateChange({ ...snapshot, rows: [{ id: "c" }, { id: "d" }], page: 2 }));
  await waitFor(() => expect(onSelect).toHaveBeenCalledWith("c"));
  expect(f.getList).not.toHaveBeenCalled();
});

test("a delayed page result cannot reopen a closed record or replace a different selection", async () => {
  let release!: (rows: { id: string }[]) => void;
  const delayed = new Promise<{ id: string }[]>((resolve) => { release = resolve; });
  const f = fixture({ getPage: async (page) => page === 2 ? delayed : [{ id: "a" }, { id: "b" }] });
  await waitFor(() => expect(f.result.current.navigation?.current).toBe(2));
  act(() => f.result.current.navigation?.onNext?.());
  await waitFor(() => expect(f.getList).toHaveBeenCalledTimes(2));
  act(() => f.result.current.setId(null));
  await act(async () => release([{ id: "c" }, { id: "d" }]));
  expect(f.onSelect).not.toHaveBeenCalled();
  expect(f.result.current.navigation).toBeNull();
  act(() => f.result.current.setId("a"));
  await waitFor(() => expect(f.result.current.navigation?.current).toBe(1));
  expect(f.result.current.id).toBe("a");
});

test.each(["filter", "pageSize", "error"] as const)("local pending page intent is cancelled by native %s changes", async (change) => {
  const f = fixture({ initialScope: null });
  const onSelect = vi.fn();
  const Wrapper = ({ children }: { children: React.ReactNode }) => <f.wrapper><ResourceViewProvider scope="local" initialState={{ pageSize: 2 }}>{children}</ResourceViewProvider></f.wrapper>;
  const local = renderHook(() => {
    const view = useResourceView();
    return { view, ...useListRecordNavigation<{ id: string }>({ resource: "notes.Note", recordId: "b", onSelect, onSetPage: view.setPage }) };
  }, { wrapper: Wrapper });
  const snapshot: ResourceListSnapshot<{ id: string }> = { rows: [{ id: "a" }, { id: "b" }], page: 1, pageSize: 2, total: 4, pageCount: 2, hasNext: true, hasPrev: false, fetching: false };
  act(() => local.result.current.onListStateChange(snapshot));
  act(() => local.result.current.navigation?.onNext?.());
  act(() => local.result.current.onListStateChange({ ...snapshot, page: 2, rows: [], fetching: true }));
  act(() => {
    if (change === "filter") local.result.current.view.setFilter({ title: { exact: "other" } });
    else if (change === "pageSize") local.result.current.view.setPageSize(20);
    else local.result.current.onListStateChange({ ...snapshot, page: 2, rows: [{ id: "c" }], error: new Error("failed") });
  });
  act(() => local.result.current.onListStateChange({ ...snapshot, page: 2, rows: [{ id: "c" }, { id: "d" }] }));
  expect(onSelect).not.toHaveBeenCalled();
  act(() => local.result.current.onListStateChange(snapshot));
  expect(local.result.current.navigation?.onPrev).toBeTypeOf("function");
});

test("captured query facts cannot cross model bindings", async () => {
  const f = fixture({ initialScope: null });
  const onSelect = vi.fn();
  const local = renderHook(({ model, id }: { model: string; id: string | null }) => useListRecordNavigation<{ id: string }>({ resource: model, recordId: id, onSelect }), { wrapper: f.wrapper, initialProps: { model: "notes.Note", id: null as string | null } });
  act(() => local.result.current.onListStateChange({ rows: [{ id: "a" }, { id: "b" }], total: 4, page: 1, pageSize: 2, pageCount: 2, hasNext: true, hasPrev: false, fetching: false, navigationScope: scope }));
  act(() => local.result.current.selectRecord("b"));
  local.rerender({ model: "notes.Note", id: "b" });
  await waitFor(() => expect(local.result.current.navigation?.current).toBe(2));
  local.rerender({ model: "notes.Other", id: "b" });
  expect(local.result.current.navigation).toBeNull();
  expect(local.result.current.navigationScope).toBeNull();
  expect(f.getList.mock.calls.every(([params]) => params.resource === "notes")).toBe(true);
});
