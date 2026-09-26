// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { LiveProvider } from "@refinedev/core";
import { focusManager, keepPreviousData, onlineManager } from "@tanstack/react-query";
import { parse } from "graphql";
import { StrictMode, type ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { authoredQueryKey, authoredQueryOptions, useAuthoredMutation, useAuthoredQuery, useAuthoredQueryBatch } from "./authored-hooks";
import type { TypedDocumentNode } from "../typed-document";
import { invalidateAuthoredQueries } from "../query-invalidation";
import { createRefineTestProviders } from "../testing";
import { keysetFeedRows, useAuthoredKeysetFeed, type KeysetFeedWindow } from "./keyset-feed";

type Data = { notes: { id: string }[] };
type Variables = { id: string };
const DOCUMENT = parse("query Notes($id: ID!) { notes(id: $id) { id } }") as TypedDocumentNode<Data, Variables>;
const OTHER = parse("query ArchivedNotes($id: ID!) { notes(id: $id, archived: true) { id } }") as TypedDocumentNode<Data, Variables>;
const MUTATION = parse("mutation UpdateNotes($id: ID!) { notes: update_notes(id: $id) { id } }") as TypedDocumentNode<Data, Variables>;
const { Provider, dataProvider, createClient, clearClients } = createRefineTestProviders({
  apiUrl: "test://query", providerNames: ["alternate"],
});
afterEach(() => {
  cleanup(); clearClients();
  focusManager.setFocused(undefined); onlineManager.setOnline(true);
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

function fixture(custom = vi.fn(async () => ({ data: { notes: [{ id: "one" }] } })), liveProvider?: LiveProvider) {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, placeholderData: keepPreviousData } } });
  const provider = { custom };
  const onError = vi.fn(async () => ({}));
  const notify = vi.fn();
  function Providers({ children }: { children: ReactNode }) {
    return <Provider dataProvider={provider} queryClient={client}
      authProvider={{ login: async () => ({ success: true }), logout: async () => ({ success: true }), check: async () => ({ authenticated: true }), onError }}
      notificationProvider={{ open: notify, close: vi.fn() }} liveProvider={liveProvider}
    >{children}</Provider>;
  }
  return { client, custom, onError, notify, wrapper: Providers };
}

const feedWindow = {
  document: DOCUMENT,
  variables: (before: string | null) => ({ id: before ?? "head" }),
  select: ({ notes }: Data): KeysetFeedWindow<Data["notes"][number]> => ({
    rows: notes, count: 2, older_cursor: notes.at(-1)?.id ?? null,
    has_older: notes.length > 0, has_more_in_window: false, has_older_than_through: false,
  }),
};

test("keyset feeds forward native policy and avoid implicit focus, reconnect and mount reads", async () => {
  const f = fixture();
  f.client.setDefaultOptions({ queries: {
    staleTime: 0, retry: 3, refetchOnMount: "always", refetchOnWindowFocus: "always", refetchOnReconnect: "always",
  } });
  const queryOptions = {
    staleTime: "static" as const, gcTime: 0, retry: false, retryOnMount: false,
    refetchOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
  };
  const useFeed = () => useAuthoredKeysetFeed({
    actor: "reader", models: [], pageSize: 1, window: feedWindow, queryOptions,
  });
  const first = renderHook(useFeed, { wrapper: f.wrapper });
  await waitFor(() => expect(first.result.current.query.data?.pages).toHaveLength(1));
  const query = f.client.getQueryCache().find({ queryKey: ["angee", "authored", "keyset-feed"], exact: false });
  expect(query?.options).toMatchObject(queryOptions);
  const second = renderHook(useFeed, { wrapper: f.wrapper });
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    onlineManager.setOnline(false);
    onlineManager.setOnline(true);
    await f.client.invalidateQueries();
  });
  expect(f.custom).toHaveBeenCalledTimes(1);
  first.unmount();
  second.unmount();
  await waitFor(() => expect(f.client.getQueryCache().find({ queryKey: ["angee", "authored", "keyset-feed"], exact: false })).toBeUndefined());
});

test.each([
  { enabled: false, staleTime: "static" as const },
  { enabled: true, staleTime: "static" as const },
  { enabled: true, staleTime: Infinity },
])("keyset restart discards loaded history with enabled=$enabled and staleTime=$staleTime", async ({ enabled, staleTime }) => {
  const custom = vi.fn(async () => ({ data: { notes: [{ id: "fresh" }] } }));
  custom.mockResolvedValueOnce({ data: { notes: [{ id: "one" }] } });
  custom.mockResolvedValueOnce({ data: { notes: [{ id: "two" }] } });
  custom.mockResolvedValueOnce({ data: { notes: [] } });
  const f = fixture(custom);
  const { result } = renderHook(() => useAuthoredKeysetFeed({
    actor: "reader", enabled, models: [], pageSize: 1, window: feedWindow,
    queryOptions: { staleTime, retry: false },
  }), { wrapper: f.wrapper });
  if (!enabled) {
    expect(custom).not.toHaveBeenCalled();
    expect(result.current.query.data).toBeUndefined();
    await act(async () => { await result.current.restart(); });
  }
  await waitFor(() => expect(result.current.query.data?.pages).toHaveLength(1));
  await act(async () => { await result.current.query.fetchNextPage(); });
  await waitFor(() => expect(result.current.query.data?.pages).toHaveLength(2));
  await act(async () => { await result.current.query.fetchNextPage(); });
  await waitFor(() => expect(result.current.query.data?.pages).toHaveLength(3));
  expect(keysetFeedRows(result.current.query.data, (left, right) => left.id.localeCompare(right.id))).toEqual([{ id: "one" }, { id: "two" }]);
  expect(result.current.query.hasNextPage).toBe(false);
  await act(async () => { await result.current.restart(); });
  await waitFor(() => expect(result.current.query.data?.pages).toHaveLength(1));
  expect(result.current.query.data?.pages[0]?.rows).toEqual([{ id: "fresh" }]);
  expect(custom).toHaveBeenCalledTimes(4);
});

test("keyset restart waits for an actor", async () => {
  const f = fixture();
  const { result } = renderHook(() => useAuthoredKeysetFeed({
    actor: undefined, models: [], pageSize: 1, window: feedWindow,
  }), { wrapper: f.wrapper });
  await act(async () => { await result.current.restart(); });
  expect(f.custom).not.toHaveBeenCalled();
});

test("keyset restart keeps identity for equivalent scopes and follows a changed scope", async () => {
  const f = fixture();
  const resetQueries = vi.spyOn(f.client, "resetQueries");
  const { result, rerender } = renderHook(({ scope }) => useAuthoredKeysetFeed({
    actor: "reader", enabled: false, models: [], pageSize: 1,
    window: { ...feedWindow, variables: (before) => ({ id: before ?? scope }) },
  }), { wrapper: f.wrapper, initialProps: { scope: "first" } });
  const restart = result.current.restart;
  expect(result.current.query).not.toHaveProperty("restart");
  rerender({ scope: "first" });
  expect(result.current.restart).toBe(restart);
  rerender({ scope: "second" });
  expect(result.current.restart).not.toBe(restart);
  await act(async () => { await result.current.restart(); });
  expect(f.custom).toHaveBeenCalledTimes(1);
  expect(resetQueries).toHaveBeenCalledWith({
    queryKey: ["angee", "authored", "keyset-feed", "reader", authoredQueryKey(DOCUMENT, { id: "second" })],
    exact: true,
  }, { throwOnError: true });
});

test("data-only keyset consumers ignore background fetch-state changes", async () => {
  const refreshed = deferred<{ data: Data }>();
  const f = fixture();
  const rendered = vi.fn();
  const { result } = renderHook(() => {
    const { query } = useAuthoredKeysetFeed({
      actor: "reader", models: [], pageSize: 1, window: feedWindow,
    });
    rendered();
    return query.data;
  }, { wrapper: f.wrapper });
  await waitFor(() => expect(result.current?.pages).toHaveLength(1));
  const renders = rendered.mock.calls.length;
  const data = result.current;
  f.custom.mockImplementationOnce(() => refreshed.promise);
  let pending!: Promise<void>;
  await act(async () => {
    pending = f.client.refetchQueries();
    // Flush Query's scheduled observer notification while the response is pending.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  expect(f.client.isFetching()).toBe(1);
  expect(rendered).toHaveBeenCalledTimes(renders);
  await act(async () => {
    refreshed.resolve({ data: { notes: [{ id: "one" }] } });
    await pending;
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  expect(f.client.isFetching()).toBe(0);
  expect(result.current).toBe(data);
  expect(rendered).toHaveBeenCalledTimes(renders);
});

test("authored mutation reset clears native error state without another request", async () => {
  const error = new Error("Update declined.");
  const f = fixture(vi.fn().mockRejectedValue(error));
  const { result } = renderHook(() => useAuthoredMutation(MUTATION), { wrapper: f.wrapper });
  await act(async () => { await expect(result.current[0]({ id: "a" })).rejects.toBe(error); });
  await waitFor(() => expect(result.current[1].error).toBe(error));
  act(() => result.current[1].reset());
  await waitFor(() => expect(result.current[1].error).toBeNull());
  expect(result.current[1].fetching).toBe(false);
  expect(f.custom).toHaveBeenCalledTimes(1);
});

test("authored mutations stay pending until active query invalidation settles", async () => {
  const refreshed = deferred<{ data: Data }>();
  const custom = vi.fn(async () => ({ data: { notes: [{ id: "one" }] } }));
  custom.mockResolvedValueOnce({ data: { notes: [{ id: "one" }] } });
  custom.mockResolvedValueOnce({ data: { notes: [{ id: "updated" }] } });
  custom.mockImplementationOnce(() => refreshed.promise);
  const f = fixture(custom);
  const { result } = renderHook(() => ({
    read: useAuthoredQuery(DOCUMENT, { id: "a" }, { models: ["notes.Note"] }),
    mutation: useAuthoredMutation(MUTATION, { invalidateModels: ["notes.Note"] }),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.read.data).toBeDefined());
  const completed = vi.fn();
  let pending!: Promise<void>;
  act(() => { pending = result.current.mutation[0]({ id: "a" }).then(completed); });
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(3));
  expect(result.current.mutation[1].fetching).toBe(true);
  expect(completed).not.toHaveBeenCalled();
  await act(async () => {
    refreshed.resolve({ data: { notes: [{ id: "updated" }] } });
    await pending;
  });
  await waitFor(() => expect(result.current.mutation[1].fetching).toBe(false));
  expect(completed).toHaveBeenCalledWith({ notes: [{ id: "updated" }] });
  expect(result.current.read.data).toEqual({ notes: [{ id: "updated" }] });
});

test("authored completion keeps its starting policy and the next operation gets current options", async () => {
  const response = deferred<{ data: Data }>();
  const custom = vi.fn(async () => ({ data: { notes: [{ id: "updated" }] } }));
  custom.mockImplementationOnce(() => response.promise);
  const f = fixture(custom);
  const first = vi.fn(() => false);
  const second = vi.fn(() => false);
  const { result, rerender } = renderHook(({ shouldInvalidate }) => useAuthoredMutation(MUTATION, {
    invalidateModels: ["notes.Note"], shouldInvalidate,
  }), { wrapper: f.wrapper, initialProps: { shouldInvalidate: first } });
  const mutate = result.current[0];
  let pending!: ReturnType<typeof mutate>;
  act(() => { pending = mutate({ id: "first" }); });
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(1));
  rerender({ shouldInvalidate: second });
  expect(result.current[0]).toBe(mutate);
  await act(async () => { response.resolve({ data: { notes: [{ id: "updated" }] } }); await pending; });
  expect(first).toHaveBeenCalledWith({ notes: [{ id: "updated" }] }, { id: "first" });
  expect(second).not.toHaveBeenCalled();
  await act(async () => { await mutate({ id: "second" }); });
  expect(second).toHaveBeenCalledWith({ notes: [{ id: "updated" }] }, { id: "second" });
});

test("authored envelope failures enter native error state before invalidation", async () => {
  const f = fixture();
  const invalidate = vi.spyOn(f.client, "invalidateQueries");
  const { result } = renderHook(() => useAuthoredMutation(MUTATION, {
    invalidateModels: ["notes.Note"],
    errorFrom: () => ({ error_code: "DENIED", error: "Permission denied" }),
  }), { wrapper: f.wrapper });
  await act(async () => { await expect(result.current[0]({ id: "a" })).rejects.toThrow("Permission denied"); });
  await waitFor(() => expect(result.current[1].error?.message).toBe("Permission denied"));
  expect(result.current[1].fetching).toBe(false);
  expect(invalidate).not.toHaveBeenCalled();
  expect(f.onError).toHaveBeenCalledTimes(1);
  expect(f.notify).toHaveBeenCalledTimes(1);
});

test("singleton and batch share one request, native refetch and every model interest", async () => {
  const pending = deferred<{ data: Data }>();
  const f = fixture(vi.fn(() => pending.promise));
  const { result } = renderHook(() => ({
    single: useAuthoredQuery(DOCUMENT, { id: "a" }, { models: ["notes.Note"] }),
    batch: useAuthoredQueryBatch([{ key: "selected", document: parse("query Notes($id: ID!) { notes(id: $id) { id } }") as typeof DOCUMENT, variables: { id: "a" }, models: ["iam.User"] }]),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  await act(async () => { pending.resolve({ data: { notes: [{ id: "a" }] } }); });
  await waitFor(() => expect(result.current.single.data?.notes[0]?.id).toBe("a"));
  expect(result.current.batch.get("selected")?.data).toBe(result.current.single.data);
  const query = f.client.getQueryCache().find({ queryKey: authoredQueryKey(DOCUMENT, { id: "a" }), exact: true });
  expect(query?.meta?.angeeModels).toEqual(["iam.User", "notes.Note"]);
  f.custom.mockResolvedValue({ data: { notes: [{ id: "updated" }] } });
  let refetched;
  await act(async () => { refetched = await result.current.single.refetch(); });
  expect(refetched).toMatchObject({ data: { notes: [{ id: "updated" }] } });
  await act(async () => { await invalidateAuthoredQueries(f.client, ["iam.User"]); });
  await act(async () => { await invalidateAuthoredQueries(f.client, ["notes.Note"]); });
  expect(f.custom).toHaveBeenCalledTimes(4);
});

test("document, variables and provider changes isolate cache data despite global previous-data defaults", async () => {
  const f = fixture();
  const { result, rerender } = renderHook(({ document, id, provider, enabled }) =>
    useAuthoredQueryBatch([{ key: "same-label", document, variables: { id } }], { dataProviderName: provider, enabled }),
    { wrapper: f.wrapper, initialProps: { document: DOCUMENT, id: "a", provider: "default", enabled: true } });
  await waitFor(() => expect(result.current.get("same-label")?.data).toBeDefined());
  for (const change of [
    { document: OTHER, id: "a", provider: "default", enabled: false },
    { document: DOCUMENT, id: "b", provider: "default", enabled: false },
    { document: DOCUMENT, id: "a", provider: "alternate", enabled: false },
  ]) {
    rerender(change);
    expect(result.current.get("same-label")?.data).toBeUndefined();
    expect(result.current.get("same-label")?.isFetching).toBe(false);
  }
  expect(f.custom).toHaveBeenCalledTimes(1);
  rerender({ document: OTHER, id: "a", provider: "alternate", enabled: true });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
});

test("one shared failed query invokes native Refine auth and notifications once per failure", async () => {
  const error = Object.assign(new Error("denied"), { statusCode: 403 });
  const f = fixture(vi.fn().mockRejectedValue(error));
  const { result } = renderHook(() => ({
    single: useAuthoredQuery(DOCUMENT, { id: "a" }),
    batch: useAuthoredQueryBatch([{ key: "same", document: DOCUMENT, variables: { id: "a" } }]),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.single.isError).toBe(true));
  await waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
  expect(f.notify).toHaveBeenCalledTimes(1);
  expect(f.custom).toHaveBeenCalledTimes(1);
  await act(async () => { await result.current.single.refetch(); });
  await waitFor(() => expect(f.onError).toHaveBeenCalledTimes(2));
  expect(f.notify).toHaveBeenCalledTimes(2);
});

test("Query cancellation discards a late provider response even when transport ignores the signal", async () => {
  const pending = deferred<{ data: Data }>();
  const f = fixture(vi.fn(() => pending.promise));
  const { result } = renderHook(() => useAuthoredQuery(DOCUMENT, { id: "a" }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  await act(async () => { await f.client.cancelQueries({ queryKey: authoredQueryKey(DOCUMENT, { id: "a" }) }); });
  await act(async () => { pending.resolve({ data: { notes: [{ id: "late" }] } }); });
  expect(result.current.data).toBeUndefined();
});

test("StrictMode and pane remounts share the in-flight read when its provider cannot cancel transport", async () => {
  const pending = deferred<{ data: Data }>();
  const f = fixture(vi.fn(() => pending.promise));
  const wrapper = ({ children }: { children: ReactNode }) => (
    <StrictMode><f.wrapper>{children}</f.wrapper></StrictMode>
  );
  const first = renderHook(() => useAuthoredQuery(DOCUMENT, { id: "a" }), { wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  first.unmount();
  const { result } = renderHook(() => useAuthoredQuery(DOCUMENT, { id: "a" }), { wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  await act(async () => { pending.resolve({ data: { notes: [{ id: "a" }] } }); });
  await waitFor(() => expect(result.current.data?.notes[0]?.id).toBe("a"));
  expect(f.custom).toHaveBeenCalledTimes(1);
});

test("providers that consume the signal still receive native unmount cancellation", async () => {
  const pending = deferred<{ data: Data }>();
  let signal: AbortSignal | undefined;
  const custom = vi.fn(({ meta }: { meta?: { signal?: AbortSignal } } = {}) => {
    signal = meta?.signal;
    return pending.promise;
  });
  const f = fixture(custom);
  const first = renderHook(() => useAuthoredQuery(DOCUMENT, { id: "a" }), { wrapper: f.wrapper });
  await waitFor(() => expect(signal).toBeDefined());
  expect(signal?.aborted).toBe(false);
  first.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => { pending.resolve({ data: { notes: [{ id: "late" }] } }); });
  expect(f.client.getQueryData(authoredQueryKey(DOCUMENT, { id: "a" }))).toBeUndefined();
});

test("real Refine live subscriptions register canonical interests and clean up", async () => {
  const subscribe = vi.fn<LiveProvider["subscribe"]>(() => "subscription");
  const unsubscribe = vi.fn();
  const f = fixture(undefined, { subscribe, unsubscribe });
  const { unmount } = renderHook(() => useAuthoredQuery(DOCUMENT, { id: "a" }, { models: ["notes.Note"] }), { wrapper: f.wrapper });
  await waitFor(() => expect(subscribe).toHaveBeenCalled());
  expect(subscribe.mock.calls[0]?.[0]).toMatchObject({ params: { models: ["notes.Note"] }, channel: "angee/authored/notes.Note" });
  unmount();
  expect(unsubscribe).toHaveBeenCalledWith("subscription");
});

test("authored reads run with no live provider", async () => {
  const f = fixture();
  const { result } = renderHook(() => useAuthoredQuery(DOCUMENT, { id: "a" }, { models: ["notes.Note"] }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.data).toEqual({ notes: [{ id: "one" }] }));
  expect(result.current.error).toBeNull();
});


test("auth error policy covers data-only consumers and imperative refreshes", async () => {
  const failure = Object.assign(new Error("denied"), { statusCode: 403 });
  const f = fixture(vi.fn().mockRejectedValue(failure));
  renderHook(() => useAuthoredQuery(DOCUMENT, { id: "a" }).data, { wrapper: f.wrapper });
  await waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
  expect(f.notify).toHaveBeenCalledTimes(1);
  const { dataProvider: provider } = createRefineTestProviders({ dataProvider: { custom: f.custom } });
  await act(async () => {
    await f.client.fetchQuery(authoredQueryOptions(f.client, () => provider, "default", DOCUMENT, { id: "a" })).catch(() => undefined);
  });
  await waitFor(() => expect(f.onError).toHaveBeenCalledTimes(2));
  expect(f.notify).toHaveBeenCalledTimes(2);
});

test("imperative authored refresh preserves all cache-registered model interests", async () => {
  const client = createClient();
  const { dataProvider: provider } = createRefineTestProviders({
    dataProvider: { custom: async () => ({ data: { notes: [{ id: "a" }] } }) },
  });
  await client.fetchQuery(authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" }, ["notes.Note"]));
  await client.fetchQuery(authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" }, ["iam.User"]));
  const options = authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" });
  await client.fetchQuery(options);
  expect(client.getQueryCache().find({ queryKey: options.queryKey, exact: true })?.meta?.angeeModels).toEqual(["iam.User", "notes.Note"]);
});

test("a broad observer wins over an exact-record observer sharing one authored query", () => {
  const client = createClient();
  const provider = dataProvider;
  const exact = authoredQueryOptions(
    client, () => provider, "default", DOCUMENT, { id: "a" },
    ["notes.Note"], [{ model: "notes.Note", id: "note-a" }],
  );
  authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" }, ["notes.Note"]);
  const meta = client.getQueryCache().find({ queryKey: exact.queryKey, exact: true })?.meta;
  expect(meta?.angeeBroadModels).toEqual(["notes.Note"]);
  expect(meta?.angeeRecords).toBeUndefined();
});

test("a related-only model with no direct rows does not become model-wide", () => {
  const client = createClient();
  const provider = dataProvider;
  const options = authoredQueryOptions(
    client, () => provider, "default", DOCUMENT, { id: "a" },
    ["workflows.StepRun"], [], ["workflows.StepRun"],
  );
  const meta = client.getQueryCache().find({ queryKey: options.queryKey, exact: true })?.meta;
  expect(meta?.angeeRelatedModels).toEqual(["workflows.StepRun"]);
  expect(meta?.angeeBroadModels).toBeUndefined();
  expect(meta?.angeeRecords).toBeUndefined();
});

test("native custom hashing keeps one query and still reports data-only consumer failures", async () => {
  const f = fixture(vi.fn().mockRejectedValue(new Error("denied")));
  f.client.setDefaultOptions({ queries: { retry: false, queryKeyHashFn: (key) => `custom:${JSON.stringify(key)}` } });
  renderHook(() => useAuthoredQuery(DOCUMENT, { id: "a" }, { models: ["notes.Note"] }).data, { wrapper: f.wrapper });
  await waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
  const queries = f.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "finite"] });
  expect(queries).toHaveLength(1);
  expect(queries[0]?.queryHash.startsWith("custom:")).toBe(true);
  expect(queries[0]?.meta?.angeeModels).toEqual(["notes.Note"]);
});

test("shared host default metadata is not mutated or reused between authored cache entries", () => {
  const meta = { host: "test", angeeModels: ["common.Model"] };
  const client = createClient({ defaultOptions: { queries: { meta } } });
  const provider = dataProvider;
  const first = authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" }, ["notes.Note"]);
  const second = authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "b" }, ["iam.User"]);
  expect(first.meta?.angeeModels).toEqual(["common.Model", "notes.Note"]);
  expect(second.meta?.angeeModels).toEqual(["common.Model", "iam.User"]);
  expect(meta).toEqual({ host: "test", angeeModels: ["common.Model"] });
});
