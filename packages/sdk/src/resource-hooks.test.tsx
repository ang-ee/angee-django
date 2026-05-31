// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { fetchExchange } from "urql";
import { describe, expect, test, vi } from "vitest";

import { createSchemaClients, GraphQLProvider } from "./graphql-provider";
import {
  useResourceList,
  useResourceMutation,
  useResourceRecord,
} from "./resource-hooks";

/** A mock transport that answers any GraphQL POST with `payload`, recording bodies. */
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

describe("useResourceList", () => {
  test("requests the model's connection and returns flattened rows", async () => {
    const { fetch, bodies } = mockTransport({
      sales: {
        totalCount: 2,
        edges: [{ node: { id: "1", title: "A" } }, { node: { id: "2", title: "B" } }],
        pageInfo: { endCursor: "c2", hasNextPage: false },
      },
    });
    const { result } = renderHook(
      () => useResourceList("Sale", { fields: ["title"] }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.rows).toEqual([
      { id: "1", title: "A" },
      { id: "2", title: "B" },
    ]);
    expect(result.current.total).toBe(2);
    expect(bodies[0]?.query).toContain("sales(");
  });

  test("does not fetch when disabled", () => {
    const { fetch } = mockTransport({});
    const { result } = renderHook(
      () => useResourceList("Sale", { fields: ["title"], enabled: false }),
      { wrapper: wrapperWith(fetch) },
    );
    expect(result.current.rows).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("useResourceRecord", () => {
  test("requests the detail document by id and returns the node", async () => {
    const { fetch, bodies } = mockTransport({ sale: { id: "1", title: "A" } });
    const { result } = renderHook(
      () => useResourceRecord("Sale", "1", { fields: ["title"] }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.record).toEqual({ id: "1", title: "A" });
    expect(bodies[0]?.variables).toEqual({ id: "1" });
  });

  test("does not fetch without an id", () => {
    const { fetch } = mockTransport({});
    const { result } = renderHook(
      () => useResourceRecord("Sale", null, { fields: ["title"] }),
      { wrapper: wrapperWith(fetch) },
    );
    expect(result.current.record).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("useResourceMutation", () => {
  test("create runs the mutation and resolves to the created node", async () => {
    const { fetch, bodies } = mockTransport({ saleCreate: { id: "9", title: "New" } });
    const { result } = renderHook(
      () => useResourceMutation("Sale", "create", { fields: ["title"] }),
      { wrapper: wrapperWith(fetch) },
    );
    const [mutate] = result.current;
    const node = await mutate({ input: { title: "New" } });
    expect(node).toEqual({ id: "9", title: "New" });
    expect(bodies[0]?.query).toContain("saleCreate(input:");
  });
});
