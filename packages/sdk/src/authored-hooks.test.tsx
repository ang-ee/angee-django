// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { fetchExchange, type TypedDocumentNode } from "@urql/core";
import { parse } from "graphql";
import { describe, expect, test, vi } from "vitest";

import {
  useAuthoredMutation,
  useAuthoredQuery,
  useAuthoredRows,
} from "./authored-hooks";
import { GraphQLClientProvider } from "./graphql-provider";
import {
  RelayInvalidationProvider,
  useInvalidateModels,
  useRegisterModelRefetch,
} from "./relay-invalidation";

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

function wrapperWith(
  fetch: typeof globalThis.fetch,
  options: { invalidation?: boolean } = {},
) {
  return ({ children }: { children: ReactNode }) =>
    createElement(GraphQLClientProvider, {
      config: { public: { url: "/graphql/", fetch, exchanges: [fetchExchange] } },
      schema: "public",
      children: options.invalidation
        ? createElement(RelayInvalidationProvider, {
          autoSubscribe: false,
          children,
        })
        : children,
    });
}

function typedDocument<TData, TVariables extends Record<string, unknown>>(
  source: string,
): TypedDocumentNode<TData, TVariables> {
  return parse(source) as TypedDocumentNode<TData, TVariables>;
}

describe("useAuthoredQuery", () => {
  test("runs a generated authored document and returns its data", async () => {
    const { fetch } = mockTransport({ noteRevisions: [{ id: "r1" }] });
    const document = typedDocument<
      { noteRevisions: Array<{ id: string }> },
      { id: string }
    >("query revs($id: Sqid!) { noteRevisions(id: $id) { id } }");
    const { result } = renderHook(
      () => useAuthoredQuery(document, { id: "1" }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.data?.noteRevisions).toEqual([{ id: "r1" }]);
  });

  test("does not run when disabled", () => {
    const { fetch } = mockTransport({});
    const document = typedDocument<{ __typename: string }, Record<string, never>>(
      "query { __typename }",
    );
    renderHook(
      () => useAuthoredQuery(document, {}, { enabled: false }),
      { wrapper: wrapperWith(fetch) },
    );
    expect(fetch).not.toHaveBeenCalled();
  });

  test("registers explicit models for invalidation", async () => {
    const { fetch, bodies } = mockTransport({ noteRevisions: [{ id: "r1" }] });
    const document = typedDocument<
      { noteRevisions: Array<{ id: string }> },
      { id: string }
    >("query revs($id: Sqid!) { noteRevisions(id: $id) { id } }");
    const { result } = renderHook(
      () => ({
        query: useAuthoredQuery(document, { id: "1" }, {
          models: ["notes.Note"],
        }),
        invalidate: useInvalidateModels(),
      }),
      { wrapper: wrapperWith(fetch, { invalidation: true }) },
    );
    await waitFor(() => expect(result.current.query.fetching).toBe(false));
    expect(noteRevisionQueries(bodies)).toHaveLength(1);

    act(() => result.current.invalidate(["notes.Note"]));
    await waitFor(() => expect(noteRevisionQueries(bodies)).toHaveLength(2));
  });

  test("does not register explicit models while disabled", () => {
    const { fetch, bodies } = mockTransport({});
    const document = typedDocument<{ __typename: string }, Record<string, never>>(
      "query { __typename }",
    );
    const { result } = renderHook(
      () => ({
        query: useAuthoredQuery(document, {}, {
          enabled: false,
          models: ["notes.Note"],
        }),
        invalidate: useInvalidateModels(),
      }),
      { wrapper: wrapperWith(fetch, { invalidation: true }) },
    );

    act(() => result.current.invalidate(["notes.Note"]));
    expect(fetch).not.toHaveBeenCalled();
    expect(bodies).toHaveLength(0);
  });
});

describe("useAuthoredRows", () => {
  test("projects an authored result into rows and preserves query state", async () => {
    const { fetch } = mockTransport({ notes: [{ id: "note-1", title: "Hello" }] });
    const document = typedDocument<
      { notes: Array<{ id: string; title: string }> },
      { limit: number }
    >("query notes($limit: Int!) { notes(limit: $limit) { id title } }");
    const { result } = renderHook(
      () => useAuthoredRows(document, {
        variables: { limit: 5 },
        selectRows: (data) => data?.notes ?? [],
      }),
      { wrapper: wrapperWith(fetch) },
    );
    await waitFor(() => expect(result.current.fetching).toBe(false));
    expect(result.current.rows).toEqual([{ id: "note-1", title: "Hello" }]);
    expect(result.current.data?.notes).toHaveLength(1);
  });
});

describe("useAuthoredMutation", () => {
  test("runs the mutation and resolves to its data", async () => {
    const { fetch } = mockTransport({ archiveNote: { ok: true } });
    const document = typedDocument<
      { archiveNote: { ok: boolean } },
      { id: string }
    >("mutation arch($id: Sqid!) { archiveNote(id: $id) { ok } }");
    const { result } = renderHook(
      () => useAuthoredMutation(document),
      { wrapper: wrapperWith(fetch) },
    );
    const [archive] = result.current;
    const data = await archive({ id: "1" });
    expect(data?.archiveNote.ok).toBe(true);
  });

  test("invalidates explicit models after a successful mutation", async () => {
    const { fetch } = mockTransport({ archiveNote: { ok: true } });
    const refetch = vi.fn();
    const document = typedDocument<
      { archiveNote: { ok: boolean } },
      { id: string }
    >("mutation arch($id: Sqid!) { archiveNote(id: $id) { ok } }");
    const { result } = renderHook(
      () => {
        useRegisterModelRefetch("notes.Note", refetch, true);
        return useAuthoredMutation(document, {
          invalidateModels: ["notes.Note"],
        });
      },
      { wrapper: wrapperWith(fetch, { invalidation: true }) },
    );
    await act(async () => {
      await result.current[0]({ id: "1" });
    });
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  test("respects a domain-level invalidation guard", async () => {
    const { fetch } = mockTransport({ revokeRole: false });
    const refetch = vi.fn();
    const document = typedDocument<
      { revokeRole: boolean },
      { principalId: string; role: string }
    >("mutation revoke($principalId: String!, $role: String!) { revokeRole(principalId: $principalId, role: $role) }");
    const { result } = renderHook(
      () => {
        useRegisterModelRefetch("rebac.RelationshipRegistry", refetch, true);
        return useAuthoredMutation(document, {
          invalidateModels: ["rebac.RelationshipRegistry"],
          shouldInvalidate: (data) => data?.revokeRole === true,
        });
      },
      { wrapper: wrapperWith(fetch, { invalidation: true }) },
    );
    await act(async () => {
      await result.current[0]({ principalId: "user-1", role: "iam/admin" });
    });
    expect(refetch).not.toHaveBeenCalled();
  });

  test("passes mutation variables to the invalidation guard", async () => {
    const { fetch } = mockTransport({ archiveNote: { ok: true } });
    const guard = vi.fn(() => true);
    const document = typedDocument<
      { archiveNote: { ok: boolean } },
      { id: string }
    >("mutation arch($id: Sqid!) { archiveNote(id: $id) { ok } }");
    const { result } = renderHook(
      () => useAuthoredMutation(document, {
        invalidateModels: ["notes.Note"],
        shouldInvalidate: guard,
      }),
      { wrapper: wrapperWith(fetch, { invalidation: true }) },
    );
    await act(async () => {
      await result.current[0]({ id: "1" });
    });
    expect(guard).toHaveBeenCalledWith(
      { archiveNote: { ok: true } },
      { id: "1" },
    );
  });


  test("throws when the mutation errors", async () => {
    const fetch = vi.fn(async () =>
      new Response(JSON.stringify({ errors: [{ message: "nope" }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof globalThis.fetch;
    const document = typedDocument<
      { fail: { ok: boolean } },
      Record<string, never>
    >("mutation { fail { ok } }");
    const { result } = renderHook(
      () => useAuthoredMutation(document),
      { wrapper: wrapperWith(fetch) },
    );
    const [run] = result.current;
    await expect(run({})).rejects.toThrow(/nope/);
  });

  test("does not invalidate explicit models when the mutation errors", async () => {
    const fetch = vi.fn(async () =>
      new Response(JSON.stringify({ errors: [{ message: "nope" }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof globalThis.fetch;
    const refetch = vi.fn();
    const document = typedDocument<
      { fail: { ok: boolean } },
      Record<string, never>
    >("mutation { fail { ok } }");
    const { result } = renderHook(
      () => {
        useRegisterModelRefetch("notes.Note", refetch, true);
        return useAuthoredMutation(document, {
          invalidateModels: ["notes.Note"],
        });
      },
      { wrapper: wrapperWith(fetch, { invalidation: true }) },
    );
    const [run] = result.current;
    await expect(run({})).rejects.toThrow(/nope/);
    expect(refetch).not.toHaveBeenCalled();
  });
});

const noteRevisionQueries = (
  bodies: Array<{ query: string; variables: Record<string, unknown> }>,
) => bodies.filter((body) => body.query.includes("noteRevisions"));
