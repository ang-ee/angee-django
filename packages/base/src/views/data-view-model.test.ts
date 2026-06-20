import { describe, expect, test } from "vitest";

import {
  DataViewState,
  Filter,
  dataViewFavoritesFromJson,
  dataViewSearchToState,
  dataViewStateToSearch,
} from "./data-view-model";

describe("data-view model", () => {
  test("round-trips flat URL search state", () => {
    const state = DataViewState.create({
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
    expect(dataViewStateToSearch(DataViewState.create())).toEqual({});
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

  test("decodes saved favorites from persisted JSON", () => {
    const raw = JSON.stringify([
      { id: "favorite:open", label: "Open" },
      { id: "favorite:closed", label: "Closed", pageSize: 20 },
    ]);

    expect(dataViewFavoritesFromJson(raw)).toEqual([
      { id: "favorite:open", label: "Open" },
      { id: "favorite:closed", label: "Closed", pageSize: 20 },
    ]);
    expect(dataViewFavoritesFromJson("{")).toEqual([]);
    expect(dataViewFavoritesFromJson(JSON.stringify([
      { id: "favorite:valid", label: "Valid" },
      { id: 123, label: "Invalid" },
      { id: "favorite:missing-label" },
    ]))).toEqual([{ id: "favorite:valid", label: "Valid" }]);
  });

  test("allocates stable favorite ids from labels", () => {
    const state = DataViewState.create();

    expect(state.toFavorite("Two per page").id).toBe("favorite:two-per-page");
    expect(state.toFavorite("Two per page", [
      { id: "favorite:two-per-page", label: "Two per page" },
      { id: "favorite:two-per-page-2", label: "Two per page" },
    ]).id).toBe("favorite:two-per-page-3");
    expect(state.toFavorite("   ").id).toBe("favorite:search");
  });

  test("round-trips groups with explicit aggregate axes", () => {
    const state = DataViewState.create({
      groupStack: [
        {
          field: "vendorLabel",
          aggregateField: "vendor",
          aggregateKey: "vendorId",
        },
      ],
    });

    const search = dataViewStateToSearch(state);

    expect(search.group).toBe("vendorLabel~vendor~vendorId");
    expect(dataViewSearchToState(search).group).toEqual({
      field: "vendorLabel",
      aggregateField: "vendor",
      aggregateKey: "vendorId",
    });
  });

  test("toggles lookup facets as exact/in-list lookups", () => {
    const selected = Filter.from({}).toggleFacet({
      field: "providerId",
      value: "provider-a",
      mode: "lookup",
    });
    const expanded = Filter.from(selected).toggleFacet({
      field: "providerId",
      value: "provider-b",
      mode: "lookup",
    });

    expect(selected).toEqual({ providerId: { exact: "provider-a" } });
    expect(expanded).toEqual({ providerId: { inList: ["provider-a", "provider-b"] } });
    expect(Filter.from(expanded).facetValues({
      field: "providerId",
      value: "provider-a",
      mode: "lookup",
    })).toEqual(["provider-a", "provider-b"]);
  });

  test("toggles public-id relation facets as single lookup filters", () => {
    const facet = Filter.facetFromFilter({
      provider: { sqid: "provider-a" },
    });

    expect(facet).toEqual({
      field: "provider",
      value: "provider-a",
      mode: "lookup",
      lookup: "sqid",
    });

    const selected = Filter.from({}).toggleFacet(facet!);
    const replaced = Filter.from(selected).toggleFacet({
      ...facet!,
      value: "provider-b",
    });
    const cleared = Filter.from(replaced).toggleFacet({
      ...facet!,
      value: "provider-b",
    });

    expect(selected).toEqual({ provider: { sqid: "provider-a" } });
    expect(Filter.from(selected).facetValues(facet!)).toEqual(["provider-a"]);
    expect(replaced).toEqual({ provider: { sqid: "provider-b" } });
    expect(cleared).toEqual({});
  });

  test("toggles direct id facets as scalar filters", () => {
    const facet = {
      field: "publisher",
      value: "publisher-a",
      mode: "id" as const,
    };
    const selected = Filter.from({}).toggleFacet(facet);
    const cleared = Filter.from(selected).toggleFacet(facet);

    expect(selected).toEqual({ publisher: "publisher-a" });
    expect(Filter.from(selected).facetValues(facet)).toEqual(["publisher-a"]);
    expect(cleared).toEqual({});
  });

  test("combines filters without duplicating equivalent constraints", () => {
    const filter = Filter.combine(
      { status: { exact: "ACTIVE" } },
      { status: { exact: "ACTIVE" }, owner: { sqid: "usr_1" } },
    );

    expect(filter).toEqual({
      status: { exact: "ACTIVE" },
      owner: { sqid: "usr_1" },
    });
  });

  test("keeps conflicting filter constraints under object-shaped AND", () => {
    const filter = Filter.combine(
      { updatedAt: { gte: "2026-01-01" } },
      { updatedAt: { exact: "2026-01-20" }, status: { exact: "ACTIVE" } },
    );

    expect(Array.isArray(filter.AND)).toBe(false);
    expect(filter).toEqual({
      updatedAt: { gte: "2026-01-01" },
      status: { exact: "ACTIVE" },
      AND: { updatedAt: { exact: "2026-01-20" } },
    });
  });

  test("combines conflicts into an existing AND branch", () => {
    const filter = Filter.combine(
      {
        updatedAt: { gte: "2026-01-01" },
        AND: { updatedAt: { lte: "2026-01-31" } },
      },
      { updatedAt: { exact: "2026-01-20" } },
    );

    expect(filter).toEqual({
      updatedAt: { gte: "2026-01-01" },
      AND: {
        updatedAt: { lte: "2026-01-31" },
        AND: { updatedAt: { exact: "2026-01-20" } },
      },
    });
  });

  test("resets page and clears selection when query scope changes", () => {
    const state = DataViewState.create({
      page: 4,
      pageSize: 20,
      selectedIds: ["note-1"],
    });

    const sorted = state.reduce({
      type: "setSort",
      sort: { field: "title", dir: "asc" },
    });
    expect(sorted.page).toBe(1);
    expect([...sorted.selectedIds]).toEqual([]);

    const filtered = sorted.reduce({
      type: "setFilter",
      filter: { title: { iContains: "beta" } },
    });
    expect(filtered.page).toBe(1);
    expect(filtered.filter).toEqual({ title: { iContains: "beta" } });

    const resized = filtered.reduce({
      type: "setPageSize",
      pageSize: 500,
    });
    expect(resized.pageSize).toBe(100);
    expect(resized.page).toBe(1);
  });

  test("updates selected ids as local row state", () => {
    const state = DataViewState.create();

    const selected = state.reduce({
      type: "toggleSelectedId",
      id: "note-1",
    });
    expect([...selected.selectedIds]).toEqual(["note-1"]);

    const cleared = selected.reduce({
      type: "toggleSelectedId",
      id: "note-1",
    });
    expect([...cleared.selectedIds]).toEqual([]);
  });

  test("maps view sort onto SDK resource order", () => {
    const state = DataViewState.create({
      page: 2,
      pageSize: 20,
      sort: { field: "updatedAt", dir: "desc" },
      filter: { title: { iContains: "alpha" } },
    });

    expect(state.resourceOrder()).toEqual({
      updatedAt: "DESC",
    });
  });
});
