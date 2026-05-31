// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { fetchExchange } from "urql";
import { describe, expect, test, vi } from "vitest";

import { createSchemaClients, GraphQLProvider } from "./graphql-provider";
import { useAggregateQuery, useResourceGroupBy } from "./aggregates";

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
  const clients = createSchemaClients({
    public: { url: "/graphql/", fetch, exchanges: [fetchExchange] },
  });
  return ({ children }: { children: ReactNode }) =>
    createElement(GraphQLProvider, { clients, schema: "public", children });
}

describe("useAggregateQuery", () => {
  test("returns the ungrouped count bucket", async () => {
    const { fetch, bodies } = mockTransport({ saleAggregate: { count: 6 } });
    const { result } = renderHook(() => useAggregateQuery("Sale"), {
      wrapper: wrapperWith(fetch),
    });
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.aggregate?.count).toBe(6);
    expect(bodies[0]?.query).toContain("saleAggregate");
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
});
