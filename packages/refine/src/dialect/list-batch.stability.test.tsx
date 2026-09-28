// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { createRefineTestProviders } from "../testing";
import { useAngeeListBatch, type AngeeListBatchScope, type ListBatchTarget } from "./hooks";

const target: ListBatchTarget = {
  root: "notes", aggregateRoot: "notes_aggregate", filterType: "NoteBoolExp", orderType: "NoteOrderBy",
  resourceName: "notes", resourceIdentifier: "example.Note", dataProviderName: "console",
};
const { Provider, dataProvider, clearClients } = createRefineTestProviders({
  providerNames: ["console"],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

function scopes(): AngeeListBatchScope[] {
  return [1, 2].map((page) => ({ key: String(page), page, pageSize: 10, where: { active: { _eq: true } }, orderBy: { id: "asc" } }));
}

describe("native list batch structural sharing", () => {
  test("keeps the Map and settled entries stable across equivalent caller declarations", async () => {
    dataProvider.getList.mockResolvedValue({ data: [{ id: "note-a" }], total: 12 });
    const { result, rerender } = renderHook(() => useAngeeListBatch({ ...target }, scopes(), { fields: ["id"] }), { wrapper: Provider });
    await waitFor(() => {
      expect(result.current.size).toBe(2);
      expect([...result.current.values()].every((entry) => !entry.fetching && entry.total === 12)).toBe(true);
    });
    const settled = result.current;
    rerender();
    expect(result.current).toBe(settled);
    expect(result.current.get("1")).toBe(settled.get("1"));
    expect(result.current.get("1")?.rows).toBe(settled.get("1")?.rows);
    expect(result.current.get("1")?.refetch).toBe(settled.get("1")?.refetch);
    expect(dataProvider.getList).toHaveBeenCalledTimes(2);
  });

  test("updates changed data through refetch without issuing a second sibling read", async () => {
    dataProvider.getList.mockResolvedValue({ data: [{ id: "note-a" }], total: 12 });
    const { result } = renderHook(() => useAngeeListBatch(target, scopes(), { fields: ["id"] }), { wrapper: Provider });
    await waitFor(() => expect([...result.current.values()].every((entry) => entry.total === 12 && !entry.fetching)).toBe(true));
    const settled = result.current;
    const siblingRows = settled.get("2")?.rows;
    dataProvider.getList.mockResolvedValueOnce({ data: [{ id: "note-b" }], total: 13 });
    act(() => result.current.get("1")?.refetch());
    await waitFor(() => expect(result.current.get("1")?.rows).toEqual([{ id: "note-b" }]));
    expect(result.current).not.toBe(settled);
    expect(result.current.get("1")?.total).toBe(13);
    expect(result.current.get("2")?.rows).toBe(siblingRows);
    expect(dataProvider.getList).toHaveBeenCalledTimes(3);
  });

  test("returns an empty stable Map when disabled and makes no requests", () => {
    const { result, rerender } = renderHook(() => useAngeeListBatch(target, scopes(), { fields: ["id"], enabled: false }), { wrapper: Provider });
    const empty = result.current;
    rerender();
    expect(result.current).toBe(empty);
    expect(result.current.size).toBe(0);
    expect(dataProvider.getList).not.toHaveBeenCalled();
  });
});
