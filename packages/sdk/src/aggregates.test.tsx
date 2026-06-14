// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { fetchExchange } from "@urql/core";
import { describe, expect, test, vi } from "vitest";

import { GraphQLClientProvider } from "./graphql-provider";
import {
  bucketKey,
  useResourceAggregate,
  useResourceGroupBy,
} from "./aggregates";
import type { AggregateBucket } from "./aggregate-extract";
import { TEST_SCHEMA_SDL } from "./test-schema";

describe("bucketKey", () => {
  const bucket: AggregateBucket = { key: { status: "open" }, count: 3 };

  test("reads the value under the dimension's key (key ?? field)", () => {
    expect(bucketKey(bucket, { field: "STATUS", key: "status" })).toBe("open");
    expect(bucketKey({ key: { STATUS: "x" }, count: 1 }, { field: "STATUS" }))
      .toBe("x");
  });

  test("returns null when the key is missing or absent", () => {
    expect(bucketKey(bucket, { field: "OWNER", key: "owner" })).toBeNull();
    expect(bucketKey({ key: null, count: 0 }, { field: "STATUS" })).toBeNull();
  });
});

function mockTransport(payload: unknown) {
  const bodies: Array<{ query: string; variables: Record<string, unknown> }> = [];
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    if (String(url).includes("/csrf/")) {
      return new Response(JSON.stringify({ token: "t" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (init?.body) bodies.push(JSON.parse(String(init.body)));
    return new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  return { fetch: fetch as unknown as typeof globalThis.fetch, bodies };
}

function wrapperWith(fetch: typeof globalThis.fetch) {
  return ({ children }: { children: ReactNode }) =>
    createElement(GraphQLClientProvider, {
      config: {
        public: {
          url: "/graphql/",
          sdl: TEST_SCHEMA_SDL,
          fetch,
          exchanges: [fetchExchange],
        },
      },
      schema: "public",
      children,
    });
}

function compactGraphQL(query: string | undefined): string {
  return query?.replace(/\s+/g, " ").trim() ?? "";
}

describe("useResourceAggregate", () => {
  test("returns the ungrouped count bucket", async () => {
    const { fetch, bodies } = mockTransport({ saleAggregate: { count: 6 } });
    const { result } = renderHook(() => useResourceAggregate("Sale"), {
      wrapper: wrapperWith(fetch),
    });
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.aggregate?.count).toBe(6);
    expect(bodies[0]?.query).toContain("saleAggregate");
  });

  test("passes a filter to the aggregate field", async () => {
    const { fetch, bodies } = mockTransport({ saleAggregate: { count: 2 } });
    const filter = { state: { exact: "OPEN" } };
    const { result } = renderHook(
      () => useResourceAggregate("Sale", { filter }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.aggregate?.count).toBe(2);
    expect(bodies[0]?.variables.filter).toEqual(filter);
    expect(bodies[0]?.query).toContain("saleAggregate(filter: $filter)");
  });

  test("requests and returns ungrouped measures", async () => {
    const { fetch, bodies } = mockTransport({
      saleAggregate: { count: 6, sum: { amount: "120" } },
    });
    const { result } = renderHook(
      () =>
        useResourceAggregate("Sale", {
          measures: [{ op: "sum", field: "amount" }],
        }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.aggregate?.sum?.amount).toBe("120");
    expect(compactGraphQL(bodies[0]?.query)).toContain(
      "saleAggregate { count sum { amount } }",
    );
  });
});

describe("useResourceGroupBy", () => {
  test("returns buckets keyed by the grouped dimension", async () => {
    const { fetch, bodies } = mockTransport({
      saleGroups: {
        totalCount: 2,
        results: [
          { count: 3, key: { state: "OPEN" } },
          { count: 2, key: { state: "CLOSED" } },
        ],
      },
    });
    const { result } = renderHook(
      () =>
        useResourceGroupBy("Sale", {
          dimensions: [{ field: "STATE", key: "state" }],
        }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.count).toBe(5);
    expect(result.current.totalCount).toBe(2);
    expect(result.current.buckets.map((bucket) => bucket.key?.state)).toEqual([
      "OPEN",
      "CLOSED",
    ]);
    expect(bodies[0]?.variables.groupBy).toEqual([{ field: "STATE" }]);
    expect(bodies[0]?.variables.pagination).toBeNull();
    expect(bodies[0]?.query).toContain("saleGroups(groupBy:");
  });

  test("passes a filter to grouped aggregate buckets", async () => {
    const { fetch, bodies } = mockTransport({
      saleGroups: {
        totalCount: 1,
        results: [{ count: 3, key: { state: "OPEN" } }],
      },
    });
    const filter = { title: { iContains: "launch" } };
    const { result } = renderHook(
      () =>
        useResourceGroupBy("Sale", {
          dimensions: [{ field: "STATE", key: "state" }],
          filter,
          page: 2,
          pageSize: 10,
        }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.totalCount).toBe(1);
    expect(bodies[0]?.variables.filter).toEqual(filter);
    expect(bodies[0]?.variables.pagination).toEqual({ offset: 10, limit: 10 });
    expect(bodies[0]?.query).toContain("filter: $filter");
  });

  test("requests and returns grouped measures", async () => {
    const { fetch, bodies } = mockTransport({
      saleGroups: {
        totalCount: 1,
        results: [
          { count: 3, key: { state: "OPEN" }, sum: { amount: "42" } },
        ],
      },
    });
    const { result } = renderHook(
      () =>
        useResourceGroupBy("Sale", {
          dimensions: [{ field: "STATE", key: "state" }],
          measures: [{ op: "sum", field: "amount" }],
        }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.buckets[0]?.sum?.amount).toBe("42");
    expect(compactGraphQL(bodies[0]?.query)).toContain("count sum { amount }");
  });
});
