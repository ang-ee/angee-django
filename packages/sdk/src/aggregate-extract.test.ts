import { describe, expect, test } from "vitest";

import {
  autoExtractAggregate,
  autoExtractGroupBy,
  selectMeasure,
} from "./aggregate-extract";

describe("autoExtractAggregate", () => {
  test("reads count and the present measure operators", () => {
    const data = {
      salesAggregate: { count: 6, sum: { total: "1125.00" }, avg: { total: 187.5 } },
    };
    expect(autoExtractAggregate(data, "salesAggregate")).toEqual({
      key: null,
      count: 6,
      measures: { sum: { total: "1125.00" }, avg: { total: 187.5 } },
    });
  });

  test("returns null when the field is absent", () => {
    expect(autoExtractAggregate({}, "salesAggregate")).toBeNull();
  });
});

describe("autoExtractGroupBy", () => {
  test("maps results into buckets carrying key, count, and measures", () => {
    const data = {
      salesGroupBy: {
        totalCount: 2,
        results: [
          { key: { state: "OPEN" }, count: 3, sum: { total: "700.00" } },
          { key: { state: "CLOSED" }, count: 2, sum: { total: "350.00" } },
        ],
      },
    };
    expect(autoExtractGroupBy(data, "salesGroupBy")).toEqual({
      totalCount: 2,
      buckets: [
        { key: { state: "OPEN" }, count: 3, measures: { sum: { total: "700.00" } } },
        { key: { state: "CLOSED" }, count: 2, measures: { sum: { total: "350.00" } } },
      ],
    });
  });

  test("returns an empty result when the field is absent", () => {
    expect(autoExtractGroupBy({}, "salesGroupBy")).toEqual({
      totalCount: 0,
      buckets: [],
    });
  });
});

describe("selectMeasure", () => {
  test("reads a measure value by operator and field", () => {
    const bucket = { key: null, count: 1, measures: { sum: { total: "700.00" } } };
    expect(selectMeasure(bucket, "sum", "total")).toBe("700.00");
  });

  test("returns null for a missing bucket or measure", () => {
    expect(selectMeasure(null, "sum", "total")).toBeNull();
    expect(
      selectMeasure({ key: null, count: 1, measures: {} }, "avg", "total"),
    ).toBeNull();
  });
});
