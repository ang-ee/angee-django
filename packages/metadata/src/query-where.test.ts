import { expect, test } from "vitest";
import { ResourceQuery } from "./query";
import { testQueryField, testResourceQuery } from "./testing";

const query = ResourceQuery.fromContract(testResourceQuery({ fields: {
  title: testQueryField("title", { filter: { field: "label", scalar: "String", values: [],
    operators: ["exact", "like", "iLike", "contains", "inList", "isNull"] } }),
  rank: testQueryField("rank", { filter: { field: "rank", scalar: "Int", values: [], operators: ["gte", "lt"] } }),
  owner: testQueryField("owner", { filter: { field: "owner.id", scalar: "ID", values: [], operators: ["exact"] } }),
} }));

test("persisted predicates retain nested boolean structure, aliases, patterns, and empty groups", () => {
  const where = { _and: [
    { _or: [{ label: { _ilike: "%A\\_%" } }, { rank: { _gte: 2, _lt: 9 } }] },
    { _not: { owner: { id: { _eq: "usr_1" } } } },
    { _or: [] }, { label: { _in: [] } }, { label: { _is_null: false } },
  ] };
  expect(query.toWhere(query.fromWhere(where))).toEqual(where);
});

test("convenience text comparisons are decoded only when their escaped pattern round trips", () => {
  const contains = ResourceQuery.fromContract(testResourceQuery({ fields: {
    title: testQueryField("title", { filter: { field: "title", scalar: "String", values: [], operators: ["contains"] } }),
  } }));
  const where = contains.toWhere({ title: { contains: "A_%\\" } });
  expect(contains.fromWhere(where)).toEqual({ title: { contains: "A_%\\" } });
  expect(() => contains.fromWhere({ title: { _like: "A%" } })).toThrow();
});

test.each([
  { secret: { _eq: "hidden" } }, { label: { _regex: ".*" } },
  { rank: { _gte: "two" } }, { _or: {} }, { owner: { secret: { _eq: 1 } } },
  { owner: { _not: { id: { _eq: "usr_1" } } } },
])("unknown or malformed stored conditions fail without dropping clauses: %j", (where) => {
  expect(() => query.fromWhere(where)).toThrow();
});

test("excessive persisted condition nesting fails at the query owner", () => {
  let value: unknown = {};
  for (let index = 0; index < 34; index += 1) value = { _not: value };
  expect(() => query.fromWhere(value)).toThrow(/nesting/);
});
