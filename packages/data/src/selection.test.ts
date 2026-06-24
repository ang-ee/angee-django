import { describe, expect, test } from "vitest";

import { clampPageSize, MAX_PAGE_SIZE } from "./operations";
import { buildSelection, printSelection } from "./selection";

describe("buildSelection", () => {
  test("injects id at the root selection", () => {
    expect(buildSelection([])).toEqual([{ name: "id" }]);
  });

  test("adds scalar leaf fields after id, in order", () => {
    expect(buildSelection(["title", "state"])).toEqual([
      { name: "id" },
      { name: "title" },
      { name: "state" },
    ]);
  });

  test("expands a dotted path into a nested selection with id at each level", () => {
    expect(buildSelection(["owner.firstName"])).toEqual([
      { name: "id" },
      { name: "owner", children: [{ name: "id" }, { name: "firstName" }] },
    ]);
  });

  test("dedupes repeated leaves and merges sibling sub-selections", () => {
    expect(
      buildSelection(["owner.firstName", "owner.lastName", "title", "title"]),
    ).toEqual([
      { name: "id" },
      {
        name: "owner",
        children: [{ name: "id" }, { name: "firstName" }, { name: "lastName" }],
      },
      { name: "title" },
    ]);
  });

  test("rejects an invalid field name instead of silently dropping it", () => {
    expect(() => buildSelection(["bad-name"])).toThrow(/bad-name/);
  });
});

describe("printSelection", () => {
  test("prints leaves space-joined and nested selections in braces", () => {
    expect(printSelection(buildSelection(["title", "owner.firstName"]))).toBe(
      "id title owner { id firstName }",
    );
  });
});

describe("clampPageSize", () => {
  test("clamps to the maximum page size and floors at 1", () => {
    expect(clampPageSize(50)).toBe(50);
    expect(clampPageSize(9999)).toBe(MAX_PAGE_SIZE);
    expect(clampPageSize(0)).toBe(1);
    expect(clampPageSize(-5)).toBe(1);
    expect(clampPageSize(20.7)).toBe(20);
  });
});
