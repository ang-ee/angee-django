import { describe, expect, test } from "vitest";

import {
  createLocalRowsDataSource,
  nextRowTextFilter,
  rowTextFilterValue,
} from "./local-rows";

const ROWS = [
  {
    id: "1",
    title: "Alpha",
    status: "ACTIVE",
    count: 4,
    provider: { id: "ipr_anthropic", name: "Anthropic" },
  },
  {
    id: "2",
    title: "Beta",
    status: "DRAFT",
    count: 12,
    provider: { id: "ipr_openai", name: "OpenAI" },
  },
  {
    id: "3",
    title: "Gamma",
    status: "ACTIVE",
    count: 8,
    provider: { id: "ipr_local", name: "Local" },
  },
] as const;

describe("createLocalRowsDataSource", () => {
  test("filters, sorts, and pages local rows through one query", () => {
    const source = createLocalRowsDataSource(ROWS);

    expect(
      source.query({
        filter: { status: { exact: "ACTIVE" } },
        sort: { field: "count", dir: "desc" },
        page: 1,
        pageSize: 1,
      }),
    ).toMatchObject({
      rows: [ROWS[2]],
      total: 2,
      page: 1,
      pageSize: 1,
      pageCount: 2,
      hasNext: true,
      hasPrev: false,
    });
  });

  test("matches free text across declared text fields", () => {
    const source = createLocalRowsDataSource(ROWS);

    expect(
      source.query({
        filter: nextRowTextFilter({}, "alp"),
        textFields: ["title", "provider.name"],
      }).rows,
    ).toEqual([ROWS[0]]);
    expect(rowTextFilterValue(nextRowTextFilter({}, " alpha "))).toBe("alpha");
  });

  test("matches relation public-id lookup filters against local row objects", () => {
    const source = createLocalRowsDataSource(ROWS);

    expect(
      source.query({
        filter: { provider: { sqid: "ipr_anthropic" } },
      }).rows,
    ).toEqual([ROWS[0]]);
  });

  test("supports AND, OR, and NOT branches for local rows", () => {
    const source = createLocalRowsDataSource(ROWS);

    expect(
      source.query({
        filter: {
          AND: [
            { status: { exact: "ACTIVE" } },
            { NOT: { provider: { sqid: "ipr_local" } } },
          ],
        },
      }).rows,
    ).toEqual([ROWS[0]]);
    expect(
      source.query({
        filter: {
          OR: [
            { title: { exact: "Beta" } },
            { count: { gt: 10 } },
          ],
        },
      }).rows,
    ).toEqual([ROWS[1]]);
  });
});
