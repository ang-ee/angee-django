import { describe, expect, test } from "vitest";

import {
  extractDeletePreview,
  extractNode,
  extractPage,
  extractRevisions,
  revisionSnapshot,
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

describe("extractRevisions", () => {
  test("returns newest-first revision rows from the single root field", () => {
    const data = {
      saleRevisions: [
        {
          id: "v2",
          createdAt: "2026-01-02T00:00:00Z",
          comment: "second",
          title: "Second",
        },
      ],
    };
    expect(extractRevisions(data)).toEqual([
      {
        id: "v2",
        createdAt: "2026-01-02T00:00:00Z",
        comment: "second",
        title: "Second",
      },
    ]);
  });

  test("returns an empty list when the root field is absent", () => {
    expect(extractRevisions({})).toEqual([]);
  });
});

describe("revisionSnapshot", () => {
  test("returns the first non-envelope, non-null field value", () => {
    expect(
      revisionSnapshot({
        id: "v2",
        createdAt: "2026-01-02T00:00:00Z",
        comment: "second",
        __typename: "NoteRevision",
        title: "Second",
      }),
    ).toBe("Second");
  });

  test("returns an empty string when only envelope fields are present", () => {
    expect(
      revisionSnapshot({ id: "v1", createdAt: "", comment: null }),
    ).toBe("");
  });
});

describe("extractDeletePreview", () => {
  test("returns the delete preview payload with a normalized tree", () => {
    const data = {
      deleteSale: {
        totalDeletedCount: 2,
        hasBlockers: false,
        deleted: [{ label: "sales", count: 1 }],
        updated: [],
        blocked: [],
        root: {
          label: "sale",
          objectLabel: "Sale A",
          objectId: "1",
          children: [
            {
              label: "line items",
              objectLabel: "1 line item",
              objectId: null,
              children: [{ label: "line item", objectLabel: "Line 1", objectId: "7" }],
            },
          ],
        },
      },
    };

    expect(extractDeletePreview(data)).toEqual({
      totalDeletedCount: 2,
      hasBlockers: false,
      deleted: [{ label: "sales", count: 1 }],
      updated: [],
      blocked: [],
      root: {
        label: "sale",
        objectLabel: "Sale A",
        objectId: "1",
        children: [
          {
            label: "line items",
            objectLabel: "1 line item",
            objectId: null,
            children: [
              {
                label: "line item",
                objectLabel: "Line 1",
                objectId: "7",
                children: [],
              },
            ],
          },
        ],
      },
    });
  });

  test("returns null for a malformed preview", () => {
    expect(extractDeletePreview({ deleteSale: { totalDeletedCount: 1 } })).toBeNull();
  });
});
