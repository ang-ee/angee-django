import { describe, expect, test } from "vitest";

import {
  createDataViewState,
  dataViewReducer,
  dataViewSortToResourceOrder,
  dataViewStateToResourceListOptions,
  parseDataViewSearchParams,
  serializeDataViewState,
} from "./data-view-model";

describe("data-view model", () => {
  test("serializes URL state through nuqs-compatible parsers", () => {
    const state = createDataViewState({
      page: 3,
      pageSize: 20,
      sort: { field: "updatedAt", dir: "desc" },
      filter: { title: { iContains: "alpha" } },
      group: { field: "updatedAt", granularity: "day" },
      selection: ["note-1", "note-2"],
      view: "board",
    });

    const query = serializeDataViewState(state);
    const params = new URLSearchParams(query);

    expect(params.get("page")).toBe("3");
    expect(params.get("pageSize")).toBe("20");
    expect(params.get("sort")).toBe("updatedAt:desc");
    expect(params.get("filter")).toBe(
      JSON.stringify({ title: { iContains: "alpha" } }),
    );
    expect(params.get("group")).toBe("updatedAt:day");
    expect(params.get("selection")).toBe("note-1,note-2");
    expect(params.get("view")).toBe("board");

    const roundTrip = parseDataViewSearchParams(query);
    expect(roundTrip.page).toBe(3);
    expect(roundTrip.pageSize).toBe(20);
    expect(roundTrip.sort).toEqual({ field: "updatedAt", dir: "desc" });
    expect(roundTrip.filter).toEqual({ title: { iContains: "alpha" } });
    expect(roundTrip.group).toEqual({
      field: "updatedAt",
      granularity: "day",
    });
    expect([...roundTrip.selection]).toEqual(["note-1", "note-2"]);
    expect(roundTrip.view).toBe("board");
  });

  test("resets page and clears selection when query scope changes", () => {
    const state = createDataViewState({
      page: 4,
      pageSize: 20,
      selection: ["note-1"],
    });

    const sorted = dataViewReducer(state, {
      type: "setSort",
      sort: { field: "title", dir: "asc" },
    });
    expect(sorted.page).toBe(1);
    expect([...sorted.selection]).toEqual([]);

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

  test("updates selection as a set of stable ids", () => {
    const state = createDataViewState();

    const selected = dataViewReducer(state, {
      type: "toggleSelection",
      id: "note-1",
    });
    expect([...selected.selection]).toEqual(["note-1"]);

    const cleared = dataViewReducer(selected, {
      type: "toggleSelection",
      id: "note-1",
    });
    expect([...cleared.selection]).toEqual([]);
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
      initialPage: 2,
      filter: { title: { iContains: "alpha" } },
      order: { updatedAt: "DESC" },
      enabled: false,
    });
  });
});
