// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { fetchExchange } from "@urql/core";
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
  test("requests the offset page and returns rows, total, and page count", async () => {
    const { fetch, bodies } = mockTransport({
      sales: {
        totalCount: 12,
        results: [{ id: "1", title: "A" }, { id: "2", title: "B" }],
        pageInfo: { offset: 0, limit: 50 },
      },
    });
    const { result } = renderHook(
      () => useResourceList("Sale", { fields: ["title"], pageSize: 5 }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.rows).toEqual([
      { id: "1", title: "A" },
      { id: "2", title: "B" },
    ]);
    expect(result.current.total).toBe(12);
    expect(result.current.pageCount).toBe(3);
    expect(result.current.page).toBe(1);
    expect(bodies[0]?.query).toContain("sales(");
    expect(bodies[0]?.variables.pagination).toEqual({ offset: 0, limit: 5 });
  });

  test("setPage jumps to the page's offset", async () => {
    const { fetch, bodies } = mockTransport({
      sales: {
        totalCount: 12,
        results: [{ id: "6", title: "F" }],
        pageInfo: { offset: 5, limit: 5 },
      },
    });
    const { result } = renderHook(
      () => useResourceList("Sale", { fields: ["title"], pageSize: 5 }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    act(() => result.current.setPage(2));
    await waitFor(() => expect(result.current.page).toBe(2));
    await waitFor(() =>
      expect(bodies.at(-1)?.variables.pagination).toEqual({ offset: 5, limit: 5 }),
    );
  });

  test("tracks controlled page changes from the owning view state", async () => {
    const { fetch, bodies } = mockTransport({
      sales: {
        totalCount: 12,
        results: [{ id: "6", title: "F" }],
        pageInfo: { offset: 5, limit: 5 },
      },
    });
    const { result, rerender } = renderHook(
      ({ page }) =>
        useResourceList("Sale", {
          fields: ["title"],
          pageSize: 5,
          page,
        }),
      { initialProps: { page: 1 }, wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    rerender({ page: 2 });
    await waitFor(() => expect(result.current.page).toBe(2));
    await waitFor(() =>
      expect(bodies.at(-1)?.variables.pagination).toEqual({ offset: 5, limit: 5 }),
    );
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
  test("create runs the verb-first mutation and resolves to the created node", async () => {
    const { fetch, bodies } = mockTransport({ createSale: { id: "9", title: "New" } });
    const { result } = renderHook(
      () => useResourceMutation("Sale", "create", { fields: ["title"] }),
      { wrapper: wrapperWith(fetch) },
    );
    const [mutate] = result.current;
    const node = await mutate({ data: { title: "New" } });
    expect(node).toEqual({ id: "9", title: "New" });
    expect(bodies[0]?.query).toContain("createSale(data:");
  });

  test("delete passes confirm and resolves to the delete preview", async () => {
    const { fetch, bodies } = mockTransport({
      deleteSale: {
        totalDeletedCount: 1,
        hasBlockers: false,
        deleted: [{ label: "sales", count: 1 }],
        updated: [],
        blocked: [],
        root: {
          label: "sale",
          objectLabel: "Sale A",
          objectId: "1",
          children: [],
        },
      },
    });
    const { result } = renderHook(
      () => useResourceMutation("Sale", "delete"),
      { wrapper: wrapperWith(fetch) },
    );
    const [mutate] = result.current;
    const preview = await mutate({ id: "1", confirm: false });

    expect(preview).toEqual({
      totalDeletedCount: 1,
      hasBlockers: false,
      deleted: [{ label: "sales", count: 1 }],
      updated: [],
      blocked: [],
      root: {
        label: "sale",
        objectLabel: "Sale A",
        objectId: "1",
        children: [],
      },
    });
    expect(bodies[0]?.query).toContain("$confirm: Boolean");
    expect(bodies[0]?.query).toContain("confirm: $confirm");
    expect(bodies[0]?.variables).toEqual({ id: "1", confirm: false });
  });
});
