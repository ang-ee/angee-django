// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useCustom, useInfiniteList, useInvalidate, useList, useMany, useOne, useUpdate } from "@refinedev/core";
import { parse } from "graphql";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useAuthoredQuery } from "./dialect/authored-hooks";
import { useAngeeListBatch } from "./dialect/hooks";
import { createAngeeChangeLiveProvider, createAngeeHasuraDataProvider } from "./provider";
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
  const attempts: { signal: AbortSignal | null | undefined; resolve: (response: Response) => void; reject: (error: Error) => void }[] = [];
  const fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
    attempts.push({ signal: init?.signal, resolve, reject });
    init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  }));
  const provider = createAngeeHasuraDataProvider({ url: "https://example.invalid/graphql", auth: (base) => base, fetch });
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider dataProvider={provider} queryClient={client}>{children}</Provider>;
  return { attempts, fetch, provider, client, wrapper };
}

function liveReadyFixture(client: ReturnType<typeof fixture>["client"]) {
  const sinks: ((result: { data: unknown }) => void)[] = [];
  const subscribe = vi.fn((_payload: unknown, sink: { next: (result: { data: unknown }) => void }) => {
    sinks.push(sink.next);
    return vi.fn();
  });
  const live = createAngeeChangeLiveProvider(
    { subscribe, on: vi.fn(() => () => undefined) } as never,
    [{ schemaName: "console", modelLabel: "example.Note", roots: { list: "notes", changes: "noteChanged" } }],
    { queryClient: client },
  );
  const subscribeNotes = () => live.subscribe({ channel: "notes", types: ["*"], callback: vi.fn(), params: { resource: "notes" } });
  function ready(index = 0) {
    const next = sinks[index];
    if (!next) throw new Error(`No upstream subscription at index ${index}`);
    next({ data: { noteChanged: { model: "example.Note", id: "", action: "ready" } } });
  }
  function change(index = 0) {
    const next = sinks[index];
    if (!next) throw new Error(`No upstream subscription at index ${index}`);
    next({ data: { noteChanged: { model: "example.Note", id: "one", action: "update" } } });
  }
  return { live, subscribe, subscribeNotes, ready, change };
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

test("ready recovers an HTTP read started after subscribe but before registration", async () => {
  const f = fixture();
  const { live, subscribe, subscribeNotes, ready } = liveReadyFixture(f.client);
  const subscription = subscribeNotes();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    expect(subscribe).toHaveBeenCalledOnce();
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    act(() => { ready(); });
    expect(f.attempts[0]?.signal?.aborted).toBe(false);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(1);
    await act(async () => { f.attempts[0]?.resolve(response("pre-registration")); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    expect(f.attempts[0]?.signal?.aborted).toBe(false);
    await act(async () => { f.attempts[1]?.resolve(response("registered")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "registered" }]));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(2);
  } finally {
    live.unsubscribe(subscription);
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    hook.unmount();
  }
});

test("ready lets an older data-less HTTP read settle before one recovery refetch", async () => {
  const f = fixture();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.attempts).toHaveLength(1));
  const { live, subscribeNotes, ready } = liveReadyFixture(f.client);
  const subscription = subscribeNotes();
  try {
    act(() => { ready(); });
    expect(f.attempts[0]?.signal?.aborted).toBe(false);
    expect(f.client.getQueryCache().getAll().some((query) => query.state.isInvalidated)).toBe(true);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(1);
    await act(async () => { f.attempts[0]?.resolve(response("older snapshot")); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    expect(f.attempts[0]?.signal?.aborted).toBe(false);
    await act(async () => { f.attempts[1]?.resolve(response("recovered")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "recovered" }]));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(2);
  } finally {
    live.unsubscribe(subscription);
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    hook.unmount();
  }
});

test("a second ready on the same sink refetches a retained HTTP read once", async () => {
  const f = fixture();
  const { live, subscribe, subscribeNotes, ready } = liveReadyFixture(f.client);
  const subscription = subscribeNotes();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    act(() => { ready(); });
    await act(async () => { f.attempts[0]?.resolve(response("retained")); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    await act(async () => { f.attempts[1]?.resolve(response("registered")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "registered" }]));
    act(() => { ready(); });
    expect(f.client.getQueryCache().getAll().some((query) => query.state.isInvalidated)).toBe(true);
    await waitFor(() => expect(f.attempts).toHaveLength(3));
    expect(subscribe).toHaveBeenCalledOnce();
    expect(f.attempts[0]?.signal?.aborted).toBe(false);
    await act(async () => { f.attempts[2]?.resolve(response("after reconnect")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "after reconnect" }]));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(3);
  } finally {
    live.setEnabled(false);
    live.unsubscribe(subscription);
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    hook.unmount();
  }
});

test("ready refetches a read that settled between subscribe and registration", async () => {
  const f = fixture();
  const { live, subscribeNotes, ready } = liveReadyFixture(f.client);
  const subscription = subscribeNotes();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    await act(async () => { f.attempts[0]?.resolve(response("before ready")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "before ready" }]));
    act(() => { ready(); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    await act(async () => { f.attempts[1]?.resolve(response("after ready")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "after ready" }]));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(2);
  } finally {
    live.unsubscribe(subscription);
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    hook.unmount();
  }
});

test("the first native list consumer recovers its pre-registration read", async () => {
  const f = fixture();
  const { live, subscribe, ready } = liveReadyFixture(f.client);
  const wrapper = ({ children }: { children: ReactNode }) => <Provider
    dataProvider={f.provider} queryClient={f.client} liveProvider={live}
    resources={[{ name: "notes" }]} options={{ liveMode: "manual" }}>{children}</Provider>;
  const hook = renderHook(() => useList({ resource: "notes" }).query, { wrapper });
  try {
    await waitFor(() => expect(subscribe).toHaveBeenCalledOnce());
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    expect(subscribe.mock.invocationCallOrder[0]).toBeLessThan(f.fetch.mock.invocationCallOrder[0]!);
    act(() => { ready(); });
    expect(f.attempts[0]?.signal?.aborted).toBe(false);
    await act(async () => { f.attempts[0]?.resolve(response("pre-registration")); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    await act(async () => { f.attempts[1]?.resolve(response("registered")); });
    await waitFor(() => expect(hook.result.current.data?.data).toEqual([{ id: "registered" }]));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(2);
  } finally {
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    hook.unmount();
  }
});

test("ready retries once after an older data-less read fails", async () => {
  const f = fixture();
  const { live, subscribeNotes, ready } = liveReadyFixture(f.client);
  const subscription = subscribeNotes();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    act(() => { ready(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(1);
    await act(async () => { f.attempts[0]?.reject(new Error("controlled read failure")); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    await act(async () => { f.attempts[1]?.resolve(response("recovered")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "recovered" }]));
    expect(f.attempts).toHaveLength(2);
  } finally {
    live.unsubscribe(subscription);
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    hook.unmount();
  }
});

test("ready does not restart a data-less read after its last observer unmounts", async () => {
  const f = fixture();
  const { live, subscribeNotes, ready } = liveReadyFixture(f.client);
  const subscription = subscribeNotes();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    act(() => { ready(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(1);
    hook.unmount();
    expect(f.attempts[0]?.signal?.aborted).toBe(true);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(1);
  } finally {
    live.unsubscribe(subscription);
    f.attempts.forEach((attempt) => attempt.resolve(response()));
  }
});

test("ready and a live change share one replacement after the change cancels a data-less read", async () => {
  const f = fixture();
  const { live, subscribeNotes, ready, change } = liveReadyFixture(f.client);
  const subscription = subscribeNotes();
  const hook = renderHook(() => useAuthoredQuery(DOCUMENT, {}, { models: ["example.Note"] }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    act(() => { ready(); change(); });
    expect(f.attempts[0]?.signal?.aborted).toBe(true);
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    await act(async () => { f.attempts[1]?.resolve(response("changed")); });
    await waitFor(() => expect(hook.result.current.data?.notes).toEqual([{ id: "changed" }]));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(f.attempts).toHaveLength(2);
  } finally {
    live.unsubscribe(subscription);
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    hook.unmount();
  }
});

const invalidateReaders = [
  ["all", () => useList({ resource: "notes" }).query],
  ["list", () => useList({ resource: "notes" }).query],
  ["many", () => useMany({ resource: "notes", ids: ["one"] }).query],
  ["resourceAll", () => useList({ resource: "notes" }).query],
  ["detail", () => useOne({ resource: "notes", id: "one" }).query],
] as const;

test.each(invalidateReaders)("native %s invalidation preserves declared cancellation options", async (selector, read) => {
  for (const cancelRefetch of [false, undefined, true] as const) {
    const f = fixture();
    const hook = renderHook(() => ({ query: read(), invalidate: useInvalidate() }), { wrapper: f.wrapper });
    let refreshed: Promise<void> | undefined;
    try {
      await waitFor(() => expect(f.attempts).toHaveLength(1));
      await act(async () => { f.attempts[0]?.resolve(response("initial")); });
      await waitFor(() => expect(hook.result.current.query.isSuccess).toBe(true));
      act(() => { void hook.result.current.query.refetch(); });
      await waitFor(() => expect(f.attempts).toHaveLength(2));
      await act(async () => {
        refreshed = hook.result.current.invalidate({
          resource: "notes", id: "one", invalidates: [selector],
          ...(cancelRefetch === undefined ? {} : { invalidationOptions: { cancelRefetch } }),
        });
      });
      expect(f.attempts[1]?.signal?.aborted).toBe(cancelRefetch === true);
      expect(f.attempts).toHaveLength(cancelRefetch === true ? 3 : 2);
      await act(async () => {
        f.attempts.at(-1)?.resolve(response("current"));
        await refreshed;
      });
      expect(hook.result.current.query.isSuccess).toBe(true);
    } finally {
      await act(async () => {
        f.attempts.forEach((attempt) => attempt.resolve(response()));
        await refreshed;
      });
      hook.unmount();
    }
  }
});

test("native invalidation retains its selectors and refetch filters", async () => {
  const f = fixture();
  const hook = renderHook(() => ({ query: useList({ resource: "notes", queryOptions: { meta: { invalidationTarget: true } } }).query, invalidate: useInvalidate() }), { wrapper: f.wrapper });
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    await act(async () => { f.attempts[0]?.resolve(response()); });
    await waitFor(() => expect(hook.result.current.query.isSuccess).toBe(true));
    const target = f.client.getQueryCache().getAll().find((query) => query.meta?.invalidationTarget === true)!;
    const otherKey = [...target.queryKey, "another-projection"];
    f.client.setQueryData(otherKey, { id: "other" });
    await act(async () => {
      await hook.result.current.invalidate({ resource: "notes", invalidates: false });
      await hook.result.current.invalidate({
        resource: "notes", invalidates: ["resourceAll"],
        invalidationFilters: { predicate: () => false },
      });
    });
    expect(target.state.isInvalidated).toBe(false);
    await act(async () => {
      await hook.result.current.invalidate({
        resource: "notes", id: "different-record", invalidates: ["detail"],
        invalidationFilters: { queryKey: target.queryKey, exact: true, refetchType: "none", predicate: (query) => query === target },
        invalidationOptions: { cancelRefetch: false, throwOnError: true },
      });
    });
    expect(target.state.isInvalidated).toBe(true);
    expect(f.client.getQueryState(otherKey)?.isInvalidated).toBe(false);
    expect(f.attempts).toHaveLength(1);
  } finally { f.attempts.forEach((attempt) => attempt.resolve(response())); }
});

test.each([false, true])("native invalidation forwards throwOnError=%s to Query", async (throwOnError) => {
  const f = fixture();
  const hook = renderHook(() => ({ query: useList({ resource: "notes" }).query, invalidate: useInvalidate() }), { wrapper: f.wrapper });
  let refreshed: Promise<void> | undefined;
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    await act(async () => { f.attempts[0]?.resolve(response()); });
    await waitFor(() => expect(hook.result.current.query.isSuccess).toBe(true));
    act(() => {
      refreshed = hook.result.current.invalidate({
        resource: "notes", invalidates: ["list"], invalidationOptions: { cancelRefetch: false, throwOnError },
      });
    });
    // Attach the rejection expectation before releasing the native fetch.
    const settled = throwOnError
      ? expect(refreshed).rejects.toThrow("Request failed.")
      : expect(refreshed).resolves.toBeUndefined();
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    await act(async () => { f.attempts[1]?.reject(new Error("controlled native provider failure")); await settled; });
  } finally {
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    await refreshed?.catch(() => undefined);
  }
});

test.each([false, undefined])("native invalidation joins an initial request with cancelRefetch=%s", async (cancelRefetch) => {
  const f = fixture();
  const hook = renderHook(() => ({ query: useList({ resource: "notes" }).query, invalidate: useInvalidate() }), { wrapper: f.wrapper });
  let refreshed: Promise<void> | undefined;
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    await act(async () => {
      refreshed = hook.result.current.invalidate({
        resource: "notes", invalidates: ["list"],
        ...(cancelRefetch === undefined ? {} : { invalidationOptions: { cancelRefetch } }),
      });
    });
    expect(f.attempts).toHaveLength(1);
    expect(f.attempts[0]?.signal?.aborted).toBe(false);
    await act(async () => { f.attempts[0]?.resolve(response()); await refreshed; });
    await waitFor(() => expect(hook.result.current.query.isSuccess).toBe(true));
  } finally {
    await act(async () => {
      f.attempts.forEach((attempt) => attempt.resolve(response()));
      await refreshed;
    });
  }
});


test("delegated pessimistic completion preserves unrelated reads and cannot roll them back on refusal", async () => {
  const f = fixture();
  let rejectWrite!: (error: Error) => void;
  const write = new Promise<never>((_resolve, reject) => { rejectWrite = reject; });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider
    dataProvider={{ ...f.provider, update: () => write }} queryClient={f.client}>{children}</Provider>;
  const hook = renderHook(() => ({
    query: useList({ resource: "notes" }).query,
    update: useUpdate({ mutationMode: "pessimistic", invalidates: [], errorNotification: false }),
  }), { wrapper });
  let completion: Promise<unknown> | undefined;
  try {
    await waitFor(() => expect(f.attempts).toHaveLength(1));
    await act(async () => { f.attempts[0]?.resolve(response("initial")); });
    await waitFor(() => expect(hook.result.current.query.isSuccess).toBe(true));
    act(() => { void hook.result.current.query.refetch(); });
    await waitFor(() => expect(f.attempts).toHaveLength(2));
    act(() => {
      completion = hook.result.current.update.mutateAsync({ resource: "notes", id: "another", values: { title: "edit" } });
    });
    const failed = expect(completion).rejects.toThrow("permission denied");
    await act(async () => { await Promise.resolve(); });
    expect(f.attempts[1]?.signal?.aborted).toBe(false);
    await act(async () => { f.attempts[1]?.resolve(response("fresh")); });
    await waitFor(() => expect(hook.result.current.query.data?.data).toEqual([{ id: "fresh" }]));
    await act(async () => { rejectWrite(new Error("permission denied")); await failed; });
    expect(hook.result.current.query.data?.data).toEqual([{ id: "fresh" }]);
  } finally {
    rejectWrite(new Error("fixture cleanup"));
    f.attempts.forEach((attempt) => attempt.resolve(response()));
    await completion?.catch(() => undefined);
  }
});
