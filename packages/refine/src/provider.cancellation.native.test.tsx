// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useCustom, useInfiniteList, useList, useMany, useOne } from "@refinedev/core";
import { parse } from "graphql";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useAuthoredQuery } from "./dialect/authored-hooks";
import { useAngeeListBatch } from "./dialect/hooks";
import { createAngeeHasuraDataProvider } from "./provider";
import { createAuthoredLiveInvalidation, invalidateAuthoredQueries } from "./query-invalidation";
import { createRefineTestProviders } from "./testing";
import type { TypedDocumentNode } from "./typed-document";

const DOCUMENT = parse("query Notes { notes { id } }") as TypedDocumentNode<{ notes: { id: string }[] }, Record<string, never>>;
const { Provider, createClient, clearClients } = createRefineTestProviders();
afterEach(() => { cleanup(); clearClients(); });

function response(id = "current") {
  return new Response(JSON.stringify({ data: {
    notes: [{ id }], notes_by_pk: { id }, notes_aggregate: { aggregate: { count: 1 } },
  } }), { headers: { "Content-Type": "application/json" } });
}

function fixture() {
  const attempts: { signal: AbortSignal | null | undefined; resolve: (response: Response) => void }[] = [];
  const fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
    attempts.push({ signal: init?.signal, resolve });
    init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  }));
  const provider = createAngeeHasuraDataProvider({ url: "https://example.invalid/graphql", auth: (base) => base, fetch });
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider dataProvider={provider} queryClient={client}>{children}</Provider>;
  return { attempts, fetch, provider, client, wrapper };
}

const readers = [
  ["authored", () => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] })],
  ["custom document", () => useCustom({ url: "", method: "get", meta: { gqlQuery: DOCUMENT } }).query],
  ["custom generated", () => useCustom({ url: "", method: "get", meta: { operation: "notes", fields: ["id"] } }).query],
  ["list", () => useList({ resource: "notes" }).query],
  ["one", () => useOne({ resource: "notes", id: "one" }).query],
  ["many", () => useMany({ resource: "notes", ids: ["one"] }).query],
  ["infinite", () => useInfiniteList({ resource: "notes" }).query],
  ["grouped leaf", () => useAngeeListBatch({
    root: "notes", aggregateRoot: "notes_aggregate", filterType: "NoteBoolExp", orderType: "NoteOrderBy",
    resourceName: "notes", resourceIdentifier: "example.Note", dataProviderName: "default",
  }, [{ key: "one", where: undefined, orderBy: undefined, page: 1, pageSize: 10 }], { fields: ["id"] })],
] as const;

test.each(readers)("%s cancellation reaches the real Hasura fetch on last unmount", async (_name, read) => {
  const f = fixture();
  const hook = renderHook(() => { read(); }, { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    expect(f.attempts[0]?.signal).toBeInstanceOf(AbortSignal);
    hook.unmount();
    expect(f.attempts[0]?.signal?.aborted).toBe(true);
    await act(async () => { f.attempts[0]?.resolve(response("stale")); });
    expect(f.client.getQueryCache().getAll().every((query) => query.state.data === undefined)).toBe(true);
  } finally {
    f.attempts.forEach((attempt) => attempt.resolve(response()));
  }
});

test("initial model invalidation aborts actual HTTP before its replacement snapshot", async () => {
  const f = fixture();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    let refreshed!: Promise<void>;
    act(() => { refreshed = invalidateAuthoredQueries(f.client, ["example.Note"]); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    expect(f.attempts[0]?.signal?.aborted).toBe(true);
    await act(async () => { f.attempts[1]?.resolve(response()); await refreshed; });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "current" }]));
  } finally { f.attempts.forEach((attempt) => attempt.resolve(response())); }
});

test("retained refetch cancellation aborts fetch and preserves the successor", async () => {
  const f = fixture();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    await act(async () => { f.attempts[0]?.resolve(response("initial")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "initial" }]));
    act(() => { void hook.result.current.refetch(); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    act(() => { void hook.result.current.refetch(); });
    await waitFor(() => expect(f.attempts).toHaveLength(3));
    expect(f.attempts[1]?.signal?.aborted).toBe(true);
    await act(async () => { f.attempts[1]?.resolve(response("stale")); f.attempts[2]?.resolve(response()); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "current" }]));
  } finally { f.attempts.forEach((attempt) => attempt.resolve(response())); }
});

test("authored writes do not consume a Query cancellation signal", async () => {
  const f = fixture();
  const signal = vi.fn(() => { throw new Error("Mutation consumed Query signal"); });
  const result = f.provider.custom({ url: "", method: "post", meta: {
    gqlMutation: parse("mutation UpdateNote { update_notes { id } }"), get signal() { return signal(); },
  } });
  expect(f.attempts).toHaveLength(1);
  f.attempts[0]?.resolve(response());
  await result;
  expect(signal).not.toHaveBeenCalled();
});


test("live changes abort actual HTTP on arrival and keep the replacement read", async () => {
  const f = fixture();
  const live = createAuthoredLiveInvalidation(f.client);
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    act(() => { live.push({ model: "example.Note", id: "one" }); });
    expect(f.attempts[0]?.signal?.aborted).toBe(true);
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    await act(async () => { f.attempts[1]?.resolve(response()); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "current" }]));
  } finally {
    live.clear();
    f.attempts.forEach((attempt) => attempt.resolve(response()));
  }
});
