// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { UseQueryResult } from "@tanstack/react-query";

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
  query: null as UseQueryResult<PlatformExplorerResult> | null,
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
    expect(graph.nodes[0]?.selected).toBe(true);
    expect(graph.edges.map((edge) => edge.id)).toEqual(["operator.task:owner"]);
  });

  test("selects deterministic models and keeps isolated selections", () => {
    const data = explorerResult();
    data.platform_explorer!.models = [...models].reverse();
    data.platform_explorer!.edges = [];
    const fallback = selectPlatformModelGraph(data, { model: "missing" });
    expect(fallback.model?.label).toBe("iam.user");
    expect(fallback.nodes.map((node) => node.id)).toEqual(["iam.user"]);
    expect(selectPlatformModelGraph(data, { model: "operator.task" }).nodes.map((node) => node.id))
      .toEqual(["operator.task"]);
  });

  test("keeps only incident edges, preserving direction, parallel edges and self loops", () => {
    const data = explorerResult();
    data.platform_explorer!.models = [...models, { ...models[1]!, label: "other.model" }];
    data.platform_explorer!.edges = [
      ...edges,
      { ...edges[0]!, id: "parallel", source: "iam.user", target: "operator.task" },
      { ...edges[0]!, id: "self", source: "iam.user", target: "iam.user" },
      { ...edges[0]!, id: "peer", source: "operator.task", target: "other.model" },
    ];
    const graph = selectPlatformModelGraph(data, { model: "iam.user" });
    expect(graph.edges.map((edge) => edge.id)).toEqual(["operator.task:owner", "parallel", "self"]);
    expect(graph.edges[0]?.source).toBe("operator.task");
    expect(graph.nodes.map((node) => node.id)).toEqual(["iam.user", "operator.task"]);
    expect(graph.relationTotal).toBe(3);
  });

  test("pages every incident relationship with a fixed bound and clamps invalid pages", () => {
    const data = explorerResult();
    const peers = Array.from({ length: 103 }, (_, index) => ({
      ...models[1]!, label: `peer.${String(index).padStart(3, "0")}`,
    }));
    data.platform_explorer!.models = [models[0]!, ...peers];
    data.platform_explorer!.edges = peers.map((peer) => ({
      ...edges[0]!, id: peer.label, source: "iam.user", target: peer.label,
    })).reverse();
    const pages = [1, 2, 3].map((relationPage) => selectPlatformModelGraph(data, { relationPage }));
    expect(pages.map((page) => page.edges.length)).toEqual([50, 50, 3]);
    expect(pages.map((page) => page.nodes.length)).toEqual([51, 51, 4]);
    expect(new Set(pages.flatMap((page) => page.edges.map((edge) => edge.id))).size).toBe(103);
    expect(pages[0]?.relationTotal).toBe(103);
    expect(selectPlatformModelGraph(data, { relationPage: 99 }).relationPage).toBe(3);
    for (const relationPage of [0, -1, NaN, 1.5, Infinity]) {
      expect(selectPlatformModelGraph(data, { relationPage }).relationPage).toBe(1);
    }
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
    expect(result.current.isFetching).toBe(false);
  });

  test("preserves loading state for missing detail records", () => {
    sdkMocks.query = queryResult(undefined, { isFetching: true });

    const { result } = renderHook(() => usePlatformAddon("angee.iam"));

    expect(result.current.addon).toBeUndefined();
    expect(result.current.notFound).toBe(false);
    expect(result.current.isFetching).toBe(true);
  });
});

function queryResult(
  data: PlatformExplorerResult | undefined,
  overrides: Partial<UseQueryResult<PlatformExplorerResult>> = {},
): UseQueryResult<PlatformExplorerResult> {
  return {
    data,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as UseQueryResult<PlatformExplorerResult>;
}

function explorerResult(): PlatformExplorerResult {
  return {
    platform_explorer: {
      pending_addon_changes: false,
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
    kind: "REQUIRED",
    model_count: 2,
    field_count: 3,
    resource_count: 1,
    depends_on: ["angee.resources", "django.contrib.auth"],
    depended_by: ["angee.operator"],
    model_labels: ["iam.user", "iam.group", "iam.user"],
  },
  {
    id: "angee.operator",
    label: "operator",
    namespace: "angee",
    kind: "CONSUMER",
    model_count: 1,
    field_count: 1,
    resource_count: 0,
    depends_on: ["angee.iam"],
    depended_by: [],
    model_labels: ["operator.task"],
  },
  {
    id: "angee.resources",
    label: "resources",
    namespace: "angee",
    kind: "REQUIRED",
    model_count: 0,
    field_count: 0,
    resource_count: 0,
    depends_on: [],
    depended_by: ["angee.iam"],
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
