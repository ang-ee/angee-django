import { afterEach, describe, expect, test, vi } from "vitest";

import {
  ANGEE_FILTER_LOOKUP_OPERATORS,
  ANGEE_TEXT_FILTER_LOOKUP_OPERATORS,
  crudFiltersFromFilterRecord,
  hasuraOrderByFromAngeeOrder,
  hasuraWhereFromCrudFilters,
  refineFieldsFromPaths,
  refineSortersFromAngeeOrder,
} from "./filter-codec";

describe("refine/Hasura filter codec", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

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
      metadata: { jsonContains: { mailbox: "INBOX" } },
      title: { iContains: "launch" },
      summary: { contains: "100%_ready" },
      code: { startsWith: "Q1_" },
      slug: { iEndsWith: "%done" },
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
      metadata: { _contains: { mailbox: "INBOX" } },
      title: { _ilike: "%launch%" },
      summary: { _like: "%100\\%\\_ready%" },
      code: { _like: "Q1\\_%" },
      slug: { _ilike: "%\\%done" },
      word_count: { _gte: 10, _lt: 50 },
      archived_at: { _is_null: true },
      _or: [
        { priority: { _in: ["HIGH", "MEDIUM"] } },
        { is_starred: { _eq: true } },
      ],
    });
  });

  test("drops URL-sourced filters the stock refine/Hasura operator set cannot express", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    expect(crudFiltersFromFilterRecord({
      title: { iExact: "Launch" },
      NOT: { title: { exact: "Draft" } },
      metadata: { jsonContains: { kind: "note" } },
      status: { exact: "ACTIVE" },
    })).toEqual([
      { field: "metadata", operator: "jsonContains", value: { kind: "note" } },
      { field: "status", operator: "eq", value: "ACTIVE" },
    ]);
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('Unsupported refine/Hasura list filter "iExact"'),
    );
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("does not support Angee NOT filters"),
    );
    expect(warn).not.toHaveBeenCalledWith(
      expect.stringContaining('Unsupported refine/Hasura list filter "jsonContains"'),
    );
  });

  test("exports the UI operator vocabulary from the codec owner", () => {
    expect(ANGEE_FILTER_LOOKUP_OPERATORS).toEqual([
      "exact",
      "inList",
      "isNull",
      "contains",
      "iContains",
      "startsWith",
      "iStartsWith",
      "endsWith",
      "iEndsWith",
      "gt",
      "gte",
      "lt",
      "lte",
    ]);
    // The case-sensitive startsWith/endsWith variants ride the provider's
    // `_similar` encoding, which the backend leaves unmapped — not offered.
    expect(ANGEE_TEXT_FILTER_LOOKUP_OPERATORS).toEqual([
      "contains",
      "iContains",
      "iStartsWith",
      "iEndsWith",
      "isNull",
    ]);
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
