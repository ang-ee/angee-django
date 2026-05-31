import { readFileSync } from "node:fs";

import { buildSchema, parse, validate } from "graphql";
import { describe, expect, test } from "vitest";

import {
  aggregateFieldName,
  assembleAggregateDocument,
  assembleDetailDocument,
  assembleGroupByDocument,
  assembleListDocument,
  assembleMutationDocument,
  groupByFieldName,
} from "./selection";

const contract = buildSchema(
  readFileSync(new URL("../schema/contract.graphql", import.meta.url), "utf8"),
);

/** A document is only correct if it validates against the pinned contract. */
function expectValid(document: string): void {
  const errors = validate(contract, parse(document));
  expect(errors.map((error) => error.message)).toEqual([]);
}

describe("assembleDetailDocument", () => {
  test("queries the singular field by Sqid id", () => {
    const document = assembleDetailDocument("Sale", ["title", "owner.firstName"]);
    expect(document).toBe(
      "query sale($id: Sqid!) { sale(id: $id) { id title owner { id firstName } } }",
    );
    expectValid(document);
  });
});

describe("assembleListDocument", () => {
  test("builds the relay connection with totalCount/edges/pageInfo", () => {
    const document = assembleListDocument("Sale", ["title"]);
    expect(document).toBe(
      "query sales($first: Int, $after: String, $search: String) { " +
        "sales(search: $search, first: $first, after: $after) { " +
        "totalCount edges { node { id title } } " +
        "pageInfo { endCursor hasNextPage } } }",
    );
    expectValid(document);
  });

  test("adds filters and order variables on request", () => {
    const document = assembleListDocument("Sale", ["title"], {
      withFilter: true,
      withOrder: true,
    });
    expect(document).toContain("$filters: SaleFilter");
    expect(document).toContain("$order: [SaleOrder!]");
    expect(document).toContain("filters: $filters");
    expect(document).toContain("order: $order");
    expectValid(document);
  });
});

describe("assembleMutationDocument", () => {
  test("create takes a noun-first input", () => {
    const document = assembleMutationDocument("Sale", "create", ["title"]);
    expect(document).toBe(
      "mutation saleCreate($input: SaleCreateInput!) { " +
        "saleCreate(input: $input) { id title } }",
    );
    expectValid(document);
  });

  test("update takes id plus input", () => {
    const document = assembleMutationDocument("Sale", "update", ["title"]);
    expect(document).toBe(
      "mutation saleUpdate($id: Sqid!, $input: SaleUpdateInput!) { " +
        "saleUpdate(id: $id, input: $input) { id title } }",
    );
    expectValid(document);
  });

  test("delete returns the DeletePreview ok/id shape", () => {
    const document = assembleMutationDocument("Sale", "delete", []);
    expect(document).toBe(
      "mutation saleDelete($id: Sqid!) { saleDelete(id: $id) { ok id } }",
    );
    expectValid(document);
  });
});

describe("aggregate documents", () => {
  test("field names are plural", () => {
    expect(aggregateFieldName("Sale")).toBe("salesAggregate");
    expect(groupByFieldName("Sale")).toBe("salesGroupBy");
  });

  test("aggregate selects count plus each measure operator", () => {
    const document = assembleAggregateDocument("Sale", ["total"]);
    expect(document).toBe(
      "query salesAggregate($search: String) { " +
        "salesAggregate(search: $search) { " +
        "count sum { total } avg { total } min { total } max { total } } }",
    );
    expectValid(document);
  });

  test("group-by selects the key fields and measures per bucket", () => {
    const document = assembleGroupByDocument("Sale", ["state"], ["total"]);
    expect(document).toBe(
      "query salesGroupBy($groupBy: [SaleGroupBySpec!]!, $search: String) { " +
        "salesGroupBy(groupBy: $groupBy, search: $search) { " +
        "totalCount results { key { state } " +
        "count sum { total } avg { total } min { total } max { total } } } }",
    );
    expectValid(document);
  });
});
