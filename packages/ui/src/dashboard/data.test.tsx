// @vitest-environment happy-dom

import { cleanup, renderHook } from "@testing-library/react";
import type { DataResourceMetadata } from "@angee/metadata";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import type { AggregateBucket } from "@angee/refine";

import { useDashboardWidgetData } from "./data";
import type { WidgetSpec } from "./headless";

const state = vi.hoisted(() => ({
  resource: null as DataResourceMetadata | null,
  list: vi.fn(),
  refetch: vi.fn(),
  aggregate: null as AggregateBucket | null,
}));

vi.mock("@angee/metadata", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/metadata")>(),
  useModelMetadata: () => state.resource ? { resource: state.resource } : null,
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAngeeAggregate: () => ({ aggregate: state.aggregate, fetching: false, error: null, updatedAt: null, refetch: state.refetch }),
  useAngeeGroupBy: () => ({ buckets: [], totalCount: 0, fetching: false, error: null, updatedAt: null, refetch: state.refetch }),
}));
vi.mock("../views/resource/resource-operations", () => ({
  useAggregateOperation: () => ({ target: null }),
  useGroupOperation: () => ({ target: null }),
}));
vi.mock("../views/resource/surface/resource-list-query", () => ({
  useResourceListQuery: (options: unknown) => {
    state.list(options);
    return { result: { data: [] }, query: { isFetching: false, error: null, dataUpdatedAt: 0, refetch: state.refetch } };
  },
}));

const spec: WidgetSpec = {
  schemaVersion: 1, id: "rows", kind: "table", kindVersion: 1, title: "Messages",
  data: { shape: "rows", source: { resource: "messaging.Message", fields: ["count", "channel"] } },
  options: {}, x: 0, y: 0, w: 6, h: 4, isArchived: false,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  state.resource = null;
  state.aggregate = null;
});

test("selects native row identity alongside requested fields and declared relation labels", () => {
  const query = testResourceQuery({
    identity: { field: "public_key" },
    fields: {
      public_key: testQueryField("public_key", { scalar: "ID" }),
      count: testQueryField("message_count", { scalar: "Int" }),
      channel: testQueryField("channel.public_key", {
        kind: "relation", scalar: "ID",
        relation: { model: "messaging.Channel", identityPath: "channel.public_key", labelPath: "channel.display_name" },
      }),
    },
  });
  state.resource = testDataResource("messaging.Message", { query });
  const { result } = renderHook(() => useDashboardWidgetData(spec));

  expect(state.list).toHaveBeenCalledWith(expect.objectContaining({
    fields: ["public_key", "message_count", "channel.public_key", "channel.display_name"],
  }));
  expect(result.current.identity).toEqual(query.identity);
  expect(result.current.queryFields).toEqual(query.fields);
});

test("reuses the empty query field map while metadata is unavailable", () => {
  const { result, rerender } = renderHook(() => useDashboardWidgetData(spec));
  const fields = result.current.queryFields;
  rerender();
  expect(result.current.queryFields).toBe(fields);
  expect(result.current.identity).toBeNull();
});

test("a reader-relative task filter stays intact in the dashboard row query", () => {
  state.resource = testDataResource("projects.Task", { query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID" }),
    waiting_for: testQueryField("waiting_for"),
  } }) });
  const waiting: WidgetSpec = { ...spec, data: { shape: "rows", source: {
    resource: "projects.Task", fields: ["id"], filter: { waiting_for: { exact: "me" } },
  } } };
  renderHook(() => useDashboardWidgetData(waiting));
  expect(state.list).toHaveBeenCalledWith(expect.objectContaining({
    scope: expect.objectContaining({ filter: { waiting_for: { exact: "me" } } }),
  }));
});

test("a value measure is a heading count only for the count operation", () => {
  state.resource = testDataResource("messaging.Message", {
    query: testResourceQuery({ fields: { id: testQueryField("id") } }),
    aggregateMeasures: [{ op: "sum", field: "amount", input: "AMOUNT" }],
  });
  state.aggregate = { key: null, count: 5, sum: { amount: 42 } };
  const countSpec: WidgetSpec = { ...spec, kind: "stat", data: {
    shape: "value", source: { resource: "messaging.Message", measure: { op: "count" } },
  } };
  const { result, rerender } = renderHook(({ widget }) => useDashboardWidgetData(widget), {
    initialProps: { widget: countSpec },
  });
  expect(result.current.value).toBe(5);
  expect(result.current.count).toBe(5);
  rerender({ widget: { ...countSpec, data: { shape: "value", source: {
    resource: "messaging.Message", measure: { op: "sum", field: "amount" },
  } } } });
  expect(result.current.value).toBe(42);
  expect(result.current.count).toBeNull();
});
