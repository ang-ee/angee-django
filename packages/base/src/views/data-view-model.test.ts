import { describe, expect, test } from "vitest";

import {
  createDataViewState,
  dataViewReducer,
  dataViewSearchToState,
  dataViewSortToResourceOrder,
  dataViewStateToSearch,
  dataViewStateToResourceListOptions,
} from "./data-view-model";

describe("data-view model", () => {
  test("round-trips flat URL search state", () => {
    const state = createDataViewState({
      page: 3,
      pageSize: 20,
      sort: { field: "updatedAt", dir: "desc" },
      filter: { title: { iContains: "alpha" } },
      groupStack: [
        { field: "status", granularity: "year" },
        { field: "updatedAt", granularity: "month" },
        { field: "owner" },
      ],
      selectedIds: ["note-1", "note-2"],
      view: "board",
    });

    const search = dataViewStateToSearch(state);

    expect(search.page).toBe(3);
    expect(search.pageSize).toBe(20);
    expect(search.sort).toBe("updatedAt:desc");
    expect(search.filter).toBe(
      JSON.stringify({ title: { iContains: "alpha" } }),
    );
    expect(search.group).toBe("status:year");
    expect(search.then).toBe("updatedAt:month,owner");
    expect("selectedIds" in search).toBe(false);
    expect("selection" in search).toBe(false);
    expect(search.view).toBe("board");

    const roundTrip = dataViewSearchToState(search);
    expect(roundTrip.page).toBe(3);
    expect(roundTrip.pageSize).toBe(20);
    expect(roundTrip.sort).toEqual({ field: "updatedAt", dir: "desc" });
    expect(roundTrip.filter).toEqual({ title: { iContains: "alpha" } });
    expect(roundTrip.group).toEqual({
      field: "status",
      granularity: "year",
    });
    expect(roundTrip.groupStack).toEqual([
      { field: "status", granularity: "year" },
      { field: "updatedAt", granularity: "month" },
      { field: "owner" },
    ]);
    expect([...roundTrip.selectedIds]).toEqual([]);
    expect(roundTrip.view).toBe("board");
  });

  test("omits default search values", () => {
    expect(dataViewStateToSearch(createDataViewState())).toEqual({});
  });

  test("parses Router search strings without JSON-quoting URL values", () => {
    const state = dataViewSearchToState({
      page: "2",
      pageSize: "80",
      group: "status:year",
      then: "updatedAt:month",
      sort: "title:asc",
      filter: JSON.stringify({ status: { exact: "ACTIVE" } }),
      view: "board",
    });

    expect(state.page).toBe(2);
    expect(state.pageSize).toBe(80);
    expect(state.group).toEqual({ field: "status", granularity: "year" });
    expect(state.groupStack).toEqual([
      { field: "status", granularity: "year" },
      { field: "updatedAt", granularity: "month" },
    ]);
    expect(state.sort).toEqual({ field: "title", dir: "asc" });
    expect(state.filter).toEqual({ status: { exact: "ACTIVE" } });
    expect(state.view).toBe("board");
  });

  test("resets page and clears selection when query scope changes", () => {
    const state = createDataViewState({
      page: 4,
      pageSize: 20,
      selectedIds: ["note-1"],
    });

    const sorted = dataViewReducer(state, {
      type: "setSort",
      sort: { field: "title", dir: "asc" },
    });
    expect(sorted.page).toBe(1);
    expect([...sorted.selectedIds]).toEqual([]);

    const filtered = dataViewReducer(sorted, {
      type: "setFilter",
      filter: { title: { iContains: "beta" } },
    });
    expect(filtered.page).toBe(1);
    expect(filtered.filter).toEqual({ title: { iContains: "beta" } });

    const resized = dataViewReducer(filtered, {
      type: "setPageSize",
      pageSize: 500,
    });
    expect(resized.pageSize).toBe(100);
    expect(resized.page).toBe(1);
  });

  test("updates selected ids as local row state", () => {
    const state = createDataViewState();

    const selected = dataViewReducer(state, {
      type: "toggleSelectedId",
      id: "note-1",
    });
    expect([...selected.selectedIds]).toEqual(["note-1"]);

    const cleared = dataViewReducer(selected, {
      type: "toggleSelectedId",
      id: "note-1",
    });
    expect([...cleared.selectedIds]).toEqual([]);
  });

  test("maps view state onto SDK offset list options", () => {
    const state = createDataViewState({
      page: 2,
      pageSize: 20,
      sort: { field: "updatedAt", dir: "desc" },
      filter: { title: { iContains: "alpha" } },
    });

    expect(dataViewSortToResourceOrder(state.sort)).toEqual({
      updatedAt: "DESC",
    });
    expect(
      dataViewStateToResourceListOptions(state, {
        fields: ["id", "title"],
        enabled: false,
      }),
    ).toEqual({
      fields: ["id", "title"],
      pageSize: 20,
      page: 2,
      filter: { title: { iContains: "alpha" } },
      order: { updatedAt: "DESC" },
      enabled: false,
    });
  });
});
