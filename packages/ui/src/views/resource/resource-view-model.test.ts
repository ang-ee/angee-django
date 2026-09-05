// @vitest-environment happy-dom

import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { ResourceViewProvider, useResourceView } from "./resource-view-context";
import { favoriteFromResourceView } from "./model/favorites";
import { useResourceViewQueryFacts } from "./surface/table-state";
import { initialResourceSorting } from "./resource-view-codecs";
import type { ResourceViewInitialState } from "./resource-view-model";
import { afterEach, describe, expect, test } from "vitest";

import {
  createResourceViewState,
  Filter,
  RESOURCE_VIEW_KINDS,
  RESOURCE_VIEW_KIND_CAPABILITIES,
  availableResourceViewKinds,
  resourceViewFavoritesFromJson,
  resourceViewKindCapabilities,
  resourceViewSearchToState,
  resourceViewStateToSearch,
  todayCalendarAnchor,
} from "./resource-view-model";

describe("resource-view model", () => {
  test("round-trips flat URL search state", () => {
    const state = createResourceViewState({
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
    expect((roundTrip.pagination.pageIndex + 1)).toBe(3);
    expect(roundTrip.pagination.pageSize).toBe(20);
    expect(roundTrip.sorting).toEqual([{ id: "updatedAt", desc: true }]);
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
    expect(roundTrip.rowSelection).toEqual({});
    expect(roundTrip.view).toBe("board");
  });

  test("omits default search values", () => {
    expect(resourceViewStateToSearch(createResourceViewState())).toEqual({});
  });

  test("preserves native declaration sorting, secondary fields, and explicit clears", () => {
    const initial = { sorting: initialResourceSorting(null, { updated_at: "DESC", id: "ASC" }) };
    const state = createResourceViewState(initial);
    expect(state.sorting).toEqual([{ id: "updated_at", desc: true }, { id: "id", desc: false }]);
    expect(resourceViewStateToSearch(state, initial)).toEqual({});
    expect(resourceViewSearchToState({}, initial).sorting).toEqual(state.sorting);
    const primaryOnly = { ...state, sorting: [{ id: "updated_at", desc: true }] };
    expect(resourceViewStateToSearch(primaryOnly, initial)).toEqual({ sort: "updated_at:desc" });
    expect(resourceViewSearchToState({ sort: "updated_at:desc" }, initial).sorting).toEqual(primaryOnly.sorting);
    expect(resourceViewSearchToState({ sort: "" }, initial).sorting).toEqual([]);
    expect(resourceViewStateToSearch({ ...state, sorting: [] }, initial)).toEqual({ sort: "" });
    expect(resourceViewStateToSearch(createResourceViewState({ sorting: [] }))).toEqual({ sort: "" });
    expect(createResourceViewState({ ...initial, sort: null }).sorting).toEqual([]);
  });

  test("serializes relative to page-owned default view and page size", () => {
    const defaults = { pageSize: 20, view: "board" as const };

    const defaultState = createResourceViewState(defaults);
    expect(resourceViewStateToSearch(defaultState, defaults)).toEqual({});

    const listState = createResourceViewState({ ...defaults, view: "list" });
    expect(resourceViewStateToSearch(listState, defaults)).toEqual({
      view: "list",
    });

    const resized = createResourceViewState({ ...defaults, pageSize: 50 });
    expect(resourceViewStateToSearch(resized, defaults)).toEqual({
      pageSize: 50,
    });
  });

  test("round-trips page one relative to a later initial page", () => {
    const initial = { page: 3 };
    const cleared = createResourceViewState({ page: 1 });
    const search = resourceViewStateToSearch(cleared, initial);

    expect(search).toEqual({ page: 1 });
    expect(resourceViewSearchToState(search, initial).pagination.pageIndex).toBe(0);
    expect(resourceViewStateToSearch(createResourceViewState(initial), initial)).toEqual({});
    expect(resourceViewSearchToState({}, initial).pagination.pageIndex).toBe(2);
  });

  test("round-trips cleared seeded filter, group, and sort through search", () => {
    const initial = {
      filter: { kind: { exact: "lead" } },
      group: { field: "stage" },
      sort: { field: "createdAt", dir: "desc" as const },
    };
    const cleared = createResourceViewState({ ...initial, filter: {}, groupStack: [], sort: null });

    const search = resourceViewStateToSearch(cleared, initial);

    expect(search).toMatchObject({
      filter: "",
      group: "",
      sort: "",
    });
    const roundTrip = resourceViewSearchToState(search, initial);
    expect(roundTrip.filter).toEqual({});
    expect(roundTrip.group).toBeNull();
    expect(roundTrip.groupStack).toEqual([]);
    expect(roundTrip.sorting).toEqual([]);
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

    expect((state.pagination.pageIndex + 1)).toBe(2);
    expect(state.pagination.pageSize).toBe(80);
    expect(state.group).toEqual({ field: "status", granularity: "year" });
    expect(state.groupStack).toEqual([
      { field: "status", granularity: "year" },
      { field: "updatedAt", granularity: "month" },
    ]);
    expect(state.sorting).toEqual([{ id: "title", desc: false }]);
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
      {
        id: "favorite:bad-sort",
        label: "Bad sort",
        sort: { field: "title", dir: "sideways" },
      },
    ]))).toEqual([{ id: "favorite:valid", label: "Valid" }]);
  });

  test("allocates stable favorite ids from labels", () => {
    const state = createResourceViewState();

    expect(favoriteFromResourceView(state, "Two per page").id).toBe("favorite:two-per-page");
    expect(favoriteFromResourceView(state, "Two per page", [
      { id: "favorite:two-per-page", label: "Two per page" },
      { id: "favorite:two-per-page-2", label: "Two per page" },
    ]).id).toBe("favorite:two-per-page-3");
    expect(favoriteFromResourceView(state, "   ").id).toBe("favorite:search");
  });

  test("round-trips groups with explicit aggregate axes", () => {
    const state = createResourceViewState({
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
    const { result } = viewHook({ page: 4, pageSize: 20, selectedIds: ["note-1"] });
    act(() => result.current.setSorting([{ id: "title", desc: false }]));
    expect(result.current.state.pagination.pageIndex).toBe(0);
    expect(result.current.state.rowSelection).toEqual({});
    act(() => result.current.setFilter({ title: { iContains: "beta" } }));
    expect(result.current.state.filter).toEqual({ title: { iContains: "beta" } });
    act(() => result.current.setPageSize(200));
    expect(result.current.state.pagination).toEqual({ pageIndex: 0, pageSize: 200 });
  });

  test("updates selection natively and retains it across page changes", () => {
    const { result } = viewHook();
    act(() => result.current.toggleSelectedId("note-1"));
    expect(result.current.state.rowSelection["note-1"]).toBe(true);
    act(() => result.current.setPage(2));
    expect(result.current.state.rowSelection["note-1"]).toBe(true);
    act(() => result.current.toggleSelectedId("note-1"));
    expect(result.current.state.rowSelection["note-1"]).toBe(false);
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
    const state = createResourceViewState({
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
    expect(resourceViewStateToSearch(createResourceViewState({ view: "calendar" })))
      .toEqual({ view: "calendar" });

    // A list state that happens to hold mode/anchor never serializes them.
    const listState = createResourceViewState({
      view: "list",
      mode: "week",
      anchor: "2026-06-15",
    });
    const listSearch = resourceViewStateToSearch(listState);
    expect("mode" in listSearch).toBe(false);
    expect("anchor" in listSearch).toBe(false);
  });

  test("round-trips month and today relative to custom calendar defaults", () => {
    const initial = { view: "calendar" as const, mode: "week" as const, anchor: "2000-01-01" };
    const today = todayCalendarAnchor();
    const cleared = createResourceViewState({ view: "calendar", mode: "month", anchor: today });
    const search = resourceViewStateToSearch(cleared, initial);

    expect(search).toEqual({ mode: "month", anchor: today });
    expect(resourceViewSearchToState(search, initial)).toMatchObject({ mode: "month", anchor: today });
    expect(resourceViewStateToSearch(createResourceViewState(initial), initial)).toEqual({});
    expect(resourceViewSearchToState({}, initial)).toMatchObject({ mode: "week", anchor: "2000-01-01" });
  });

  test("updates mode and anchor without disturbing list scope", () => {
    const { result } = viewHook({ view: "calendar", page: 3 });
    act(() => result.current.setMode("day"));
    expect(result.current.state.mode).toBe("day");
    expect(result.current.state.pagination.pageIndex).toBe(2);
    act(() => result.current.setAnchor("2026-07-01"));
    expect(result.current.state.anchor).toBe("2026-07-01");
    expect(result.current.state.pagination.pageIndex).toBe(2);
  });

  test("maps view sort onto Hasura resource order", () => {
    const { result } = renderHook(() => useResourceViewQueryFacts({
      columns: [], resourceView: useResourceView(), modelMetadata: null,
    }), { wrapper: viewWrapper({ sort: { field: "updatedAt", dir: "desc" } }) });
    expect(result.current.sortOrder).toEqual({ updatedAt: "DESC" });
  });

  test("local native sorting inherits a late declaration until a user explicitly clears it", () => {
    const { result, rerender } = renderHook(({ order }) => {
      const view = useResourceView();
      return { view, ...useResourceViewQueryFacts({ columns: [], resourceView: view, modelMetadata: null, order }) };
    }, {
      initialProps: { order: undefined as { updated_at: "DESC" } | undefined },
      wrapper: viewWrapper(),
    });
    expect(result.current.sortOrder).toBeUndefined();
    rerender({ order: { updated_at: "DESC" } });
    expect(result.current.sortOrder).toEqual({ updated_at: "DESC" });
    act(() => result.current.view.setSorting([]));
    expect(result.current.sortOrder).toEqual({});
    rerender({ order: undefined });
    rerender({ order: { updated_at: "DESC" } });
    expect(result.current.view.state.sorting).toEqual([]);
    expect(result.current.sortOrder).toEqual({});
  });
});


afterEach(cleanup);

function viewWrapper(initialState: ResourceViewInitialState = {}) {
  return ({ children }: { children: ReactNode }) => createElement(ResourceViewProvider, { scope: "local", initialState, children });
}

function viewHook(initialState: ResourceViewInitialState = {}) {
  return renderHook(useResourceView, { wrapper: viewWrapper(initialState) });
}
