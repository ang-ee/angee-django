// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { createRefineTestProviders } from "../testing";
import { listQueryMeta } from "../list-query";
import { useAngeeListBatch, type AngeeListBatchScope, type ListBatchTarget } from "./hooks";

vi.mock("../list-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../list-query")>();
  return { ...actual, listQueryMeta: vi.fn(actual.listQueryMeta) };
});

const target: ListBatchTarget = {
  root: "notes", aggregateRoot: "notes_aggregate", filterType: "NoteBoolExp", orderType: "NoteOrderBy",
  resourceName: "notes", resourceIdentifier: "example.Note", dataProviderName: "console",
};
const { Provider, dataProvider, clients, clearClients } = createRefineTestProviders({
  providerNames: ["console"],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

function scopes(): AngeeListBatchScope[] {
  return [1, 2].map((page) => ({ key: String(page), page, pageSize: 10, where: { active: { _eq: true } }, orderBy: [{ id: "asc" }] }));
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

  test("a target declared afresh with the same names builds no request again", async () => {
    dataProvider.getList.mockResolvedValue({ data: [{ id: "note-a" }], total: 12 });
    const declared = scopes();
    const { result, rerender } = renderHook(() => useAngeeListBatch({ ...target }, declared, { fields: ["id"] }), { wrapper: Provider });
    await waitFor(() => expect([...result.current.values()].every((entry) => entry.total === 12 && !entry.fetching)).toBe(true));
    const built = vi.mocked(listQueryMeta).mock.calls.length;
    rerender();
    expect(vi.mocked(listQueryMeta).mock.calls.length).toBe(built);
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
    expect(result.current.get("2")).toBe(settled.get("2"));
    expect(dataProvider.getList).toHaveBeenCalledTimes(3);
  });

  test("a page inserted at the front or removed from the middle keeps every other page's entry", async () => {
    dataProvider.getList.mockImplementation(async ({ pagination }: { pagination?: { currentPage?: number } }) =>
      ({ data: [{ id: `note-${pagination?.currentPage}` }], total: 40 }));
    const page = (index: number): AngeeListBatchScope =>
      ({ key: String(index), page: index, pageSize: 10, where: { active: { _eq: true } }, orderBy: [{ id: "asc" }] });
    const { result, rerender } = renderHook(
      ({ pages }) => useAngeeListBatch(target, pages.map(page), { fields: ["id"] }),
      { initialProps: { pages: [2, 3, 4] }, wrapper: Provider },
    );
    await waitFor(() => expect([...result.current.values()].every((entry) => entry.total === 40 && !entry.fetching)).toBe(true));
    const settled = result.current;
    rerender({ pages: [1, 2, 3, 4] });
    for (const key of ["2", "3", "4"]) expect(result.current.get(key)).toBe(settled.get(key));
    rerender({ pages: [2, 4] });
    expect([...result.current.keys()]).toEqual(["2", "4"]);
    for (const key of ["2", "4"]) expect(result.current.get(key)).toBe(settled.get(key));
  });

  test("a batch of one page keyed with the empty string, then disabled, holds no page", async () => {
    dataProvider.getList.mockResolvedValue({ data: [{ id: "note-a" }], total: 1 });
    const only = [{ ...scopes()[0]!, key: "" }];
    const { result, rerender } = renderHook(
      ({ enabled }) => useAngeeListBatch(target, only, { fields: ["id"], enabled }),
      { initialProps: { enabled: true }, wrapper: Provider },
    );
    await waitFor(() => expect(result.current.get("")?.total).toBe(1));
    rerender({ enabled: false });
    expect(result.current.size).toBe(0);
  });

  test("the render that enables the batch reports its pages fetching", () => {
    dataProvider.getList.mockReturnValue(new Promise(() => undefined));
    const seen: (boolean | undefined)[] = [];
    const { rerender } = renderHook(({ enabled }) => {
      seen.push(useAngeeListBatch(target, scopes(), { fields: ["id"], enabled }).get("1")?.fetching);
    }, { initialProps: { enabled: false }, wrapper: Provider });
    expect(seen.every((fetching) => fetching === undefined)).toBe(true);
    const enabledAt = seen.length;
    rerender({ enabled: true });
    expect(seen[enabledAt]).toBe(true);
  });

  test("the render after a page's cache entry is removed reports it fetching", async () => {
    dataProvider.getList
      .mockResolvedValueOnce({ data: [{ id: "note-a" }], total: 1 })
      .mockReturnValue(new Promise(() => undefined));
    const seen: { rows: readonly unknown[] | undefined; fetching: boolean | undefined }[] = [];
    const { rerender } = renderHook(({ tick }) => {
      const page = useAngeeListBatch(target, [scopes()[0]!], { fields: ["id"] }).get("1");
      seen.push({ rows: page?.rows, fetching: page?.fetching });
      return tick;
    }, { initialProps: { tick: 0 }, wrapper: Provider });
    await waitFor(() => expect(seen.at(-1)).toEqual({ rows: [{ id: "note-a" }], fetching: false }));
    act(() => { clients.at(-1)!.removeQueries(); });
    const removedAt = seen.length;
    rerender({ tick: 1 });
    expect(seen[removedAt]).toEqual({ rows: [], fetching: true });
  });

  test("two labels on one request share one observer and one entry", async () => {
    dataProvider.getList.mockResolvedValue({ data: [{ id: "note-a" }], total: 1 });
    const warn = vi.spyOn(console, "warn");
    try {
      const { result } = renderHook(() => {
        const [first] = scopes();
        return useAngeeListBatch(target, [first!, { ...first!, key: "again" }], { fields: ["id"] });
      }, { wrapper: Provider });
      await waitFor(() => expect(result.current.get("1")?.total).toBe(1));
      expect(result.current.get("again")).toBe(result.current.get("1"));
      const pages = clients.at(-1)!.getQueryCache().findAll({ predicate: (query) => query.queryHash.includes('"list"') });
      expect(pages.map((query) => query.getObserversCount())).toEqual([1]);
      expect(dataProvider.getList).toHaveBeenCalledTimes(1);
      expect(warn.mock.calls.flat().some((message) => String(message).includes("Duplicate Queries"))).toBe(false);
    } finally {
      warn.mockRestore();
    }
  });

  test("changing the fields asks for each page again, under its new request", async () => {
    dataProvider.getList.mockResolvedValue({ data: [{ id: "note-a" }], total: 12 });
    const { result, rerender } = renderHook(
      ({ fields }) => useAngeeListBatch(target, scopes(), { fields }),
      { initialProps: { fields: ["id"] }, wrapper: Provider },
    );
    await waitFor(() => expect([...result.current.values()].every((entry) => entry.total === 12 && !entry.fetching)).toBe(true));
    const settled = result.current;
    rerender({ fields: ["id", "title"] });
    expect(result.current.get("1")).not.toBe(settled.get("1"));
    await waitFor(() => expect(dataProvider.getList).toHaveBeenCalledTimes(4));
    const pages = clients.at(-1)!.getQueryCache().findAll({ predicate: (query) => query.queryHash.includes('"list"') });
    expect(pages).toHaveLength(4);
    expect(pages.filter((query) => query.getObserversCount() > 0)).toHaveLength(2);
  });

  test("the same keys in another priority are another request, read again", async () => {
    dataProvider.getList.mockResolvedValue({ data: [{ id: "note-a" }], total: 1 });
    const page = (orderBy: AngeeListBatchScope["orderBy"]): AngeeListBatchScope[] =>
      [{ key: "1", page: 1, pageSize: 10, where: undefined, orderBy }];
    const { result, rerender } = renderHook(
      ({ orderBy }) => useAngeeListBatch(target, page(orderBy), { fields: ["id"] }),
      { initialProps: { orderBy: [{ due: "desc" }, { title: "asc" }] as AngeeListBatchScope["orderBy"] }, wrapper: Provider },
    );
    await waitFor(() => expect(result.current.get("1")?.total).toBe(1));
    rerender({ orderBy: [{ title: "asc" }, { due: "desc" }] });
    await waitFor(() => expect(dataProvider.getList).toHaveBeenCalledTimes(2));
    expect(dataProvider.getList.mock.calls.map(([params]) => params.meta?.gqlVariables?.order_by))
      .toEqual([[{ due: "desc" }, { title: "asc" }], [{ title: "asc" }, { due: "desc" }]]);
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
