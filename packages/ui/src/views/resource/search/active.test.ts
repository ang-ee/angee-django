import { expect, test } from "vitest";
import { createResourceViewState } from "../resource-view-model";
import { activeItems } from "./active";
import type { ResourceViewInitialState } from "../resource-view-model";
import { searchFixture } from "./search-fixture.test-support";

const blank = { id: "missing", label: "Not given", filter: { status: { isNull: true } } };
const open = { id: "open", label: "Open", value: "open", filter: { status: { exact: "open" } } };
const catalog = searchFixture({ catalog: {
  filters: [{ id: "compound", label: "My open", filter: { status: { exact: "open" }, owner: { exact: "viewer" } } }],
  facets: [{ field: "status", label: "Status", source: "scalar", options: [open, blank] }],
  fields: [{ id: "status", label: "Status", type: "selection" }, { id: "owner", label: "Owner", type: "text" }, { id: "amount", label: "Amount", type: "number" }],
  groups: [{ id: "status", label: "Status", group: { field: "status" } }],
  favorites: [{ id: "favorite:recent", label: "Recent", filter: { title: { iContains: "review" }, status: { exact: "open" }, amount: { gte: 2 } } }],
} }).catalog;

test("projects every active kind once, in provider-defined search order", () => {
  const active = activeItems(createResourceViewState({ filter: {
    title: { iContains: "review" }, status: { exact: "open" }, amount: { gte: 2 },
  }, groupStack: [{ field: "status" }, { field: "owner" }] }), catalog);
  expect(active.map((item) => item.id)).toEqual([
    "facet:status", "text:title", "clause:amount:gte", "group:0", "group:1", "favorite:favorite:recent",
  ]);
  expect(active[0]).toMatchObject({ kind: "facet", options: [open] });
  expect(active[3]).toMatchObject({ index: 0, level: { field: "status" }, label: "Status" });
});

test("a compound named filter claims its fields without facet or clause duplicates", () => {
  const active = activeItems(createResourceViewState({ filter: {
    status: { exact: "open" }, owner: { exact: "viewer" }, amount: { gt: 0 },
  } }), catalog);
  expect(active.map((item) => item.id)).toEqual(["filter:compound", "clause:amount:gt"]);
});

test("the blank facet option is active through its predicate, never a synthetic scalar value", () => {
  const active = activeItems(createResourceViewState({ filter: { status: { isNull: true } } }), catalog);
  expect(active).toEqual([{ id: "facet:status", kind: "facet", field: "status", label: "Status", options: [blank] }]);
});

test("unmatched facet comparisons and non-text lookups remain visible as clauses", () => {
  const active = activeItems(createResourceViewState({ filter: { status: { ne: "open" }, title: { exact: "Review" } } }), catalog);
  expect(active.map((item) => item.id)).toEqual(["clause:status:ne", "clause:title:exact"]);
});

const savedView = { id: "by-company", label: "Bills by company", groupStack: [{ field: "company" }],
  sort: { field: "title", dir: "desc" as const }, view: "board" as const, pageSize: 20, columnVisibility: { title: false } };

test.each<ResourceViewInitialState>([
  { groupStack: [] }, { groupStack: [{ field: "status" }] }, { sort: null },
  { sort: { field: "title", dir: "asc" } }, { view: "list" }, { pageSize: 10 }, { columnVisibility: {} },
  { filter: { title: { iContains: "other" } } }, { preset: "other" },
])("saved view activity requires every declared fact: %j", (change) => {
  const favorites = searchFixture({ catalog: { favorites: [savedView] } }).catalog;
  const matching = createResourceViewState(savedView);
  expect(activeItems({ ...matching, pagination: { pageIndex: 3, pageSize: 20 } }, favorites)
    .some((item) => item.kind === "favorite")).toBe(true);
  expect(activeItems(createResourceViewState({ ...savedView, ...change }), favorites)
    .some((item) => item.kind === "favorite")).toBe(false);
});

test("selected shipped preset identity survives editable changes until clearPreset", () => {
  const favorites = searchFixture({ catalog: { favorites: [{ ...savedView, preset: savedView.id }] } }).catalog;
  expect(activeItems(createResourceViewState({ preset: savedView.id }), favorites)
    .some((item) => item.kind === "favorite")).toBe(true);
  expect(activeItems(createResourceViewState({ preset: "" }), favorites)
    .some((item) => item.kind === "favorite")).toBe(false);
});

test("obsolete saved query intent remains inactive without breaking other search items", () => {
  const favorites = searchFixture({ catalog: { favorites: [{ id: "old", label: "Old", groupStack: [{ field: "company", aggregateKey: "legacy" }] }] } }).catalog;
  expect(activeItems(createResourceViewState(), favorites)).toEqual([]);
});
