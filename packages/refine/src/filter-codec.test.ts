import { describe, expect, test } from "vitest";

import {
  crudFiltersFromFilterRecord,
  hasuraOrderByFromAngeeOrder,
  hasuraWhereFromCrudFilters,
  refineFieldsFromPaths,
  refineSortersFromAngeeOrder,
} from "./filter-codec";

describe("refine/Hasura filter codec", () => {
  test("turns dotted field paths into refine GraphQL field selections", () => {
    expect(refineFieldsFromPaths([
      "id",
      "title",
      "provider.display_name",
      "provider.vendor.name",
    ])).toEqual([
      "id",
      "title",
      { provider: ["display_name", { vendor: ["name"] }] },
    ]);
  });

  test("maps filter records to refine CrudFilters for Hasura", () => {
    expect(crudFiltersFromFilterRecord({
      status: { exact: "ACTIVE" },
      provider: { sqid: "provider_1" },
      title: { iContains: "launch" },
      word_count: { gte: 10, lt: 50 },
      thread: { _eq: "thread_1" },
      archived_at: { isNull: true },
      OR: [
        { priority: { inList: ["HIGH", "MEDIUM"] } },
        { is_starred: { _eq: true } },
      ],
    })).toEqual([
      { field: "status", operator: "eq", value: "ACTIVE" },
      { field: "provider", operator: "eq", value: "provider_1" },
      { field: "title", operator: "contains", value: "launch" },
      { field: "word_count", operator: "gte", value: 10 },
      { field: "word_count", operator: "lt", value: 50 },
      { field: "thread", operator: "eq", value: "thread_1" },
      { field: "archived_at", operator: "null", value: true },
      {
        operator: "or",
        value: [
          { field: "priority", operator: "in", value: ["HIGH", "MEDIUM"] },
          { field: "is_starred", operator: "eq", value: true },
        ],
      },
    ]);
  });

  test("maps refine CrudFilters to Hasura bool expressions for custom roots", () => {
    const filters = crudFiltersFromFilterRecord({
      status: { exact: "ACTIVE" },
      provider: { sqid: "provider_1" },
      owner: { display_name: { exact: "Iva" } },
      title: { iContains: "launch" },
      word_count: { gte: 10, lt: 50 },
      archived_at: { isNull: true },
      OR: [
        { priority: { inList: ["HIGH", "MEDIUM"] } },
        { is_starred: { _eq: true } },
      ],
    });

    expect(hasuraWhereFromCrudFilters(filters)).toEqual({
      status: { _eq: "ACTIVE" },
      provider: { _eq: "provider_1" },
      owner: { display_name: { _eq: "Iva" } },
      title: { _ilike: "launch" },
      word_count: { _gte: 10, _lt: 50 },
      archived_at: { _is_null: true },
      _or: [
        { priority: { _in: ["HIGH", "MEDIUM"] } },
        { is_starred: { _eq: true } },
      ],
    });
  });

  test("rejects filters the stock refine/Hasura operator set cannot express", () => {
    expect(() =>
      crudFiltersFromFilterRecord({ title: { iExact: "Launch" } })
    ).toThrow(/Unsupported refine\/Hasura list filter "iExact"/);
    expect(() =>
      crudFiltersFromFilterRecord({ NOT: { title: { exact: "Draft" } } })
    ).toThrow(/does not support Angee NOT filters/);
    expect(() =>
      crudFiltersFromFilterRecord({ metadata: { jsonContains: { kind: "note" } } })
    ).toThrow(/Unsupported refine\/Hasura list filter "jsonContains"/);
  });

  test("maps Angee order objects to refine sorters", () => {
    expect(refineSortersFromAngeeOrder({
      updated_at: "DESC",
      title: "ASC",
    })).toEqual([
      { field: "updated_at", order: "desc" },
      { field: "title", order: "asc" },
    ]);
    expect(hasuraOrderByFromAngeeOrder({
      "owner.display_name": "ASC",
      updated_at: "DESC",
    })).toEqual({
      owner: { display_name: "asc" },
      updated_at: "desc",
    });
  });
});
