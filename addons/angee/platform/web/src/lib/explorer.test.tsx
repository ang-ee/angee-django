// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { AuthoredQueryResult } from "@angee/sdk";

import type {
  PlatformAddonData,
  PlatformEdgeData,
  PlatformModelData,
} from "../documents";
import {
  selectPlatformAddonDetail,
  selectPlatformFieldRows,
  selectPlatformModelDetail,
  selectPlatformModelGraph,
  selectPlatformModelRows,
  usePlatformAddon,
  usePlatformAddonRows,
  usePlatformExplorer,
  type PlatformExplorerResult,
} from "./explorer";

const sdkMocks = vi.hoisted(() => ({
  query: null as AuthoredQueryResult<PlatformExplorerResult> | null,
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useAuthoredQuery: () => {
      if (!sdkMocks.query) {
        throw new Error("Missing mocked platform explorer query.");
      }
      return sdkMocks.query;
    },
    useAuthoredRows: (
      _document: unknown,
      options: {
        selectRows: (
          data: PlatformExplorerResult | undefined,
        ) => readonly unknown[];
      },
    ) => {
      if (!sdkMocks.query) {
        throw new Error("Missing mocked platform explorer query.");
      }
      return {
        ...sdkMocks.query,
        rows: options.selectRows(sdkMocks.query.data),
      };
    },
  };
});

beforeEach(() => {
  sdkMocks.query = queryResult(explorerResult());
});

describe("platform explorer selectors", () => {
  test("filters model rows by addon scope", () => {
    const rows = selectPlatformModelRows(explorerResult(), {
      addon: "angee.iam",
    });

    expect(rows.map((row) => row.id)).toEqual(["iam.user"]);
  });

  test("intersects field rows by model and addon scopes", () => {
    const data = explorerResult();

    expect(
      selectPlatformFieldRows(data, {
        model: "iam.user",
        addon: "angee.iam",
      }).map((row) => row.id),
    ).toEqual(["iam.user.id", "iam.user.created_by"]);
    expect(
      selectPlatformFieldRows(data, {
        model: "iam.user",
        addon: "angee.resources",
      }),
    ).toEqual([]);
  });

  test("projects addon dependency detail from known platform addons", () => {
    const detail = selectPlatformAddonDetail(explorerResult(), "angee.iam");

    expect(detail.addon?.label).toBe("iam");
    expect(detail.dependsOn).toEqual(["angee.resources"]);
    expect(detail.dependedBy).toEqual(["angee.operator"]);
    expect(detail.modelLabels).toEqual(["iam.group", "iam.user"]);
  });

  test("projects model reverse dependency detail", () => {
    const detail = selectPlatformModelDetail(explorerResult(), "iam.user");

    expect(detail.model?.modelName).toBe("User");
    expect(detail.dependedBy).toEqual(["operator.task"]);
  });

  test("projects graph nodes and highlights the scoped model", () => {
    const graph = selectPlatformModelGraph(explorerResult(), {
      model: "iam.user",
    });

    expect(graph.nodes.map((node) => node.id)).toEqual([
      "iam.user",
      "operator.task",
    ]);
    expect(graph.nodes[0]?.highlighted).toBe(true);
    expect(graph.edges.map((edge) => edge.id)).toEqual(["operator.task:owner"]);
  });

  test("treats a null explorer payload as an empty platform surface", () => {
    const data: PlatformExplorerResult = { platformExplorer: null };

    expect(selectPlatformModelRows(data)).toEqual([]);
    expect(selectPlatformFieldRows(data)).toEqual([]);
    expect(selectPlatformAddonDetail(data, "angee.iam").addon).toBeUndefined();
    expect(selectPlatformModelGraph(data).nodes).toEqual([]);
  });
});

describe("platform explorer hooks", () => {
  test("exposes the nullable explorer payload", () => {
    sdkMocks.query = queryResult({ platformExplorer: null });

    const { result } = renderHook(() => usePlatformExplorer());

    expect(result.current.explorer).toBeNull();
    expect(result.current.fetching).toBe(false);
  });

  test("projects authored addon rows through the platform owner", () => {
    const { result } = renderHook(() => usePlatformAddonRows());

    expect(result.current.rows.map((row) => row.id)).toEqual([
      "angee.iam",
      "angee.operator",
      "angee.resources",
    ]);
    expect(result.current.error).toBeNull();
  });

  test("preserves loading state for missing detail records", () => {
    sdkMocks.query = queryResult(undefined, { fetching: true });

    const { result } = renderHook(() => usePlatformAddon("angee.iam"));

    expect(result.current.addon).toBeUndefined();
    expect(result.current.notFound).toBe(false);
    expect(result.current.fetching).toBe(true);
  });
});

function queryResult(
  data: PlatformExplorerResult | undefined,
  overrides: Partial<AuthoredQueryResult<PlatformExplorerResult>> = {},
): AuthoredQueryResult<PlatformExplorerResult> {
  return {
    data,
    fetching: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

function explorerResult(): PlatformExplorerResult {
  return {
    platformExplorer: {
      addons,
      models,
      edges,
    },
  };
}

const addons: PlatformAddonData[] = [
  {
    id: "angee.iam",
    label: "iam",
    namespace: "angee",
    kind: "required",
    modelCount: 2,
    fieldCount: 3,
    resourceCount: 1,
    dependsOn: ["angee.resources", "django.contrib.auth"],
    modelLabels: ["iam.user", "iam.group", "iam.user"],
  },
  {
    id: "angee.operator",
    label: "operator",
    namespace: "angee",
    kind: "optional",
    modelCount: 1,
    fieldCount: 1,
    resourceCount: 0,
    dependsOn: ["angee.iam"],
    modelLabels: ["operator.task"],
  },
  {
    id: "angee.resources",
    label: "resources",
    namespace: "angee",
    kind: "required",
    modelCount: 0,
    fieldCount: 0,
    resourceCount: 0,
    dependsOn: [],
    modelLabels: [],
  },
];

const models: PlatformModelData[] = [
  {
    label: "iam.user",
    appLabel: "iam",
    modelName: "User",
    verboseName: "user",
    dbTable: "iam_user",
    addonId: "angee.iam",
    addonLabel: "iam",
    resourceType: "auth/user",
    fieldCount: 2,
    relationCount: 1,
    dependsOn: [],
    fields: [
      {
        name: "id",
        attname: "id",
        kind: "BigAutoField",
        isRelation: false,
        relationTarget: null,
        addon: "iam",
      },
      {
        name: "created_by",
        attname: "created_by_id",
        kind: "ForeignKey",
        isRelation: true,
        relationTarget: "iam.user",
        addon: "iam",
      },
    ],
  },
  {
    label: "operator.task",
    appLabel: "operator",
    modelName: "Task",
    verboseName: "task",
    dbTable: "operator_task",
    addonId: "angee.operator",
    addonLabel: "operator",
    resourceType: null,
    fieldCount: 1,
    relationCount: 1,
    dependsOn: ["iam.user"],
    fields: [
      {
        name: "owner",
        attname: "owner_id",
        kind: "ForeignKey",
        isRelation: true,
        relationTarget: "iam.user",
        addon: "operator",
      },
    ],
  },
];

const edges: PlatformEdgeData[] = [
  {
    id: "operator.task:owner",
    source: "operator.task",
    target: "iam.user",
    kind: "relation",
    fieldName: "owner",
  },
];
