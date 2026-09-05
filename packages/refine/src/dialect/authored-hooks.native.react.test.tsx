// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { Refine, type DataProvider, type LiveProvider } from "@refinedev/core";
import { QueryClient, keepPreviousData } from "@tanstack/react-query";
import { parse } from "graphql";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { authoredQueryKey, authoredQueryOptions, useAuthoredQuery, useAuthoredQueryBatch } from "./authored-hooks";
import type { TypedDocumentNode } from "../typed-document";
import { invalidateAuthoredQueries } from "../query-invalidation";

type Data = { notes: { id: string }[] };
type Variables = { id: string };
const DOCUMENT = parse("query Notes($id: ID!) { notes(id: $id) { id } }") as TypedDocumentNode<Data, Variables>;
const OTHER = parse("query ArchivedNotes($id: ID!) { notes(id: $id, archived: true) { id } }") as TypedDocumentNode<Data, Variables>;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

function fixture(custom = vi.fn(async () => ({ data: { notes: [{ id: "one" }] } })), liveProvider?: LiveProvider) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, placeholderData: keepPreviousData } } });
  clients.push(client);
  const provider = { getApiUrl: () => "test://query", getList: vi.fn(), getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(), custom } as unknown as DataProvider;
  const onError = vi.fn(async () => ({}));
  const notify = vi.fn();
  function Providers({ children }: { children: ReactNode }) {
    return <Refine dataProvider={{ default: provider, alternate: provider }}
      authProvider={{ login: async () => ({ success: true }), logout: async () => ({ success: true }), check: async () => ({ authenticated: true }), onError }}
      notificationProvider={{ open: notify, close: vi.fn() }} liveProvider={liveProvider}
      options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>{children}</Refine>;
  }
  return { client, custom, onError, notify, wrapper: Providers };
}

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
  const provider = { custom: f.custom } as unknown as DataProvider;
  await act(async () => {
    await f.client.fetchQuery(authoredQueryOptions(f.client, () => provider, "default", DOCUMENT, { id: "a" })).catch(() => undefined);
  });
  await waitFor(() => expect(f.onError).toHaveBeenCalledTimes(2));
  expect(f.notify).toHaveBeenCalledTimes(2);
});

test("imperative authored refresh preserves all cache-registered model interests", async () => {
  const client = new QueryClient();
  clients.push(client);
  const provider = { custom: async () => ({ data: { notes: [{ id: "a" }] } }) } as unknown as DataProvider;
  await client.fetchQuery(authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" }, ["notes.Note"]));
  await client.fetchQuery(authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" }, ["iam.User"]));
  const options = authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" });
  await client.fetchQuery(options);
  expect(client.getQueryCache().find({ queryKey: options.queryKey, exact: true })?.meta?.angeeModels).toEqual(["iam.User", "notes.Note"]);
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
  const client = new QueryClient({ defaultOptions: { queries: { meta } } });
  clients.push(client);
  const provider = {} as DataProvider;
  const first = authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "a" }, ["notes.Note"]);
  const second = authoredQueryOptions(client, () => provider, "default", DOCUMENT, { id: "b" }, ["iam.User"]);
  expect(first.meta?.angeeModels).toEqual(["common.Model", "notes.Note"]);
  expect(second.meta?.angeeModels).toEqual(["common.Model", "iam.User"]);
  expect(meta).toEqual({ host: "test", angeeModels: ["common.Model"] });
});
