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
      {
        field: "nestedOwner",
        input: "nestedOwner",
        key: "nestedOwner",
        kind: "column",
      },
    ],
  },
} as unknown as ModelMetadata;

function params(overrides: Partial<GroupedRenderParams> = {}): GroupedRenderParams {
  return {
    groupStack: [{ field: "status" }, { field: "owner" }],
    baseFilter: undefined,
    expandedKeys: new Set(),
    paginationByScope: {},
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
        paginationByScope: { [childKey]: { pageIndex: 8, pageSize: 2 } },
      }),
    );

    expect(model.items.find((item) => item.kind === "groupHeader")?.pager).toMatchObject({
      pageKey: childKey,
      page: 3,
      pageSize: 2,
      total: 5,
      unit: "groups",
    });
    expect(model.items.map((item) => item.kind)).toEqual(["groupHeader", "groupHeader"]);
  });

  test("keeps leaf pagination in its header while fetching and clamps before querying", () => {
    const leafParams = params({ groupStack: [{ field: "status" }] });
    const initial = buildGroupedRenderModel<Row>(new Map(), EMPTY_LEAVES, EMPTY_ROWS, leafParams);
    const rootKey = initial.groupScopes[0]!.key;
    const results = new Map([[rootKey, result([{ key: { status: "ACTIVE" }, count: 45 }])]]);
    const collapsed = buildGroupedRenderModel<Row>(results, EMPTY_LEAVES, EMPTY_ROWS, leafParams);
    const header = collapsed.items.find((item) => item.kind === "groupHeader")!;
    expect(header.pager).toBeUndefined();
    expect(collapsed.leafScopes).toEqual([]);

    const expanded = buildGroupedRenderModel<Row>(results, EMPTY_LEAVES, EMPTY_ROWS, {
      ...leafParams,
      expandedKeys: new Set([header.bucketKey]),
      paginationByScope: { [header.bucketKey]: { pageIndex: 8, pageSize: 20 } },
    });
    expect(expanded.items.map((item) => item.kind)).toEqual(["groupHeader", "skeleton"]);
    expect(expanded.items.find((item) => item.kind === "groupHeader")?.pager).toEqual({
      pageKey: header.bucketKey,
      page: 3,
      pageSize: 20,
      total: 45,
      unit: "records",
      pending: true,
    });
    expect(expanded.leafScopes[0]?.page).toBe(3);
  });

  test("retains the parent pager when an out-of-range subgroup page is empty", () => {
    const { rootKey, rootResult, bucketKey } = rootFixture();
    const expandedParams = params({ expandedKeys: new Set([bucketKey]) });
    const frontier = buildGroupedRenderModel<Row>(
      new Map([[rootKey, rootResult]]), EMPTY_LEAVES, EMPTY_ROWS, expandedParams,
    );
    const childKey = frontier.groupScopes[1]!.key;
    const model = buildGroupedRenderModel<Row>(
      new Map([[rootKey, rootResult], [childKey, result([], { totalCount: 5 })]]),
      EMPTY_LEAVES, EMPTY_ROWS,
      { ...expandedParams, paginationByScope: { [childKey]: { pageIndex: 8, pageSize: 2 } } },
    );
    expect(model.items.find((item) => item.kind === "groupHeader")?.pager).toMatchObject({
      pageKey: childKey, page: 3, total: 5, pending: false,
    });
  });

  test("renders a filter-less group dimension as a non-expandable bucket", () => {
    const filterlessParams = params({
      groupStack: [{ field: "nestedOwner" }],
    });
    const initial = buildGroupedRenderModel<Row>(
      new Map(),
      EMPTY_LEAVES,
      EMPTY_ROWS,
      filterlessParams,
    );
    const rootKey = initial.groupScopes[0]?.key ?? "";
    const rootResult = result([
      { key: { nestedOwner: "Portfolio" }, count: 3 },
    ]);
    const model = buildGroupedRenderModel<Row>(
      new Map([[rootKey, rootResult]]),
      EMPTY_LEAVES,
      EMPTY_ROWS,
      {
        ...filterlessParams,
        expandedKeys: new Set(["any-requested-bucket"]),
      },
    );

    expect(model.groupScopes).toHaveLength(1);
    expect(model.leafScopes).toEqual([]);
    expect(model.items).toEqual([
      expect.objectContaining({
        kind: "groupHeader",
        label: "Portfolio",
        count: 3,
        expandable: false,
        expanded: false,
      }),
    ]);
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


test("subgroup page sizes keep stable scope identity and feed the native request", () => {
  const { rootKey, rootResult, bucketKey } = rootFixture();
  const expanded = params({ expandedKeys: new Set([bucketKey]) });
  const results = new Map([[rootKey, rootResult]]);
  const before = buildGroupedRenderModel<Row>(results, EMPTY_LEAVES, EMPTY_ROWS, expanded);
  const childKey = before.groupScopes[1]!.key;
  const after = buildGroupedRenderModel<Row>(results, EMPTY_LEAVES, EMPTY_ROWS, {
    ...expanded,
    paginationByScope: { [childKey]: { pageIndex: 0, pageSize: 50 } },
  });
  expect(after.groupScopes[0]).toEqual(before.groupScopes[0]);
  expect(after.groupScopes[1]).toMatchObject({ key: childKey, query: { page: 1, pageSize: 50 } });
  expect(after.items.find((item) => item.kind === "groupHeader")?.pager).toMatchObject({
    pageKey: childKey, page: 1, pageSize: 50, unit: "groups",
  });
});

test("leaf page size is shared by its query and header pager", () => {
  const leafParams = params({ groupStack: [{ field: "status" }] });
  const initial = buildGroupedRenderModel<Row>(new Map(), EMPTY_LEAVES, EMPTY_ROWS, leafParams);
  const rootKey = initial.groupScopes[0]!.key;
  const results = new Map([[rootKey, result([{ key: { status: "ACTIVE" }, count: 145 }])]]);
  const collapsed = buildGroupedRenderModel<Row>(results, EMPTY_LEAVES, EMPTY_ROWS, leafParams);
  const header = collapsed.items.find((item) => item.kind === "groupHeader")!;
  const expanded = buildGroupedRenderModel<Row>(results, EMPTY_LEAVES, EMPTY_ROWS, {
    ...leafParams,
    expandedKeys: new Set([header.bucketKey]),
    paginationByScope: { [header.bucketKey]: { pageIndex: 1, pageSize: 50 } },
  });
  expect(expanded.leafScopes[0]).toMatchObject({ key: header.bucketKey, page: 2, pageSize: 50 });
  expect(expanded.items.find((item) => item.kind === "groupHeader")?.pager).toMatchObject({
    pageKey: header.bucketKey, page: 2, pageSize: 50, unit: "records",
  });
});
