// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { fetchExchange } from "@urql/core";
import { describe, expect, test, vi } from "vitest";

import { useAuthoredMutation, useAuthoredQuery } from "./authored-hooks";
import { GraphQLClientProvider } from "./graphql-provider";

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
      config: { public: { url: "/graphql/", fetch, exchanges: [fetchExchange] } },
      schema: "public",
      children,
    });
}

describe("useAuthoredQuery", () => {
  test("runs a hand-written document and returns its data", async () => {
    const { fetch } = mockTransport({ noteRevisions: [{ id: "r1" }] });
    const { result } = renderHook(
      () => useAuthoredQuery<{ noteRevisions: Array<{ id: string }> }>(
        "query revs($id: Sqid!) { noteRevisions(id: $id) { id } }",
        { id: "1" },
      ),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.data?.noteRevisions).toEqual([{ id: "r1" }]);
  });

  test("does not run when disabled", () => {
    const { fetch } = mockTransport({});
    renderHook(
      () => useAuthoredQuery("query { __typename }", {}, { enabled: false }),
      { wrapper: wrapperWith(fetch) },
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("useAuthoredMutation", () => {
  test("runs the mutation and resolves to its data", async () => {
    const { fetch } = mockTransport({ archiveNote: { ok: true } });
    const { result } = renderHook(
      () => useAuthoredMutation<{ archiveNote: { ok: boolean } }>(
        "mutation arch($id: Sqid!) { archiveNote(id: $id) { ok } }",
      ),
      { wrapper: wrapperWith(fetch) },
    );
    const [archive] = result.current;
    const data = await archive({ id: "1" });
    expect(data?.archiveNote.ok).toBe(true);
  });

  test("throws when the mutation errors", async () => {
    const fetch = vi.fn(async () =>
      new Response(JSON.stringify({ errors: [{ message: "nope" }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof globalThis.fetch;
    const { result } = renderHook(
      () => useAuthoredMutation("mutation { fail { ok } }"),
      { wrapper: wrapperWith(fetch) },
    );
    const [run] = result.current;
    await expect(run({})).rejects.toThrow(/nope/);
  });
});
