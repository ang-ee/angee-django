import { describe, expect, test } from "vitest";

import {
  extractConnection,
  extractNode,
  type PageInfo,
} from "./resource-result";

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

describe("extractConnection", () => {
  test("flattens edges to nodes and reads totalCount/pageInfo", () => {
    const pageInfo: PageInfo = { endCursor: "c2", hasNextPage: true };
    const data = {
      sales: {
        totalCount: 2,
        edges: [{ node: { id: "1" } }, { node: { id: "2" } }],
        pageInfo,
      },
    };
    expect(extractConnection(data)).toEqual({
      rows: [{ id: "1" }, { id: "2" }],
      total: 2,
      pageInfo,
    });
  });

  test("returns empty rows and undefined total for an absent connection", () => {
    expect(extractConnection({})).toEqual({
      rows: [],
      total: undefined,
      pageInfo: undefined,
    });
  });
});
