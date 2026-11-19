// @vitest-environment happy-dom

import { cleanup, renderHook } from "@testing-library/react";
import type { DataResourceMetadata } from "@angee/metadata";
import { testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import type { AggregateBucket } from "@angee/refine";

import { useDashboardWidgetData } from "./data";
import type { WidgetSpec } from "./headless";

const state = vi.hoisted(() => ({
  resource: null as DataResourceMetadata | null,
  list: vi.fn(),
  aggregateOptions: vi.fn(),
  groupOptions: vi.fn(),
  refetch: vi.fn(),
  aggregate: null as AggregateBucket | null,
}));

vi.mock("@angee/metadata", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/metadata")>(),
  useModelMetadata: () => state.resource ? { resource: state.resource } : null,
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAngeeAggregate: (_target: unknown, options: unknown) => {
    state.aggregateOptions(options);
    return { aggregate: state.aggregate, fetching: false, error: null, updatedAt: null, refetch: state.refetch };
  },
  useAngeeGroupBy: (_target: unknown, options: unknown) => {
    state.groupOptions(options);
    return { buckets: [], totalCount: 0, fetching: false, error: null, updatedAt: null, refetch: state.refetch };
  },
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

test("dashboard refresh declarations reach native query adapters through unrelated renders", () => {
  const widget: WidgetSpec = { ...spec, data: { shape: "rows", source: {
    resource: "messaging.Message", refresh: { mode: "interval", seconds: 60 },
  } } };
  const { rerender } = renderHook(({ widget }) => useDashboardWidgetData(widget), { initialProps: { widget } });
  for (const adapter of [state.list, state.aggregateOptions, state.groupOptions]) {
    expect(adapter).toHaveBeenLastCalledWith(expect.objectContaining({ refetchInterval: 60_000 }));
  }
  rerender({ widget: { ...widget, title: "Unrelated title" } });
  expect(state.list).toHaveBeenLastCalledWith(expect.objectContaining({ refetchInterval: 60_000 }));
  rerender({ widget: { ...widget, data: { shape: "rows", source: {
    resource: "messaging.Message", refresh: { mode: "manual" },
  } } } });
  for (const adapter of [state.list, state.aggregateOptions, state.groupOptions]) {
    expect(adapter).toHaveBeenLastCalledWith(expect.objectContaining({ refetchInterval: false }));
  }
});

const archiveResource = testDataResource("example.Record", {
  fields: [{ name: "is_archived", kind: "scalar", scalar: "Boolean", archivable: true,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  query: testResourceQuery({ fields: {
    id: testQueryField("id"), name: testQueryField("name"),
    is_archived: testQueryField("is_archived", { scalar: "Boolean",
      filter: { field: "is_archived", scalar: "Boolean", operators: ["exact"], values: [] } }),
  }, axes: { name: testQueryAxis("name", { server: { input: "NAME", key: "name" } }) } }),
});

test.each(["rows", "value", "series"] as const)("dashboard %s use the default archive scope", (shape) => {
  state.resource = archiveResource;
  const widget: WidgetSpec = { ...spec, data: { shape, source: {
    resource: archiveResource.modelLabel, groups: shape === "series" ? [{ field: "name" }] : [],
  } } };
  const { result } = renderHook(() => useDashboardWidgetData(widget));
  expect(result.current.error).toBeNull();
  for (const adapter of [state.aggregateOptions, state.groupOptions]) {
    expect(adapter).toHaveBeenLastCalledWith(expect.objectContaining({ where: { is_archived: { _eq: false } } }));
  }
  if (shape === "rows") expect(state.list).toHaveBeenLastCalledWith(expect.objectContaining({
    scope: expect.objectContaining({ filter: { is_archived: { exact: false } } }),
  }));
});

test.each(["source", "page"] as const)("a dashboard %s filter can request archived rows", (owner) => {
  state.resource = archiveResource;
  const filter = { NOT: { is_archived: { exact: false } } };
  const widget: WidgetSpec = { ...spec, data: { shape: "rows", source: {
    resource: archiveResource.modelLabel, filter: owner === "source" ? filter : undefined,
  } } };
  const { result } = renderHook(() => useDashboardWidgetData(widget, owner === "page"
    ? { resource: archiveResource.modelLabel, filter } : undefined));
  expect(result.current.error).toBeNull();
  expect(state.list).toHaveBeenLastCalledWith(expect.objectContaining({ scope: expect.objectContaining({ filter }) }));
  expect(state.aggregateOptions).toHaveBeenLastCalledWith(expect.objectContaining({
    where: { _not: { is_archived: { _eq: false } } },
  }));
});
