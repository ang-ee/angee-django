import type { CrudFilter } from "@refinedev/core";
import { expect, test } from "vitest";
import { ResourceQuery, unarchivedFilter, type QueryFilter } from "./query";
import type { DataResourceQuery } from "./query-schema";
import { testDataResource, testQueryField, testResourceQuery } from "./testing";

const flag = testQueryField("archived", {
  scalar: "Boolean",
  filter: { field: "archived", scalar: "Boolean", operators: ["exact", "inList"], values: [] },
});
const resource = testDataResource("example.Record", {
  fields: [{ name: "is_archived", kind: "scalar", scalar: "Boolean", archivable: true,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  query: testResourceQuery({ fields: { is_archived: flag, name: testQueryField("name") } }),
});

test("adds the declared archive scope without mutating the filter and honors its wire alias", () => {
  const filter = { name: { exact: "One" } };
  const scoped = unarchivedFilter(resource, filter);
  expect(scoped).toEqual({ name: { exact: "One" }, is_archived: { exact: false } });
  expect(ResourceQuery.from(resource).toWhere(scoped)).toEqual({
    _and: [{ archived: { _eq: false } }, { name: { _eq: "One" } }],
  });
  expect(filter).toEqual({ name: { exact: "One" } });
  expect(unarchivedFilter(resource, scoped)).toBe(scoped);
});

test("does not infer archive capability or require an update root", () => {
  const filter = {};
  expect(unarchivedFilter({ ...resource, fields: [] }, filter)).toBe(filter);
  expect(unarchivedFilter(null, filter)).toBe(filter);
  expect(unarchivedFilter(undefined, filter)).toBe(filter);
  expect(unarchivedFilter({ ...resource, roots: { list: "records" } }, filter))
    .toEqual({ is_archived: { exact: false } });
});

test("requires an executable exact comparison, not merely a readable flag", () => {
  const filter = {};
  const contracts: DataResourceQuery["fields"][] = [
    {},
    { is_archived: { ...flag, filter: null } },
    { is_archived: { ...flag, filter: { ...flag.filter!, operators: ["inList" as const] } } },
  ];
  for (const fields of contracts) {
    expect(unarchivedFilter({ ...resource, query: testResourceQuery({ fields }) }, filter)).toBe(filter);
  }
});

test("preserves explicit archive predicates anywhere in a compound filter", () => {
  const filters: QueryFilter[] = [
    { is_archived: { exact: true } },
    { is_archived: { exact: false } },
    { OR: [{ name: "One" }, { NOT: { is_archived: { exact: false } } }] },
  ];
  for (const filter of filters) expect(unarchivedFilter(resource, filter)).toBe(filter);
  expect(unarchivedFilter(resource, { "parent.is_archived": true }))
    .toEqual({ "parent.is_archived": { exact: true }, is_archived: { exact: false } });
});

test("native picker predicates can override the scope using the flag's declared wire field", () => {
  const filter = {};
  const native: CrudFilter[] = [{ operator: "or", value: [
    { field: "archived", operator: "eq", value: true },
    { field: "name", operator: "contains", value: "One" },
  ] }];
  expect(unarchivedFilter(resource, filter, native)).toBe(filter);
  expect(unarchivedFilter(resource, filter, [{ field: "parent.archived", operator: "eq", value: true }]))
    .toEqual({ is_archived: { exact: false } });
});
