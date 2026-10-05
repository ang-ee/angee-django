// @vitest-environment happy-dom
import { useState, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { QueryParseError } from "@angee/metadata";
import { AppRuntimeProvider } from "../../../runtime";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { resourceViewSearchToState, resourceViewStateToSearch, type ResourceViewInitialState } from "../resource-view-model";
import { RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY } from "../resource-view-favorites";
import { searchFixture } from "./search-fixture.test-support";
import type { SearchFacetOption } from "./types";
import { useResourceSearch } from "./use-resource-search";

afterEach(cleanup);
const defaults: ResourceViewInitialState = { filter: { title: { iContains: "baseline" } },
  groupStack: [{ field: "status" }], sort: { field: "title", dir: "desc" } };
const options: readonly SearchFacetOption[] = [
  { id: "open", label: "Open", value: "open", filter: { status: { exact: "open" } } },
  { id: "closed", label: "Closed", value: "closed", filter: { status: { exact: "closed" } } },
  { id: "blank", label: "Not given", filter: { status: { isNull: true } } },
];
function fixture(maxGroupDepth?: number, initialState: ResourceViewInitialState = defaults) {
  function Wrapper({ children }: { children: ReactNode }) {
    const [preferences, setPreferences] = useState<Record<string, unknown>>({
      [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: { version: 1, models: { "collection:commands": [
        { id: "favorite:open", label: "Open records", filter: { status: { exact: "open" } }, groupStack: [{ field: "owner" }] },
      ] } },
    });
    return <AppRuntimeProvider runtime={{ userPreferences: { available: true, preferences,
      patchPreferences: async (patch) => { setPreferences((current) => patch(current)); },
    } }}><ResourceViewProvider scope="local" favoriteKey="commands" initialState={initialState}>{children}</ResourceViewProvider></AppRuntimeProvider>;
  }
  return renderHook(() => {
    const view = useResourceView();
    const catalog = searchFixture({ catalog: {
      filters: [
        { id: "mine", label: "Mine", filter: { owner: { exact: "viewer" } } },
        { id: "other", label: "Other owner", filter: { owner: { exact: "other" } } },
        { id: "compound", label: "My open records", filter: { owner: { exact: "viewer" }, status: { exact: "open" } } },
      ],
      facets: [{ field: "status", label: "Status", source: "scalar", options }],
      fields: [{ id: "owner", label: "Owner", type: "text" }, { id: "amount", label: "Amount", type: "number" }],
      favorites: view.savedFavorites,
    } }).catalog;
    const search = useResourceSearch({ resourceView: view, catalog, groupingEnabled: true, maxGroupDepth });
    return { view, search };
  }, { wrapper: Wrapper });
}

test("text, named filters and clauses compose against the latest native state and toggle twice restores it", () => {
  const { result } = fixture();
  const start = result.current.view.state.filter;
  act(() => { result.current.search.toggleFilter("mine"); result.current.search.setText(" review "); result.current.search.addClause({ field: "amount", operator: "gte", value: 2 }); });
  expect(result.current.view.state.filter).toEqual({ title: { iContains: "review" }, owner: { exact: "viewer" }, amount: { gte: 2 } });
  act(() => result.current.search.setClause("amount", { field: "amount", operator: "lt", value: 5 }));
  expect(result.current.view.state.filter.amount).toEqual({ lt: 5 });
  act(() => result.current.search.setClause("amount", null));
  act(() => result.current.search.setText("baseline"));
  act(() => result.current.search.toggleFilter("mine"));
  expect(result.current.view.state.filter).toEqual(start);
  act(() => result.current.search.toggleFilter("mine"));
  act(() => result.current.search.toggleFilter("mine"));
  expect(result.current.view.state.filter).toEqual(start);
});

test("single-value named options combine on one field and clearing one preserves the other", () => {
  const { result } = fixture();
  act(() => { result.current.search.toggleFilter("mine"); result.current.search.toggleFilter("other"); });
  expect(result.current.view.state.filter).toEqual({ title: { iContains: "baseline" }, owner: { inList: ["viewer", "other"] } });
  expect(result.current.search.active.filter((item) => item.kind === "filter").map((item) => item.id)).toEqual(["filter:mine", "filter:other"]);
  act(() => result.current.search.clear("filter:mine"));
  expect(result.current.view.state.filter.owner).toEqual({ exact: "other" });
  expect(result.current.search.active.some((item) => item.id === "filter:other")).toBe(true);
  act(() => result.current.search.toggleFilter("other"));
  expect(result.current.view.state.filter).toEqual(defaults.filter);
});

test("a compound named option toggles its predicate as a unit", () => {
  const { result } = fixture();
  act(() => result.current.search.toggleFilter("compound"));
  expect(result.current.view.state.filter).toEqual({ title: { iContains: "baseline" }, owner: { exact: "viewer" }, status: { exact: "open" } });
  expect(result.current.search.active.some((item) => item.id === "filter:compound")).toBe(true);
  act(() => result.current.search.toggleFilter("compound"));
  expect(result.current.view.state.filter).toEqual(defaults.filter);
  expect(result.current.search.active.some((item) => item.id === "filter:compound")).toBe(false);
});

test("facet replacement is exclusive for blanks and value toggles round-trip", () => {
  const { result } = fixture();
  act(() => result.current.search.setFacet("status", ["open", "closed"]));
  expect(result.current.view.state.filter.status).toEqual({ inList: ["open", "closed"] });
  act(() => result.current.search.setFacet("status", ["open", "blank"]));
  expect(result.current.view.state.filter.status).toEqual({ isNull: true });
  act(() => result.current.search.setFacet("status", ["blank", "closed"]));
  expect(result.current.view.state.filter.status).toEqual({ exact: "closed" });
  act(() => result.current.search.setFacet("status", ["blank"]));
  act(() => result.current.search.toggleFacetOption("status", "closed"));
  expect(result.current.view.state.filter.status).toEqual({ exact: "closed" });
  act(() => result.current.search.toggleFacetOption("status", "closed"));
  expect(result.current.view.state.filter.status).toBeUndefined();
  act(() => result.current.search.toggleFacetOption("status", "blank"));
  act(() => result.current.search.toggleFacetOption("status", "blank"));
  expect(result.current.view.state.filter.status).toBeUndefined();
  expect(result.current.view.state.filter.title).toEqual({ iContains: "baseline" });
});

test("facet replacement retains active buckets reached beyond the initial catalog page", () => {
  const catalog = searchFixture({ catalog: { facets: [{ field: "status", label: "Status", source: "relation", options,
    optionForValue: (value) => ({ id: value, label: value, value, filter: { status: { exact: value } } }),
  }] } }).catalog;
  const { result } = renderHook(() => {
    const view = useResourceView();
    return { view, search: useResourceSearch({ resourceView: view, catalog }) };
  }, { wrapper: ({ children }) => <ResourceViewProvider scope="local" initialState={{ filter: { status: { exact: "beyond" } } }}>{children}</ResourceViewProvider> });
  act(() => result.current.search.setFacet("status", ["beyond", "open"]));
  expect(result.current.view.state.filter.status).toEqual({ inList: ["open", "beyond"] });
});

test("groups append, move, replace and remove through the provider and preserve URL order", () => {
  const { result } = fixture();
  act(() => result.current.search.addGroup({ field: "owner" }));
  act(() => result.current.search.addGroup({ field: "created", granularity: "month" }));
  act(() => result.current.search.moveGroup(2, 0));
  expect(result.current.view.state.groupStack).toEqual([{ field: "created", granularity: "month" }, { field: "status" }, { field: "owner" }]);
  const encoded = resourceViewStateToSearch(result.current.view.state);
  expect(encoded).toMatchObject({ group: "created:month", then: "status,owner" });
  expect(resourceViewSearchToState(encoded).groupStack).toEqual(result.current.view.state.groupStack);
  act(() => result.current.search.moveGroup(0, 2));
  act(() => result.current.search.setGroupLevel(2, { field: "created", granularity: "year" }));
  expect(result.current.view.state.groupStack[2]?.granularity).toBe("year");
  act(() => result.current.search.removeGroup(1));
  expect(result.current.view.state.groupStack.map((level) => level.field)).toEqual(["status", "created"]);
  act(() => result.current.search.setGroupStack([]));
  expect(result.current.view.state.groupStack).toEqual([]);
});

test("the provider rejects a duplicate axis and max depth retains the final levels", () => {
  const { result } = fixture(2);
  act(() => result.current.search.addGroup({ field: "created", granularity: "month" }));
  act(() => result.current.search.addGroup({ field: "created", granularity: "year" }));
  expect(result.current.view.state.groupStack).toEqual([{ field: "created", granularity: "month" }, { field: "created", granularity: "year" }]);
  expect(() => act(() => result.current.search.setGroupLevel(1, { field: "created", granularity: "month" }))).toThrow(QueryParseError);
  expect(result.current.view.state.groupStack[1]?.granularity).toBe("year");
});

test("clear dispatches each active kind without clearing unrelated facts", () => {
  const { result } = fixture();
  act(() => result.current.search.toggleFilter("mine"));
  act(() => result.current.search.setFacet("status", ["blank"]));
  act(() => result.current.search.addClause({ field: "amount", operator: "gt", value: 0 }));
  act(() => result.current.search.clear("clause:amount:gt"));
  expect(result.current.view.state.filter.amount).toBeUndefined();
  act(() => result.current.search.clear("filter:mine"));
  expect(result.current.view.state.filter.owner).toBeUndefined();
  act(() => result.current.search.clear("facet:status"));
  expect(result.current.view.state.filter.status).toBeUndefined();
  act(() => result.current.search.clear("text:title"));
  expect(result.current.view.state.filter.title).toBeUndefined();
  act(() => result.current.search.clear("group:0"));
  expect(result.current.view.state.groupStack).toEqual([]);
});

test("resetQuery empties editable facts; clearQuery restores the provider's defaults", () => {
  const { result } = fixture();
  act(() => result.current.search.resetQuery());
  expect(result.current.view.state).toMatchObject({ filter: {}, sorting: [], groupStack: [] });
  expect(result.current.search.queryDirty).toBe(true);
  act(() => result.current.search.clearQuery());
  expect(result.current.view.state).toMatchObject({ filter: defaults.filter, groupStack: defaults.groupStack, sorting: [{ id: "title", desc: true }] });
  expect(result.current.search.queryDirty).toBe(false);
});

test("favorite application, toggle, save, rename and pin retain provider persistence", async () => {
  const { result } = fixture();
  act(() => result.current.search.applyFavorite("favorite:open"));
  expect(result.current.view.state.filter).toEqual({ status: { exact: "open" } });
  expect(result.current.view.state.groupStack).toEqual([{ field: "owner" }]);
  act(() => result.current.search.clear("favorite:favorite:open"));
  expect(result.current.view.state.filter).toEqual({});
  await act(async () => result.current.search.saveFavorite?.("Saved search"));
  const saved = result.current.search.catalog.favorites.find((favorite) => favorite.label === "Saved search")!;
  expect(saved).toBeTruthy();
  await act(async () => result.current.search.renameFavorite?.(saved.id, "Renamed"));
  await act(async () => result.current.search.pinFavorite?.(saved.id, true));
  expect(result.current.search.catalog.favorites.find((favorite) => favorite.id === saved.id)).toMatchObject({ label: "Renamed", pinned: true });
  act(() => result.current.search.toggleFavorite("favorite:open"));
  expect(result.current.view.state.filter).toEqual({ status: { exact: "open" } });
});
