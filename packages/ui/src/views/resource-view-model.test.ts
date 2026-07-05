import { describe, expect, test } from "vitest";

import {
  ResourceViewState,
  Filter,
  RESOURCE_VIEW_KINDS,
  RESOURCE_VIEW_KIND_CAPABILITIES,
  availableResourceViewKinds,
  resourceViewFavoritesFromJson,
  resourceViewKindCapabilities,
  resourceViewSearchToState,
  resourceViewStateToSearch,
} from "./resource-view-model";

describe("resource-view model", () => {
  test("round-trips flat URL search state", () => {
    const state = ResourceViewState.create({
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

    const search = resourceViewStateToSearch(state);

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

    const roundTrip = resourceViewSearchToState(search);
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
    expect(resourceViewStateToSearch(ResourceViewState.create())).toEqual({});
  });

  test("serializes relative to page-owned default view and page size", () => {
    const defaults = { pageSize: 20, view: "board" as const };

    const defaultState = ResourceViewState.create(defaults);
    expect(resourceViewStateToSearch(defaultState, defaults)).toEqual({});

    const listState = defaultState.reduce({ type: "setView", view: "list" });
    expect(resourceViewStateToSearch(listState, defaults)).toEqual({
      view: "list",
    });

    const resized = defaultState.reduce({ type: "setPageSize", pageSize: 50 });
    expect(resourceViewStateToSearch(resized, defaults)).toEqual({
      pageSize: 50,
    });
  });

  test("parses Router search strings without JSON-quoting URL values", () => {
    const state = resourceViewSearchToState({
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

    expect(resourceViewFavoritesFromJson(raw)).toEqual([
      { id: "favorite:open", label: "Open" },
      { id: "favorite:closed", label: "Closed", pageSize: 20 },
    ]);
    expect(resourceViewFavoritesFromJson("{")).toEqual([]);
    expect(resourceViewFavoritesFromJson(JSON.stringify([
      { id: "favorite:valid", label: "Valid" },
      { id: 123, label: "Invalid" },
      { id: "favorite:missing-label" },
    ]))).toEqual([{ id: "favorite:valid", label: "Valid" }]);
  });

  test("allocates stable favorite ids from labels", () => {
    const state = ResourceViewState.create();

    expect(state.toFavorite("Two per page").id).toBe("favorite:two-per-page");
    expect(state.toFavorite("Two per page", [
      { id: "favorite:two-per-page", label: "Two per page" },
      { id: "favorite:two-per-page-2", label: "Two per page" },
    ]).id).toBe("favorite:two-per-page-3");
    expect(state.toFavorite("   ").id).toBe("favorite:search");
  });

  test("round-trips groups with explicit aggregate axes", () => {
    const state = ResourceViewState.create({
      groupStack: [
        {
          field: "vendor.displayName",
          aggregateField: "vendor",
          aggregateKey: "vendorId",
        },
      ],
    });

    const search = resourceViewStateToSearch(state);

    expect(search.group).toBe("vendor.displayName~vendor~vendorId");
    expect(resourceViewSearchToState(search).group).toEqual({
      field: "vendor.displayName",
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

  test("returns no optional filter when both sides are empty", () => {
    expect(Filter.combineOptional(undefined, {})).toBeUndefined();
  });

  test("keeps optional conflicting filters under object-shaped AND", () => {
    expect(Filter.combineOptional(
      { status: { exact: "ACTIVE" } },
      { status: { exact: "DRAFT" } },
    )).toEqual({
      status: { exact: "ACTIVE" },
      AND: { status: { exact: "DRAFT" } },
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

  test("removes facet fields from nested filter controls", () => {
    const filter = Filter.from({
      provider: { sqid: "provider-a" },
      status: { exact: "ACTIVE" },
      AND: {
        provider: { sqid: "provider-b" },
        title: { iContains: "launch" },
      },
      OR: [
        { provider: { sqid: "provider-c" } },
        { status: { exact: "ARCHIVED" } },
      ],
      not: { provider: { sqid: "provider-d" } },
    }).withoutFields(["provider"]);

    expect(filter).toEqual({
      status: { exact: "ACTIVE" },
      AND: { title: { iContains: "launch" } },
      OR: [{ status: { exact: "ARCHIVED" } }],
    });
  });

  test("resets page and clears selection when query scope changes", () => {
    const state = ResourceViewState.create({
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
    const state = ResourceViewState.create();

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

  test("registers the calendar kind with its applicability", () => {
    expect(RESOURCE_VIEW_KINDS).toEqual(["list", "board", "calendar"]);
    // The calendar takes only window args in v1: no group-by/pager/columns/filter.
    expect(RESOURCE_VIEW_KIND_CAPABILITIES.calendar).toEqual({
      grouping: false,
      pagination: false,
      columns: false,
      filter: false,
      requiresSources: true,
    });
    // list/board applicability is unchanged (both keep filter + pager + group-by).
    expect(RESOURCE_VIEW_KIND_CAPABILITIES.list.filter).toBe(true);
    expect(RESOURCE_VIEW_KIND_CAPABILITIES.list.pagination).toBe(true);
    expect(RESOURCE_VIEW_KIND_CAPABILITIES.board.filter).toBe(true);
    expect(RESOURCE_VIEW_KIND_CAPABILITIES.board.pagination).toBe(true);
    // A surface that names no kind keeps every control applicable.
    expect(resourceViewKindCapabilities(undefined)).toEqual({
      grouping: true,
      pagination: true,
      columns: true,
      filter: true,
    });
  });

  test("offers the calendar kind only where sources are declared", () => {
    expect(availableResourceViewKinds()).toEqual(["list", "board"]);
    expect(availableResourceViewKinds({ calendar: false })).toEqual(["list", "board"]);
    expect(availableResourceViewKinds({ calendar: true })).toEqual([
      "list",
      "board",
      "calendar",
    ]);
  });

  test("round-trips calendar mode + anchor through the family codec", () => {
    const state = ResourceViewState.create({
      view: "calendar",
      mode: "week",
      anchor: "2026-06-15",
    });

    const search = resourceViewStateToSearch(state);
    expect(search).toMatchObject({
      view: "calendar",
      mode: "week",
      anchor: "2026-06-15",
    });

    const roundTrip = resourceViewSearchToState(search);
    expect(roundTrip.view).toBe("calendar");
    expect(roundTrip.mode).toBe("week");
    expect(roundTrip.anchor).toBe("2026-06-15");

    // Router-string parse (not JSON-quoted) restores the same view.
    const parsed = resourceViewSearchToState({
      view: "calendar",
      mode: "day",
      anchor: "2026-06-15",
    });
    expect(parsed.mode).toBe("day");
    expect(parsed.anchor).toBe("2026-06-15");
  });

  test("serializes mode/anchor only under the calendar kind", () => {
    // Defaults (month + today) are omitted even under the calendar kind.
    expect(resourceViewStateToSearch(ResourceViewState.create({ view: "calendar" })))
      .toEqual({ view: "calendar" });

    // A list state that happens to hold mode/anchor never serializes them.
    const listState = ResourceViewState.create({
      view: "list",
      mode: "week",
      anchor: "2026-06-15",
    });
    const listSearch = resourceViewStateToSearch(listState);
    expect("mode" in listSearch).toBe(false);
    expect("anchor" in listSearch).toBe(false);
  });

  test("reduces setMode and setAnchor without disturbing list scope", () => {
    const base = ResourceViewState.create({ view: "calendar", page: 3 });

    const day = base.reduce({ type: "setMode", mode: "day" });
    expect(day.mode).toBe("day");
    expect(day.page).toBe(3);

    const moved = base.reduce({ type: "setAnchor", anchor: "2026-07-01" });
    expect(moved.anchor).toBe("2026-07-01");
    expect(moved.page).toBe(3);
  });

  test("maps view sort onto Hasura resource order", () => {
    const state = ResourceViewState.create({
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
