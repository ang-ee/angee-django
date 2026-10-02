// @vitest-environment happy-dom

import { ResourceQuery } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { createUiTestProviders } from "../../testing";
import { useRelationOptions } from "./relation-options";

const resource = testDataResource("example.Lane", {
  recordRepresentation: "name",
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, name: { scalar: "String" }, code: { scalar: "String" } } }).contract,
});
const { Provider, dataProvider, clearClients } = createUiTestProviders({
  resources: [resource],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
const relation = { resource: resource.modelLabel, labelField: "name", canCreate: false };
afterEach(() => { cleanup(); clearClients(); });

describe("relation catalogue paging", () => {
  test("uses one-based server pages and returns the server total rather than page length", async () => {
    dataProvider.getList.mockResolvedValueOnce({ data: [{ id: "lane-a", name: "Alpha", code: "A" }], total: 21 })
      .mockResolvedValueOnce({ data: [{ id: "lane-b", name: "Beta", code: "B" }], total: 21 });
    const { result, rerender } = renderHook(({ page }) => useRelationOptions(relation, { page, pageSize: 10, fields: ["code"] }), {
      wrapper: Provider, initialProps: { page: 1 },
    });
    await waitFor(() => expect(result.current.options).toEqual([{ value: "lane-a", label: "Alpha" }]));
    expect(result.current.list.total).toBe(21);
    expect(dataProvider.getList).toHaveBeenLastCalledWith(expect.objectContaining({
      resource: "lanes", pagination: { mode: "server", currentPage: 1, pageSize: 10 },
      meta: expect.objectContaining({ fields: ["id", "name", "code"] }),
    }));
    rerender({ page: 2 });
    await waitFor(() => expect(result.current.options).toEqual([{ value: "lane-b", label: "Beta" }]));
    expect(result.current.list.total).toBe(21);
    expect(dataProvider.getList).toHaveBeenLastCalledWith(expect.objectContaining({
      pagination: { mode: "server", currentPage: 2, pageSize: 10 },
    }));
    expect(dataProvider.getList).toHaveBeenCalledTimes(2);
  });

  test("retains result, list, options, rows and refetch references for value-equal inputs", async () => {
    dataProvider.getList.mockResolvedValue({ data: [{ id: "lane-a", name: "Alpha", code: "A" }], total: 1 });
    const { result, rerender } = renderHook(() => useRelationOptions(relation, {
      fields: ["code"], filters: [{ field: "code", operator: "eq", value: "A" }],
      sorters: [{ field: "name", order: "asc" }], page: 1, pageSize: 10,
    }), { wrapper: Provider });
    await waitFor(() => expect(result.current.list.fetching).toBe(false));
    await waitFor(() => expect(result.current.options).toHaveLength(1));
    const settled = result.current;
    rerender();
    expect(result.current).toBe(settled);
    expect(result.current.list).toBe(settled.list);
    expect(result.current.options).toBe(settled.options);
    expect(result.current.rows).toBe(settled.rows);
    expect(result.current.list.refetch).toBe(settled.list.refetch);
    expect(dataProvider.getList).toHaveBeenCalledTimes(1);
  });

  test("does not fetch a disabled catalogue and preserves a zero total", async () => {
    dataProvider.getList.mockResolvedValue({ data: [], total: 0 });
    const { result, rerender } = renderHook(({ enabled }) => useRelationOptions(relation, { enabled }), {
      wrapper: Provider, initialProps: { enabled: false },
    });
    expect(dataProvider.getList).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.list.total).toBe(0));
    expect(result.current.rows).toEqual([]);
    expect(result.current.options).toEqual([]);
    expect(dataProvider.getList).toHaveBeenCalledTimes(1);
  });
});
