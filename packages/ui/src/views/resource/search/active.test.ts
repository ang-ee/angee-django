import { expect, test } from "vitest";
import { createResourceViewState } from "../resource-view-model";
import { activeItems } from "./active";
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
