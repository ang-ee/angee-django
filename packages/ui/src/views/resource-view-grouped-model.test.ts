import { describe, expect, test, vi } from "vitest";
import type { UseAngeeGroupByResult } from "@angee/refine";
import type { ModelMetadata, Row } from "@angee/metadata";

import {
  buildGroupedRenderModel,
  type GroupedRenderParams,
} from "./resource-view-grouped-model";

const EMPTY_LEAVES = new Map();
const EMPTY_ROWS = new Map();
const TEST_METADATA = {
  typeName: "TestRow",
  fields: {},
  rootFields: {},
  resource: {
    groupDimensions: [
      {
        field: "status",
        input: "status",
        key: "status",
        kind: "column",
        filter: {
          kind: "equality",
          field: "status",
          valueKey: "status",
        },
      },
      {
        field: "owner",
        input: "owner",
        key: "owner",
        kind: "column",
        filter: {
          kind: "equality",
          field: "owner",
          valueKey: "owner",
        },
      },
    ],
  },
} as unknown as ModelMetadata;

function params(overrides: Partial<GroupedRenderParams> = {}): GroupedRenderParams {
  return {
    groupStack: [{ field: "status" }, { field: "owner" }],
    baseFilter: undefined,
    expandedKeys: new Set(),
    pageByScope: {},
    rootPage: 1,
    pageSize: 2,
    queryMeasures: [],
    leafOrder: undefined,
    modelMetadata: TEST_METADATA,
    emptyGroupMessage: "No records",
    emptySubgroupsMessage: "No subgroups",
    emptyValueLabel: "Empty",
    emptyRelationLabel: (field) => `No ${field}`,
    allRecordsLabel: "All records",
    t: (key, vars) => {
      if (key === "list.quarter") return `Q${vars?.quarter} ${vars?.year}`;
      if (key === "list.weekOf") return `Week of ${vars?.date}`;
      return key;
    },
    ...overrides,
  };
}

function result(
  buckets: UseAngeeGroupByResult["buckets"],
  overrides: Partial<UseAngeeGroupByResult> = {},
): UseAngeeGroupByResult {
  return {
    count: buckets.reduce((total, bucket) => total + bucket.count, 0),
    totalCount: buckets.length,
    buckets,
    fetching: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

function rootFixture() {
  const initial = buildGroupedRenderModel<Row>(
    new Map(),
    EMPTY_LEAVES,
    EMPTY_ROWS,
    params(),
  );
  const rootKey = initial.groupScopes[0]?.key ?? "";
  const rootResult = result([{ key: { status: "ACTIVE" }, count: 4 }]);
  const collapsed = buildGroupedRenderModel<Row>(
    new Map([[rootKey, rootResult]]),
    EMPTY_LEAVES,
    EMPTY_ROWS,
    params(),
  );
  const header = collapsed.items.find((item) => item.kind === "groupHeader");
  if (!header || header.kind !== "groupHeader") throw new Error("missing root header");
  return { rootKey, rootResult, bucketKey: header.bucketKey };
}

describe("buildGroupedRenderModel", () => {
  test("grows the request frontier only through expanded resolved buckets", () => {
    const { rootKey, rootResult, bucketKey } = rootFixture();
    const expanded = buildGroupedRenderModel<Row>(
      new Map([[rootKey, rootResult]]),
      EMPTY_LEAVES,
      EMPTY_ROWS,
      params({ expandedKeys: new Set([bucketKey]) }),
    );

    expect(expanded.groupScopes).toHaveLength(2);
    expect(expanded.items.map((item) => item.kind)).toEqual([
      "groupHeader",
      "skeleton",
    ]);
  });

  test("clamps nested pager windows to the available group pages", () => {
    const { rootKey, rootResult, bucketKey } = rootFixture();
    const frontier = buildGroupedRenderModel<Row>(
      new Map([[rootKey, rootResult]]),
      EMPTY_LEAVES,
      EMPTY_ROWS,
      params({ expandedKeys: new Set([bucketKey]) }),
    );
    const childKey = frontier.groupScopes[1]?.key ?? "";
    const childResult = result(
      [{ key: { owner: "person_1" }, count: 1 }],
      { totalCount: 5 },
    );
    const model = buildGroupedRenderModel<Row>(
      new Map([[rootKey, rootResult], [childKey, childResult]]),
      EMPTY_LEAVES,
      EMPTY_ROWS,
      params({
        expandedKeys: new Set([bucketKey]),
        pageByScope: { [childKey]: 9 },
      }),
    );

    expect(model.items.find((item) => item.kind === "pager")).toMatchObject({
      kind: "pager",
      page: 3,
      pageSize: 2,
      total: 5,
      unit: "groups",
    });
  });

  test.each([
    ["loading", undefined, "skeleton", undefined],
    [
      "error",
      result([], { error: Object.assign(new Error("Broken"), { statusCode: 500 }) }),
      "status",
      "Broken",
    ],
    ["empty", result([]), "status", "No subgroups"],
  ] as const)(
    "emits the nested %s state",
    (_label, childResult, expectedKind, expectedMessage) => {
      const { rootKey, rootResult, bucketKey } = rootFixture();
      const frontier = buildGroupedRenderModel<Row>(
        new Map([[rootKey, rootResult]]),
        EMPTY_LEAVES,
        EMPTY_ROWS,
        params({ expandedKeys: new Set([bucketKey]) }),
      );
      const childKey = frontier.groupScopes[1]?.key ?? "";
      const results = new Map([[rootKey, rootResult]]);
      if (childResult) results.set(childKey, childResult);
      const model = buildGroupedRenderModel<Row>(
        results,
        EMPTY_LEAVES,
        EMPTY_ROWS,
        params({ expandedKeys: new Set([bucketKey]) }),
      );
      const stateItem = model.items[1];

      expect(stateItem?.kind).toBe(expectedKind);
      if (expectedMessage && stateItem?.kind === "status") {
        expect(stateItem.message).toBe(expectedMessage);
      }
    },
  );
});
