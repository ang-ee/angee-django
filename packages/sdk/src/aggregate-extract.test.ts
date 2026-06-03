import { describe, expect, test } from "vitest";

import { autoExtractAggregate, autoExtractGroupBy } from "./aggregate-extract";

describe("autoExtractAggregate", () => {
  test("reads the ungrouped total count", () => {
    const data = { saleAggregate: { count: 6 } };
    expect(autoExtractAggregate(data, "saleAggregate")).toEqual({
      key: null,
      count: 6,
    });
  });

  test("reads ungrouped aggregate measures", () => {
    const data = {
      saleAggregate: {
        count: 6,
        sum: { amount: "120" },
        avg: { amount: 20 },
      },
    };
    expect(autoExtractAggregate(data, "saleAggregate")).toEqual({
      key: null,
      count: 6,
      sum: { amount: "120" },
      avg: { amount: 20 },
    });
  });

  test("returns null when the field is absent", () => {
    expect(autoExtractAggregate({}, "saleAggregate")).toBeNull();
  });
});

describe("autoExtractGroupBy", () => {
  test("maps grouped result rows into buckets keyed by their key object", () => {
    const data = {
      saleGroups: {
        totalCount: 2,
        results: [
          { count: 3, key: { state: "OPEN" } },
          { count: 2, key: { state: "CLOSED" } },
        ],
      },
    };
    expect(autoExtractGroupBy(data, "saleGroups")).toEqual({
      count: 5,
      totalCount: 2,
      buckets: [
        { key: { state: "OPEN" }, count: 3 },
        { key: { state: "CLOSED" }, count: 2 },
      ],
    });
  });

  test("extracts the grouped result row's filter echo", () => {
    const filter = { state: { exact: "OPEN" } };
    const data = {
      saleGroups: {
        totalCount: 1,
        results: [{ count: 3, key: { state: "OPEN" }, filter }],
      },
    };
    expect(autoExtractGroupBy(data, "saleGroups").buckets[0]).toEqual({
      key: { state: "OPEN" },
      count: 3,
      filter,
    });
  });

  test("extracts grouped result row measures", () => {
    const data = {
      saleGroups: {
        totalCount: 1,
        results: [
          {
            count: 3,
            key: { state: "OPEN" },
            sum: { amount: "42" },
            max: { amount: 30 },
          },
        ],
      },
    };
    expect(autoExtractGroupBy(data, "saleGroups").buckets[0]).toEqual({
      key: { state: "OPEN" },
      count: 3,
      sum: { amount: "42" },
      max: { amount: 30 },
    });
  });

  test("leaves the grouped bucket filter absent when no echo is returned", () => {
    const data = {
      saleGroups: {
        totalCount: 1,
        results: [{ count: 3, key: { state: "OPEN" } }],
      },
    };
    expect(autoExtractGroupBy(data, "saleGroups").buckets[0]).not.toHaveProperty(
      "filter",
    );
  });

  test("keeps reading legacy aggregate groups", () => {
    const data = {
      saleAggregate: {
        count: 5,
        groups: [
          { count: 3, state: "OPEN" },
          { count: 2, state: "CLOSED" },
        ],
      },
    };
    expect(autoExtractGroupBy(data, "saleAggregate")).toEqual({
      count: 5,
      totalCount: 2,
      buckets: [
        { key: { state: "OPEN" }, count: 3 },
        { key: { state: "CLOSED" }, count: 2 },
      ],
    });
  });

  test("returns an empty result when the field is absent", () => {
    expect(autoExtractGroupBy({}, "saleAggregate")).toEqual({
      count: 0,
      totalCount: 0,
      buckets: [],
    });
  });
});
