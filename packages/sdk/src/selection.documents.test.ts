import { readFileSync } from "node:fs";

import { buildSchema, parse, validate } from "graphql";
import { describe, expect, test } from "vitest";

import {
  aggregateFieldName,
  assembleAggregateDocument,
  assembleGroupByDocument,
  assembleDetailDocument,
  assembleListDocument,
  assembleMutationDocument,
  groupByFieldName,
} from "./selection";
import { changeSubscriptionDocument } from "./relay-invalidation";

const contract = buildSchema(
  readFileSync(new URL("../schema/contract.graphql", import.meta.url), "utf8"),
);

/** A document is only correct if it validates against the pinned contract. */
function expectValid(document: string): void {
  const errors = validate(contract, parse(document));
  expect(errors.map((error) => error.message)).toEqual([]);
}

describe("assembleDetailDocument", () => {
  test("queries the singular field by relay id", () => {
    const document = assembleDetailDocument("Sale", ["title", "state"]);
    expect(document).toBe(
      "query sale($id: ID!) { sale(id: $id) { id title state } }",
    );
    expectValid(document);
  });
});

describe("assembleListDocument", () => {
  test("builds the offset page with totalCount/results/pageInfo", () => {
    const document = assembleListDocument("Sale", ["title"]);
    expect(document).toBe(
      "query sales($pagination: OffsetPaginationInput) { " +
        "sales(pagination: $pagination) { " +
        "totalCount results { id title } pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("adds filters and the @oneOf order variable on request", () => {
    const document = assembleListDocument("Sale", ["title"], {
      withFilter: true,
      withOrder: true,
    });
    expect(document).toContain("$pagination: OffsetPaginationInput");
    expect(document).toContain("$filters: SaleFilter");
    expect(document).toContain("$order: SaleOrder");
    expect(document).toContain("filters: $filters");
    expect(document).toContain("order: $order");
    expectValid(document);
  });
});

describe("assembleMutationDocument", () => {
  test("create takes a verb-first data input", () => {
    const document = assembleMutationDocument("Sale", "create", ["title"]);
    expect(document).toBe(
      "mutation createSale($data: SaleInput!) { " +
        "createSale(data: $data) { id title } }",
    );
    expectValid(document);
  });

  test("update takes a patch whose id travels inside the data", () => {
    const document = assembleMutationDocument("Sale", "update", ["title"]);
    expect(document).toBe(
      "mutation updateSale($data: SalePatch!) { " +
        "updateSale(data: $data) { id title } }",
    );
    expectValid(document);
  });

  test("delete returns the cascade DeletePreview shape", () => {
    const document = assembleMutationDocument("Sale", "delete", []);
    expect(document).toBe(
      "mutation deleteSale($id: ID!, $confirm: Boolean) { deleteSale(id: $id, confirm: $confirm) { " +
        "totalDeletedCount hasBlockers " +
        "deleted { label count } updated { label count } blocked { label count } " +
        "root { label objectLabel objectId " +
        "children { label objectLabel objectId " +
        "children { label objectLabel objectId } } } } }",
    );
    expect(document).toContain("confirm: $confirm");
    expect(document).toContain("root { label objectLabel objectId");
    expectValid(document);
  });
});

describe("aggregate documents", () => {
  test("the field name is the singular noun plus Aggregate", () => {
    expect(aggregateFieldName("Sale")).toBe("saleAggregate");
  });

  test("the group field name is the singular noun plus Groups", () => {
    expect(groupByFieldName("Sale")).toBe("saleGroups");
  });

  test("the ungrouped aggregate selects just count", () => {
    const document = assembleAggregateDocument("Sale");
    expect(document).toBe("query saleAggregate { saleAggregate { count } }");
    expectValid(document);
  });

  test("the ungrouped aggregate accepts the model filter on request", () => {
    const document = assembleAggregateDocument("Sale", { withFilter: true });
    expect(document).toBe(
      "query saleAggregate($filter: SaleFilter) { " +
        "saleAggregate(filter: $filter) { count } }",
    );
    expectValid(document);
  });

  test("the ungrouped aggregate selects requested measures", () => {
    const document = assembleAggregateDocument("Sale", {
      measures: [{ op: "sum", field: "amount" }],
    });
    expect(document).toBe(
      "query saleAggregate { saleAggregate { count sum { amount } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate declares groupBy and offset pagination", () => {
    const document = assembleGroupByDocument("Sale", { keyFields: ["state"] });
    expect(document).toBe(
      "query saleGroups($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput) { " +
        "saleGroups(groupBy: $groupBy, pagination: $pagination) { " +
        "totalCount results { key { state } count } " +
        "pageInfo { offset limit } } }",
    );
    expect(document).not.toContain("filter");
    expectValid(document);
  });

  test("the grouped aggregate selects the filter echo on request", () => {
    const document = assembleGroupByDocument("Sale", {
      keyFields: ["state"],
      withFilterEcho: true,
    });
    expect(document).toBe(
      "query saleGroups($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput) { " +
        "saleGroups(groupBy: $groupBy, pagination: $pagination) { " +
        "totalCount results { key { state } count filter } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate selects requested measures", () => {
    const document = assembleGroupByDocument("Sale", {
      keyFields: ["state"],
      measures: [{ op: "sum", field: "amount" }],
    });
    expect(document).toBe(
      "query saleGroups($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput) { " +
        "saleGroups(groupBy: $groupBy, pagination: $pagination) { " +
        "totalCount results { key { state } count sum { amount } } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate accepts the model filter on request", () => {
    const document = assembleGroupByDocument("Sale", {
      keyFields: ["state"],
      withFilter: true,
    });
    expect(document).toBe(
      "query saleGroups($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput, $filter: SaleFilter) { " +
        "saleGroups(groupBy: $groupBy, pagination: $pagination, filter: $filter) { " +
        "totalCount results { key { state } count } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });

  test("the grouped aggregate accepts group ordering on request", () => {
    const document = assembleGroupByDocument("Sale", {
      keyFields: ["createdAtMonth"],
      withOrderBy: true,
    });
    expect(document).toBe(
      "query saleGroups($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput, $orderBy: [SaleGroupOrder!]) { " +
        "saleGroups(groupBy: $groupBy, pagination: $pagination, orderBy: $orderBy) { " +
        "totalCount results { key { createdAtMonth } count } " +
        "pageInfo { offset limit } } }",
    );
    expectValid(document);
  });
});

describe("changeSubscriptionDocument", () => {
  test("subscribes to the model's change event", () => {
    const document = changeSubscriptionDocument("Sale");
    expect(document).toBe(
      "subscription angeeSaleChanged { " +
        "saleChanged { model id action changedFields changedValues } }",
    );
    expectValid(document);
  });
});
