// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { AuthoredQueryResult } from "@angee/refine";

import type {
  PlatformAddonData,
  PlatformEdgeData,
  PlatformModelData,
} from "../documents";
import {
  selectPlatformAddonDetail,
  selectPlatformModelDetail,
  selectPlatformModelGraph,
  usePlatformAddon,
  usePlatformExplorer,
  type PlatformExplorerResult,
} from "./explorer";

const sdkMocks = vi.hoisted(() => ({
  query: null as AuthoredQueryResult<PlatformExplorerResult> | null,
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: () => {
      if (!sdkMocks.query) {
        throw new Error("Missing mocked platform explorer query.");
      }
      return sdkMocks.query;
    },
  };
});

beforeEach(() => {
  sdkMocks.query = queryResult(explorerResult());
});

describe("platform explorer selectors", () => {
  test("projects addon dependency detail from known platform addons", () => {
    const detail = selectPlatformAddonDetail(explorerResult(), "angee.iam");

    expect(detail.addon?.label).toBe("iam");
    expect(detail.dependsOn).toEqual(["angee.resources"]);
    expect(detail.dependedBy).toEqual(["angee.operator"]);
    expect(detail.modelLabels).toEqual(["iam.group", "iam.user"]);
  });

  test("projects model reverse dependency detail", () => {
    const detail = selectPlatformModelDetail(explorerResult(), "iam.user");

    expect(detail.model?.model_name).toBe("User");
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
    const data: PlatformExplorerResult = { platform_explorer: null };

    expect(selectPlatformAddonDetail(data, "angee.iam").addon).toBeUndefined();
    expect(selectPlatformModelGraph(data).nodes).toEqual([]);
  });
});

describe("platform explorer hooks", () => {
  test("exposes the nullable explorer payload", () => {
    sdkMocks.query = queryResult({ platform_explorer: null });

    const { result } = renderHook(() => usePlatformExplorer());

    expect(result.current.explorer).toBeNull();
    expect(result.current.fetching).toBe(false);
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
    platform_explorer: {
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
    model_count: 2,
    field_count: 3,
    resource_count: 1,
    depends_on: ["angee.resources", "django.contrib.auth"],
    model_labels: ["iam.user", "iam.group", "iam.user"],
  },
  {
    id: "angee.operator",
    label: "operator",
    namespace: "angee",
    kind: "optional",
    model_count: 1,
    field_count: 1,
    resource_count: 0,
    depends_on: ["angee.iam"],
    model_labels: ["operator.task"],
  },
  {
    id: "angee.resources",
    label: "resources",
    namespace: "angee",
    kind: "required",
    model_count: 0,
    field_count: 0,
    resource_count: 0,
    depends_on: [],
    model_labels: [],
  },
];

const models: PlatformModelData[] = [
  {
    label: "iam.user",
    app_label: "iam",
    model_name: "User",
    verbose_name: "user",
    db_table: "iam_user",
    addon_id: "angee.iam",
    addon_label: "iam",
    resource_type: "auth/user",
    field_count: 2,
    relation_count: 1,
    depends_on: [],
    fields: [
      {
        name: "id",
        attname: "id",
        kind: "BigAutoField",
        is_relation: false,
        relation_target: null,
        addon: "iam",
      },
      {
        name: "created_by",
        attname: "created_by_id",
        kind: "ForeignKey",
        is_relation: true,
        relation_target: "iam.user",
        addon: "iam",
      },
    ],
  },
  {
    label: "operator.task",
    app_label: "operator",
    model_name: "Task",
    verbose_name: "task",
    db_table: "operator_task",
    addon_id: "angee.operator",
    addon_label: "operator",
    resource_type: null,
    field_count: 1,
    relation_count: 1,
    depends_on: ["iam.user"],
    fields: [
      {
        name: "owner",
        attname: "owner_id",
        kind: "ForeignKey",
        is_relation: true,
        relation_target: "iam.user",
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
    field_name: "owner",
  },
];
