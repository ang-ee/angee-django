import { describe, expect, test } from "vitest";

import { DISABLED_DOCUMENTS } from "../disabled-documents";
import type { ModelRootFieldMetadata } from "../model-metadata";
import { createGraphQLDataSource } from "./graphql-source";

const ROOT_FIELDS: ModelRootFieldMetadata = {
  list: "sales",
  aggregate: "totalSales",
  groupBy: "saleBreakdown",
  groupByInput: "SaleGroupBySpec",
  groupOrderInput: "SaleGroupOrder",
};

describe("createGraphQLDataSource", () => {
  test("centralizes list documents and variables", () => {
    const source = createGraphQLDataSource({
      modelLabel: "Sale",
      rootFields: ROOT_FIELDS,
    });

    expect(
      source.listDocument({
        fields: ["title"],
        filter: {},
        order: {},
      }),
    ).toBe(
      "query sales($pagination: OffsetPaginationInput, " +
        "$filters: SaleFilter, $order: SaleOrder) { " +
        "sales(pagination: $pagination, filters: $filters, order: $order) { " +
        "totalCount results { id title } pageInfo { offset limit } } }",
    );
    expect(
      source.listVariables({
        page: 3,
        pageSize: 25,
        filter: { state: { exact: "OPEN" } },
        order: { title: "ASC" },
      }),
    ).toEqual({
      pagination: { offset: 50, limit: 25 },
      filters: { state: { exact: "OPEN" } },
      order: { title: "ASC" },
    });
  });

  test("applies the shared pagination policy", () => {
    const source = createGraphQLDataSource({
      modelLabel: "Sale",
      rootFields: ROOT_FIELDS,
    });

    expect(
      source.listVariables({
        page: Number.NaN,
        pageSize: 200,
      }),
    ).toEqual({ pagination: { offset: 0, limit: 100 } });
    expect(
      source.groupByVariables({
        groups: [{ field: "STATE" }],
        page: 0,
        pageSize: -1,
      }),
    ).toEqual({
      groupBy: [{ field: "STATE" }],
      pagination: { offset: 0, limit: 1 },
    });
  });

  test("centralizes ungrouped aggregate documents and variables", () => {
    const source = createGraphQLDataSource({
      modelLabel: "Sale",
      rootFields: ROOT_FIELDS,
    });

    expect(
      source.aggregateDocument({
        filter: {},
        measures: [{ op: "sum", field: "amount" }],
      }),
    ).toBe(
      "query totalSales($filter: SaleFilter) { " +
        "totalSales(filter: $filter) { count sum { amount } } }",
    );
    expect(
      source.aggregateVariables({
        filter: { state: { exact: "OPEN" } },
      }),
    ).toEqual({ filter: { state: { exact: "OPEN" } } });
  });

  test("centralizes grouped aggregate documents and variables", () => {
    const source = createGraphQLDataSource({
      modelLabel: "Sale",
      rootFields: ROOT_FIELDS,
    });

    expect(
      source.groupByDocument(
        {
          groups: [
            { field: "STATE", key: "state" },
            {
              field: "CREATED_AT",
              key: "createdAtMonth",
              granularity: "month",
            },
          ],
          filter: {},
          groupOrder: [{ field: "count", direction: "DESC" }],
          measures: [{ op: "avg", field: "amount" }],
        },
        { withFilterEcho: true },
      ),
    ).toBe(
      "query saleBreakdown($groupBy: [SaleGroupBySpec!]!, " +
        "$pagination: OffsetPaginationInput, $filter: SaleFilter, " +
        "$orderBy: [SaleGroupOrder!]) { " +
        "saleBreakdown(groupBy: $groupBy, pagination: $pagination, " +
        "filter: $filter, orderBy: $orderBy) { " +
        "totalCount results { key { state createdAtMonth } " +
        "count filter avg { amount } } pageInfo { offset limit } } }",
    );
    expect(
      source.groupByVariables({
        groups: [
          { field: "STATE", key: "state" },
          {
            field: "CREATED_AT",
            key: "createdAtMonth",
            granularity: "month",
          },
        ],
        page: 2,
        pageSize: 20,
        filter: { state: { exact: "OPEN" } },
        groupOrder: [{ field: "count", direction: "DESC" }],
      }),
    ).toEqual({
      groupBy: [
        { field: "STATE" },
        { field: "CREATED_AT", granularity: "month" },
      ],
      pagination: { offset: 20, limit: 20 },
      filter: { state: { exact: "OPEN" } },
      orderBy: [{ field: "count", direction: "DESC" }],
    });
  });

  test("centralizes multi-facet grouped documents and variables", () => {
    const source = createGraphQLDataSource({
      modelLabel: "Sale",
      rootFields: ROOT_FIELDS,
    });

    expect(
      source.facetsDocument(
        {
          facets: [
            {
              id: "state",
              groups: [{ field: "STATE", key: "state" }],
              filter: { title: { iContains: "launch" } },
            },
            {
              id: "created",
              groups: [{
                field: "CREATED_AT",
                key: "createdAtMonth",
                granularity: "month",
              }],
              filter: { state: { exact: "OPEN" } },
              groupOrder: [{ field: "createdAtMonth", direction: "ASC" }],
            },
          ],
        },
        { withFilterEcho: true },
      ),
    ).toBe(
      "query saleBreakdownFacets(" +
        "$groupBy0: [SaleGroupBySpec!]!, " +
        "$pagination0: OffsetPaginationInput, " +
        "$filter0: SaleFilter, " +
        "$groupBy1: [SaleGroupBySpec!]!, " +
        "$pagination1: OffsetPaginationInput, " +
        "$filter1: SaleFilter, " +
        "$orderBy1: [SaleGroupOrder!]) { " +
        "facet0: saleBreakdown(groupBy: $groupBy0, " +
        "pagination: $pagination0, filter: $filter0) { " +
        "totalCount results { key { state } count filter } " +
        "pageInfo { offset limit } } " +
        "facet1: saleBreakdown(groupBy: $groupBy1, " +
        "pagination: $pagination1, filter: $filter1, orderBy: $orderBy1) { " +
        "totalCount results { key { createdAtMonth } count filter } " +
        "pageInfo { offset limit } } }",
    );
    expect(
      source.facetsVariables({
        facets: [
          {
            id: "state",
            groups: [{ field: "STATE", key: "state" }],
            filter: { title: { iContains: "launch" } },
          },
          {
            id: "created",
            groups: [{
              field: "CREATED_AT",
              key: "createdAtMonth",
              granularity: "month",
            }],
            filter: { state: { exact: "OPEN" } },
            page: 2,
            pageSize: 10,
            groupOrder: [{ field: "createdAtMonth", direction: "ASC" }],
          },
        ],
      }),
    ).toEqual({
      groupBy0: [{ field: "STATE" }],
      pagination0: null,
      filter0: { title: { iContains: "launch" } },
      groupBy1: [{ field: "CREATED_AT", granularity: "month" }],
      pagination1: { offset: 10, limit: 10 },
      orderBy1: [{ field: "createdAtMonth", direction: "ASC" }],
      filter1: { state: { exact: "OPEN" } },
    });
  });

  test("uses the shared disabled document when the model cannot query", () => {
    const source = createGraphQLDataSource({
      modelLabel: "Sale",
      rootFields: null,
    });

    expect(source.canQuery).toBe(false);
    expect(source.listDocument({ fields: ["title"] })).toBe(
      DISABLED_DOCUMENTS.query,
    );
    expect(source.aggregateDocument()).toBe(DISABLED_DOCUMENTS.query);
    expect(source.groupByDocument({ groups: [{ field: "STATE" }] })).toBe(
      DISABLED_DOCUMENTS.query,
    );
    expect(source.facetsDocument({ facets: [] })).toBe(DISABLED_DOCUMENTS.query);
  });
});
