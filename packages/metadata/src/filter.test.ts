import type { CrudFilter } from "@refinedev/core";
import { expect, test } from "vitest";
import { Filter } from "./filter";
import type { QueryFilter } from "./query";

const references: QueryFilter[] = [
  { is_archived: true },
  { is_archived: { exact: false } },
  { AND: [{ name: "One" }, { is_archived: { exact: true } }] },
  { OR: [{ name: "One" }, { is_archived: { inList: [true, false] } }] },
  { NOT: { is_archived: { exact: true } } },
  { AND: [{ OR: [{ NOT: { is_archived: { exact: false } } }, { name: "One" }] }] },
];

test.each(references)("finds the resource's own archive predicate through Boolean branches: %j", (filter) => {
  expect(Filter.references(filter, "is_archived")).toBe(true);
});

test("does not confuse a relation path, a comparison operand or an empty branch with the field", () => {
  expect(Filter.references({ NOT: { "parent.is_archived": true } }, "is_archived")).toBe(false);
  expect(Filter.references({ metadata: { jsonContains: { is_archived: true } } }, "is_archived")).toBe(false);
  expect(Filter.references({ OR: [], AND: [], NOT: {} }, "is_archived")).toBe(false);
  expect(Filter.references(undefined, "is_archived")).toBe(false);
});

test("field projection and reference detection share nested Boolean traversal", () => {
  const filter = Filter.from({ AND: [{ name: "One" }, {
    OR: [{ is_archived: true }, { NOT: { is_archived: false, name: "Two" } }],
  }] });
  expect(filter.onlyFields(["is_archived"])).toEqual({ AND: [{
    OR: [{ is_archived: { exact: true } }, { NOT: { is_archived: { exact: false } } }],
  }] });
});

test("native Refine predicates use the same exact-name walk without translating operators", () => {
  const filters: CrudFilter[] = [{ operator: "and", value: [{ operator: "or", value: [
    { field: "name", operator: "contains", value: "One" },
    { field: "is_archived", operator: "in", value: [true, false] },
  ] }] }];
  expect(Filter.references(filters, "is_archived")).toBe(true);
  expect(Filter.references([{ field: "parent.is_archived", operator: "eq", value: true }], "is_archived")).toBe(false);
});
