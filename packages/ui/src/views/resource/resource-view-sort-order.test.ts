// @vitest-environment happy-dom

import { createElement, type ReactNode } from "react";
import { act, renderHook } from "@testing-library/react";
import { ResourceQuery } from "@angee/metadata";
import type { SortingState } from "@tanstack/react-table";
import { describe, expect, test } from "vitest";

import { ResourceViewProvider, useResourceView } from "./resource-view-context";
import { favoriteFromResourceView, resourceViewFavoriteIsActive, resourceViewFavoritesFromUnknown } from "./model/favorites";
import { validateResourceViewState } from "./model/state";
import {
  createResourceViewState,
  mergeResourceViewSearch,
  resourceViewSearchToState,
  resourceViewStateToSearch,
  type ResourceViewInitialState,
} from "./resource-view-model";

const order = (...keys: readonly string[]): SortingState => keys.map((key) => {
  const [id, dir] = key.split(" ");
  return { id: id!, desc: dir === "desc" };
});
/** The list's state with `sorting` over the list's declared defaults. */
const stateOver = (initial: ResourceViewInitialState, sorting: readonly SortingState[number][]) => ({ ...createResourceViewState(initial), sorting: [...sorting] });
const query = ResourceQuery.forRows({ fields: { status: {}, rank: { scalar: "Int" }, title: {} } });

describe("the URL keeps the whole order in its one `sort` key", () => {
  test.each([
    ["one key", {}, order("title asc"), "title:asc"],
    ["two keys", {}, order("rank desc", "title asc"), "rank:desc,title:asc"],
    ["a grouping key and the tie-breaker behind it", { groupStack: [{ field: "status" }], sorting: order("status asc", "rank asc") },
      order("status asc", "rank desc"), "status:asc,rank:desc"],
    ["cleared", { sorting: order("rank asc") }, [], ""],
  ] as const)("%s: state to URL to state", (_name, initial, sorting, spelled) => {
    const search = resourceViewStateToSearch(stateOver(initial, sorting), initial);
    expect(search.sort).toBe(spelled);
    expect(resourceViewSearchToState(search, initial).sorting).toEqual(sorting);
  });

  test("an order equal to the declared one writes nothing, and nothing reads as the declared order", () => {
    const initial = { sorting: order("status asc", "rank asc", "title asc") };
    expect(resourceViewStateToSearch(createResourceViewState(initial), initial)).toEqual({});
    expect(resourceViewSearchToState({}, initial).sorting).toEqual(initial.sorting);
  });

  test("a namespaced collection keeps its whole order under its own key", () => {
    const initial = { sorting: order("rank asc") };
    const next = mergeResourceViewSearch({ "s.sort": "title:asc" },
      resourceViewStateToSearch(stateOver(initial, order("status asc", "rank desc")), initial), "c");
    expect(next).toEqual({ "s.sort": "title:asc", "c.sort": "status:asc,rank:desc" });
    expect(resourceViewSearchToState(next, initial, "c").sorting).toEqual(order("status asc", "rank desc"));
    expect(resourceViewSearchToState(next, initial, "s").sorting).toEqual(order("title asc"));
  });

  test("an old one-key link reads as it always has", () => {
    expect(resourceViewSearchToState({ sort: "name:asc" }).sorting).toEqual(order("name asc"));
    expect(resourceViewSearchToState({ sort: "name:desc" }).sorting).toEqual(order("name desc"));
  });

  test.each(["rank:asc,", "rank:asc:title", ",rank:asc", "rank"])("the malformed list %j is refused when read", (sort) => {
    const state = resourceViewSearchToState({ sort });
    expect(state.queryError?.message).toBe("sort: expected a comma list of field:asc or field:desc");
  });

  test.each([
    ["a padded field", "status:asc, rank:desc", 'sort[1].field: field " rank" cannot be sorted'],
    ["a duplicate field", "rank:asc,rank:desc", 'sort[1]: duplicate sort field "rank"'],
  ])("%s reads, and the resource's query refuses it before any read", (_name, sort, message) => {
    const state = resourceViewSearchToState({ sort });
    expect(state.queryError ?? null).toBeNull();
    expect(validateResourceViewState(state, query).queryError?.message).toBe(message);
  });
});

describe("a saved view keeps the whole order", () => {
  test("one key stays the object every saved view has stored, byte for byte", () => {
    const favorite = favoriteFromResourceView(createResourceViewState({ sorting: order("title desc") }), "One");
    expect(JSON.stringify(favorite)).toBe('{"id":"favorite:one","label":"One","pageSize":50,"sort":{"field":"title","dir":"desc"}}');
  });

  test("several keys are written as a list, in order, grouping key first", () => {
    const state = createResourceViewState({ groupStack: [{ field: "status" }], sorting: order("status asc", "rank desc", "title asc") });
    expect(favoriteFromResourceView(state, "Two").sort).toEqual([
      { field: "status", dir: "asc" }, { field: "rank", dir: "desc" }, { field: "title", dir: "asc" },
    ]);
  });

  test("an old one-key object, a list and null all read; a list with a bad key does not", () => {
    const stored = [
      { id: "favorite:old", label: "Old", sort: { field: "title", dir: "asc" } },
      { id: "favorite:list", label: "List", sort: [{ field: "status", dir: "asc" }, { field: "rank", dir: "desc" }] },
      { id: "favorite:cleared", label: "Cleared", sort: null },
    ];
    expect(resourceViewFavoritesFromUnknown(JSON.parse(JSON.stringify(stored)))).toEqual(stored);
    expect(resourceViewFavoritesFromUnknown([{ id: "favorite:bad", label: "Bad", sort: [{ field: "rank", dir: "sideways" }] }])).toEqual([]);
  });

  test("an empty list is refused as any bad saved view is; null is the one spelling that clears", () => {
    expect(resourceViewFavoritesFromUnknown([{ id: "favorite:empty", label: "Empty", sort: [] }])).toEqual([]);
    const [cleared] = resourceViewFavoritesFromUnknown([{ id: "favorite:cleared", label: "Cleared", sort: null }]);
    expect(createResourceViewState({ sort: cleared!.sort }).sorting).toEqual([]);
  });

  test.each([
    ["one key", order("title desc")],
    ["several keys", order("status asc", "rank desc")],
  ])("a view of %s shows active right after it is saved, and applying it restores the whole order", (_name, sorting) => {
    const state = createResourceViewState({ groupStack: [{ field: "status" }], sorting });
    const [stored] = resourceViewFavoritesFromUnknown([JSON.parse(JSON.stringify(favoriteFromResourceView(state, "Saved")))]);
    expect(resourceViewFavoriteIsActive(stored!, state)).toBe(true);
    // Applied to a list in another order.
    const { result } = renderHook(() => useResourceView(), { wrapper: ({ children }: { children: ReactNode }) =>
      createElement(ResourceViewProvider, { scope: "local", initialState: { groupStack: [{ field: "status" }], sorting: order("title asc") }, children }) });
    act(() => result.current.applyFavorite(stored!));
    expect(result.current.state.sorting).toEqual(sorting);
    expect(resourceViewFavoriteIsActive(stored!, result.current.state)).toBe(true);
  });
});
