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

  test("returns null when the field is absent", () => {
    expect(autoExtractAggregate({}, "saleAggregate")).toBeNull();
  });
});

describe("autoExtractGroupBy", () => {
  test("maps groups into buckets keyed by their dimension values", () => {
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
      buckets: [
        { key: { state: "OPEN" }, count: 3 },
        { key: { state: "CLOSED" }, count: 2 },
      ],
    });
  });

  test("returns an empty result when the field is absent", () => {
    expect(autoExtractGroupBy({}, "saleAggregate")).toEqual({
      count: 0,
      buckets: [],
    });
  });
});
