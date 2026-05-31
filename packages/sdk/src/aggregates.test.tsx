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
  test("returns the count and measures bucket", async () => {
    const { fetch, bodies } = mockTransport({
      salesAggregate: { count: 6, sum: { total: "1125.00" } },
    });
    const { result } = renderHook(
      () => useAggregateQuery("Sale", { measureFields: ["total"] }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.aggregate?.count).toBe(6);
    expect(result.current.aggregate?.measures.sum?.total).toBe("1125.00");
    expect(bodies[0]?.query).toContain("salesAggregate(");
  });
});

describe("useResourceGroupBy", () => {
  test("returns buckets keyed by the group fields", async () => {
    const { fetch, bodies } = mockTransport({
      salesGroupBy: {
        totalCount: 2,
        results: [
          { key: { state: "OPEN" }, count: 3, sum: { total: "700.00" } },
          { key: { state: "CLOSED" }, count: 2, sum: { total: "350.00" } },
        ],
      },
    });
    const { result } = renderHook(
      () =>
        useResourceGroupBy("Sale", {
          groupBy: [{ field: "STATE" }],
          keyFields: ["state"],
          measureFields: ["total"],
        }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.totalCount).toBe(2);
    expect(result.current.buckets.map((b) => b.key?.state)).toEqual([
      "OPEN",
      "CLOSED",
    ]);
    expect(bodies[0]?.variables.groupBy).toEqual([{ field: "STATE" }]);
  });
});
