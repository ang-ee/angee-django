import { describe, expect, test } from "vitest";

import { extractNode, extractPage, type PageInfo } from "./resource-result";

describe("extractNode", () => {
  test("returns the single root field's record", () => {
    const data = { sale: { id: "1", title: "A" } };
    expect(extractNode(data)).toEqual({ id: "1", title: "A" });
  });

  test("returns null for a null or absent root value", () => {
    expect(extractNode({ sale: null })).toBeNull();
    expect(extractNode(undefined)).toBeNull();
    expect(extractNode({})).toBeNull();
  });
});

describe("extractPage", () => {
  test("reads results, totalCount, and the offset pageInfo", () => {
    const pageInfo: PageInfo = { offset: 0, limit: 50 };
    const data = {
      sales: {
        totalCount: 2,
        results: [{ id: "1" }, { id: "2" }],
        pageInfo,
      },
    };
    expect(extractPage(data)).toEqual({
      rows: [{ id: "1" }, { id: "2" }],
      total: 2,
      pageInfo,
    });
  });

  test("returns empty rows and undefined total for an absent page", () => {
    expect(extractPage({})).toEqual({
      rows: [],
      total: undefined,
      pageInfo: undefined,
    });
  });

  test("normalizes a malformed pageInfo to the declared shape", () => {
    const data = {
      sales: {
        totalCount: 0,
        results: [],
        pageInfo: { offset: "x", limit: "y" },
      },
    };
    expect(extractPage(data).pageInfo).toEqual({ offset: 0, limit: null });
  });
});
