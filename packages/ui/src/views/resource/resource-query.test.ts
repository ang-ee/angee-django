import { ResourceQuery } from "@angee/metadata";
import { expect, test } from "vitest";
import { filterForTextSearch, queryForColumns } from "./resource-query";

const query = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, summary: { scalar: "String" }, status: { scalar: "String" },
} });

test("rows search expands across columns while preserving comparisons on the search field", () => {
  const filter = filterForTextSearch(query, {
    title: { iContains: "alpha", ne: "Forbidden" }, status: { exact: "active" },
  }, "title", ["title", "summary"]);
  expect(query.matches({ title: "Other", summary: "Alpha found", status: "active" }, filter)).toBe(true);
  expect(query.matches({ title: "Forbidden", summary: "Alpha found", status: "active" }, filter)).toBe(false);
  expect(query.matches({ title: "Other", summary: "Alpha found", status: "draft" }, filter)).toBe(false);
});

test("rows search preserves empty membership predicates and rejects unknown fields", () => {
  expect(query.matches({ title: "Alpha", status: "active" }, filterForTextSearch(query,
    { title: { iContains: "alpha" }, status: { inList: [] } }, "title", ["title"]))).toBe(false);
  expect(() => filterForTextSearch(query, { missing: { exact: "x" } })).toThrow(/missing/);
});

test("bare rows query declares local grouping without enabling server dimensions", () => {
  const rows = queryForColumns([{ field: "title" }], null, [{ field: "created", granularity: "month" }]);
  expect(rows.axis("created", "month").identity({ created: "2026-09-07T12:00:00Z" })).toBe("2026-09");
  expect(() => rows.axis("title").groupBy()).toThrow(/server/);
});
