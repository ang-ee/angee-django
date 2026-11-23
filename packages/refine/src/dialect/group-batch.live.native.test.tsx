// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { parse, type DocumentNode } from "graphql";
import { focusManager } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { createAngeeChangeLiveProvider } from "../provider";
import { useAuthoredQuery } from "./authored-hooks";
import { extractGroupBy, groupByVariables } from "../operations";
import type { TypedDocumentNode } from "../typed-document";
import { invalidateAuthoredQueries } from "../query-invalidation";
import { createRefineTestProviders } from "../testing";
import { useAngeeAggregate, useAngeeFacets, useAngeeGroupBy, useAngeeGroupByBatch, type GroupByBatchScope } from "./hooks";

// Decoding counted, its result unchanged.
vi.mock("../operations", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../operations")>();
  return { ...actual, extractGroupBy: vi.fn(actual.extractGroupBy) };
});

const TARGET = { dataProviderName: "console", root: "messages_groups", modelLabel: "messaging.Message" };
const DOCUMENT = "query Groups { messages_groups { key { channel_id } aggregate { count } } totalCount }";
const AST_DOCUMENT = JSON.parse(JSON.stringify(parse(DOCUMENT))) as DocumentNode;
const OTHER_DOCUMENT = "query OtherGroups { messages_groups { key { channel_id } aggregate { count } } totalCount }";
const SCOPES: readonly GroupByBatchScope[] = [{ key: "channels", query: {
  dimensions: [{ input: "CHANNEL", key: "channel_id" }], page: 1, pageSize: 50,
} }];
const { Provider, createClient, clearClients } = createRefineTestProviders({
  apiUrl: "test://group-live", providerNames: ["console", "archive"],
});
afterEach(() => { cleanup(); clearClients(); vi.useRealTimers(); focusManager.setFocused(undefined); });

function response(count: number) {
  return { data: { messages_groups: Array.from({ length: count }, (_, index) => ({
    key: { channel_id: `channel-${index}` }, aggregate: { count: index + 1 },
  })), totalCount: count } };
}
function fixture(custom = vi.fn(async () => response(1))) {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const sinks: { next: (value: unknown) => void }[] = [];
  const disposers: ReturnType<typeof vi.fn>[] = [];
  const subscribe = vi.fn((_request: { query: string }, sink: { next: (value: unknown) => void }) => {
    sinks.push(sink);
    const dispose = vi.fn();
    disposers.push(dispose);
    return dispose;
  });
  const liveProvider = createAngeeChangeLiveProvider({ subscribe, on: vi.fn(() => () => undefined) } as never,
    [{ schemaName: "console", modelLabel: "messaging.Message", roots: { list: "messages", changes: "messageChanged" } },
      { schemaName: "console", modelLabel: "messaging.Part", roots: { changes: "partChanged" } }],
    { queryClient: client });
  const provider = { custom };
  const onError = vi.fn(async () => ({}));
  const notify = vi.fn();
  function wrapper({ children }: { children: ReactNode }) {
    return <Provider dataProvider={provider} liveProvider={liveProvider} queryClient={client}
      authProvider={{ login: async () => ({ success: true }), logout: async () => ({ success: true }),
        check: async () => ({ authenticated: true }), onError }}
      notificationProvider={{ open: notify, close: vi.fn() }}>{children}</Provider>;
  }
  const change = () => sinks.at(-1)!.next({ data: { messageChanged: {
    model: "messaging.Message", id: "message-a", action: "create",
  } } });
  const relatedChange = (model: string) => {
    const index = subscribe.mock.calls.findIndex(([request]) => request.query.includes("angee_partChanged"));
    sinks[index]!.next({ data: { partChanged: {
      model: "messaging.Part", id: "part-1", relatedRecords: [{ model, id: "off-page" }], action: "create",
    } } });
  };
  return { wrapper, client, custom, subscribe, disposers, change, relatedChange, onError, notify };
}

test("grouped lists refresh buckets for related off-page records without needing a leaf subscription", async () => {
  const f = fixture();
  const { result, unmount } = renderHook(() => useAngeeGroupByBatch(TARGET, SCOPES,
    { document: DOCUMENT, relatedModels: ["messaging.Part"] }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.get("channels")?.totalCount).toBe(1));
  expect(f.subscribe).toHaveBeenCalledTimes(2);
  vi.useFakeTimers();
  f.custom.mockResolvedValue(response(2));
  await act(async () => { f.relatedChange("messaging.Channel"); await vi.advanceTimersByTimeAsync(300); });
  expect(f.custom).toHaveBeenCalledTimes(1);
  await act(async () => { f.relatedChange("messaging.Message"); await vi.advanceTimersByTimeAsync(301); });
  expect(f.custom).toHaveBeenCalledTimes(2);
  expect(result.current.get("channels")?.totalCount).toBe(2);
  unmount();
  for (const dispose of f.disposers) expect(dispose).toHaveBeenCalledTimes(1);
});

test("group-only views refresh lanes and counts through native live model invalidation without leaf queries", async () => {
  const f = fixture();
  const { result, rerender } = renderHook(({ scopes, enabled }) =>
    useAngeeGroupByBatch(TARGET, scopes, { document: DOCUMENT, enabled }),
    { initialProps: { scopes: SCOPES, enabled: true }, wrapper: f.wrapper });
  await waitFor(() => expect(result.current.get("channels")?.totalCount).toBe(1));
  await waitFor(() => expect(f.subscribe).toHaveBeenCalledTimes(1));
  expect(f.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "finite"] })[0]?.meta)
    .toMatchObject({ angeeModels: ["messaging.Message"], angeeBroadModels: ["messaging.Message"] });
  f.custom.mockResolvedValue(response(2));
  await act(async () => { f.change(); });
  await waitFor(() => expect(result.current.get("channels")?.totalCount).toBe(2));
  expect(result.current.get("channels")?.buckets.map((bucket) => bucket.key?.channel_id)).toEqual(["channel-0", "channel-1"]);
  expect(f.custom).toHaveBeenCalledTimes(2);
  await act(async () => { await invalidateAuthoredQueries(f.client, ["notes.Note"]); });
  expect(f.custom).toHaveBeenCalledTimes(2);
  rerender({ scopes: SCOPES, enabled: false });
  await waitFor(() => expect(f.disposers[0]).toHaveBeenCalledTimes(1));
  expect(result.current.size).toBe(0);
  rerender({ scopes: [], enabled: true });
  expect(f.subscribe).toHaveBeenCalledTimes(1);
});

test("active group scopes share one changes subscription and tear it down with the final observer", async () => {
  const f = fixture();
  const { unmount } = renderHook(() => useAngeeGroupByBatch(TARGET,
    [...SCOPES, { ...SCOPES[0]!, key: "nested" }], { document: DOCUMENT }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  expect(f.subscribe).toHaveBeenCalledTimes(1);
  f.custom.mockResolvedValue(response(3));
  await act(async () => { f.change(); });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
  unmount();
  expect(f.disposers[0]).toHaveBeenCalledTimes(1);
});

test("two mounted group consumers with different result labels share one in-flight request", async () => {
  let complete!: (value: ReturnType<typeof response>) => void;
  const pending = new Promise<ReturnType<typeof response>>((resolve) => { complete = resolve; });
  const f = fixture(vi.fn(() => pending));
  const { result } = renderHook(() => ({
    first: useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }),
    second: useAngeeGroupByBatch(TARGET, [{ ...SCOPES[0]!, key: "other-panel" }], { document: DOCUMENT }),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  expect(f.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "finite"] })).toHaveLength(1);
  await act(async () => { complete(response(1)); });
  await waitFor(() => expect(result.current.first.get("channels")?.totalCount).toBe(1));
  expect(result.current.second.get("other-panel")?.totalCount).toBe(1);
});

test("string and generated AST documents with the same operation share one in-flight request", async () => {
  let complete!: (value: ReturnType<typeof response>) => void;
  const pending = new Promise<ReturnType<typeof response>>((resolve) => { complete = resolve; });
  const f = fixture(vi.fn(() => pending));
  const { result } = renderHook(() => ({
    source: useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }),
    ast: useAngeeGroupByBatch(TARGET, SCOPES, { document: AST_DOCUMENT }),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  await act(async () => { complete(response(1)); });
  await waitFor(() => expect(result.current.ast.get("channels")?.totalCount).toBe(1));
  expect(result.current.source.get("channels")?.totalCount).toBe(1);
});

test("equal variables with different documents remain separate requests", async () => {
  const f = fixture();
  renderHook(() => ({
    first: useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }),
    second: useAngeeGroupByBatch(TARGET, SCOPES, { document: OTHER_DOCUMENT }),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
  expect(f.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "finite"] })).toHaveLength(2);
});

test("equal documents and variables on different providers remain separate requests", async () => {
  const f = fixture();
  renderHook(() => ({
    first: useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }),
    second: useAngeeGroupByBatch({ ...TARGET, dataProviderName: "archive" }, SCOPES, { document: DOCUMENT }),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
  expect(f.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "finite"] })).toHaveLength(2);
});

test("group transport receives the native query cancellation signal", async () => {
  let complete!: (value: ReturnType<typeof response>) => void;
  const pending = new Promise<ReturnType<typeof response>>((resolve) => { complete = resolve; });
  const f = fixture(vi.fn(() => pending));
  renderHook(() => useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  const [request] = f.custom.mock.calls[0] as unknown as [{ meta: { signal: AbortSignal } }];
  const signal = request.meta.signal;
  expect(signal).toBeInstanceOf(AbortSignal);
  await act(async () => { await f.client.cancelQueries({ queryKey: ["angee", "authored", "finite"] }); });
  expect(signal.aborted).toBe(true);
  await act(async () => { complete(response(1)); });
});

test("a single group consumer and a batch consumer share the request", async () => {
  let complete!: (value: ReturnType<typeof response>) => void;
  const pending = new Promise<ReturnType<typeof response>>((resolve) => { complete = resolve; });
  const f = fixture(vi.fn(() => pending));
  const { result } = renderHook(() => ({
    single: useAngeeGroupBy(TARGET, { document: DOCUMENT, ...SCOPES[0]!.query }),
    batch: useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  await act(async () => { complete(response(1)); });
  await waitFor(() => expect(result.current.single.totalCount).toBe(1));
  expect(result.current.batch.get("channels")?.totalCount).toBe(1);
});

test("different group variables remain separate requests", async () => {
  const f = fixture();
  renderHook(() => useAngeeGroupByBatch(TARGET, [
    SCOPES[0]!,
    { key: "next-page", query: { ...SCOPES[0]!.query, page: 2 } },
  ], { document: DOCUMENT }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
  expect(f.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "finite"] })).toHaveLength(2);
});

test("a single group consumer refreshes through live model invalidation", async () => {
  const f = fixture();
  const { result } = renderHook(() => useAngeeGroupBy(TARGET,
    { document: DOCUMENT, ...SCOPES[0]!.query }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.totalCount).toBe(1));
  await waitFor(() => expect(f.subscribe).toHaveBeenCalledTimes(1));
  f.custom.mockResolvedValue(response(2));
  await act(async () => { f.change(); });
  await waitFor(() => expect(result.current.totalCount).toBe(2));
  expect(f.custom).toHaveBeenCalledTimes(2);
});

test("one failed group request checks auth and notifies once across consumers", async () => {
  const error = Object.assign(new Error("denied"), { statusCode: 401 });
  const f = fixture(vi.fn().mockRejectedValue(error));
  const { result } = renderHook(() => ({
    single: useAngeeGroupBy(TARGET, { document: DOCUMENT, ...SCOPES[0]!.query }),
    batch: useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.single.error).toBe(error));
  await waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
  expect(f.notify).toHaveBeenCalledTimes(1);
  expect(f.custom).toHaveBeenCalledTimes(1);
});

test("a failed facet read checks auth and notifies", async () => {
  const error = Object.assign(new Error("denied"), { statusCode: 401 });
  const f = fixture(vi.fn().mockRejectedValue(error));
  const { result } = renderHook(() => useAngeeFacets(TARGET, {
    document: DOCUMENT,
    facets: [{ id: "channels", ...SCOPES[0]!.query }],
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.error).toBe(error));
  await waitFor(() => expect(f.onError).toHaveBeenCalledTimes(1));
  expect(f.notify).toHaveBeenCalledTimes(1);
  expect(f.custom).toHaveBeenCalledTimes(1);
});

test("single group read retains its last buckets with the refetch error", async () => {
  const f = fixture();
  const { result } = renderHook(() => useAngeeGroupBy(TARGET,
    { document: DOCUMENT, ...SCOPES[0]!.query }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.totalCount).toBe(1));
  const error = new Error("refresh failed");
  f.custom.mockRejectedValueOnce(error);
  await act(async () => { result.current.refetch(); });
  await waitFor(() => expect(result.current.error).toBe(error));
  expect(result.current.totalCount).toBe(1);
  expect(result.current.buckets).toHaveLength(1);
  expect(f.onError).toHaveBeenCalledTimes(1);
});

test("a live event during initial group discovery lets that request commit, then refetches once it settles", async () => {
  let accept!: (value: ReturnType<typeof response>) => void;
  const pending = new Promise<ReturnType<typeof response>>((resolve) => { accept = resolve; });
  const f = fixture(vi.fn().mockReturnValueOnce(pending).mockResolvedValue(response(2)));
  const { result } = renderHook(() => useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  await act(async () => { f.change(); });
  // Past the coalescing window the running request is still not restarted.
  await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 400); }); });
  expect(f.custom).toHaveBeenCalledTimes(1);
  await act(async () => { accept(response(1)); });
  // It settles, so the change it may have missed refetches at once.
  await waitFor(() => expect(result.current.get("channels")?.totalCount).toBe(2));
  expect(f.custom).toHaveBeenCalledTimes(2);
});

test("group and general authored reads share one request and model invalidation", async () => {
  let complete!: (value: ReturnType<typeof response>) => void;
  const custom = vi.fn(() => new Promise<ReturnType<typeof response>>((resolve) => { complete = resolve; }));
  const f = fixture(custom);
  const document = AST_DOCUMENT as TypedDocumentNode<ReturnType<typeof response>["data"], Record<string, unknown>>;
  const { result } = renderHook(() => ({
    grouped: useAngeeGroupByBatch(TARGET, SCOPES, { document }),
    authored: useAuthoredQuery(document, groupByVariables(SCOPES[0]!.query), { dataProviderName: "console" }),
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(1));
  expect(f.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "finite"] })).toHaveLength(1);
  await act(async () => { complete(response(2)); });
  await waitFor(() => expect(result.current.grouped.get("channels")?.totalCount).toBe(2));
  expect(result.current.authored.data?.totalCount).toBe(2);
  custom.mockResolvedValue(response(3));
  await act(async () => { await invalidateAuthoredQueries(f.client, [TARGET.modelLabel]); });
  await waitFor(() => expect(result.current.grouped.get("channels")?.totalCount).toBe(3));
  expect(result.current.authored.data?.totalCount).toBe(3);
  expect(custom).toHaveBeenCalledTimes(2);
});

test("aggregate totals register shared model interests and retain success after failed refresh", async () => {
  const custom = vi.fn(async () => ({ data: { ...response(1).data, messages_aggregate: { aggregate: { count: 4 } } } }));
  const f = fixture(custom);
  const { result } = renderHook(() => useAngeeAggregate({ ...TARGET, root: "messages_aggregate" }, {
    document: "query Total { messages_aggregate { aggregate { count } } }", refetchInterval: 30_000,
  }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.aggregate?.count).toBe(4));
  const query = f.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "finite"] })[0];
  expect(query?.meta).toMatchObject({ angeeModels: [TARGET.modelLabel] });
  expect(query?.options).toMatchObject({ refetchInterval: 30_000 });
  custom.mockResolvedValue({ data: { ...response(1).data, messages_aggregate: { aggregate: { count: 5 } } } });
  await act(async () => { f.change(); });
  await waitFor(() => expect(result.current.aggregate?.count).toBe(5));
  custom.mockRejectedValue(new Error("Refresh unavailable"));
  await act(async () => { await invalidateAuthoredQueries(f.client, [TARGET.modelLabel]); });
  await waitFor(() => expect(result.current.error?.message).toBe("Refresh unavailable"));
  expect(result.current.aggregate?.count).toBe(5);
});

test("a render that changes nothing keeps every group and aggregate result; changed data changes it", async () => {
  const totals = (groups: number, count: number) => ({ data: {
    ...response(groups).data, messages_aggregate: { aggregate: { count } },
  } });
  const custom = vi.fn(async () => totals(1, 4));
  const f = fixture(custom);
  // Targets and scopes declared afresh on every render, as a surface's derivation does.
  const { result, rerender } = renderHook(({ title }) => ({
    title,
    batch: useAngeeGroupByBatch({ ...TARGET }, SCOPES.map((scope) => ({ ...scope })), { document: DOCUMENT }),
    single: useAngeeGroupBy({ ...TARGET }, { document: DOCUMENT, ...SCOPES[0]!.query }),
    aggregate: useAngeeAggregate({ ...TARGET, root: "messages_aggregate" }, {
      document: "query Total { messages_aggregate { aggregate { count } } }",
    }),
  }), { initialProps: { title: "First" }, wrapper: f.wrapper });
  await waitFor(() => {
    expect(result.current.batch.get("channels")?.totalCount).toBe(1);
    expect(result.current.single.totalCount).toBe(1);
    expect(result.current.aggregate.aggregate?.count).toBe(4);
  });
  const settled = result.current;
  rerender({ title: "Unrelated title" });
  expect(result.current.batch).toBe(settled.batch);
  expect(result.current.batch.get("channels")).toBe(settled.batch.get("channels"));
  expect(result.current.single).toBe(settled.single);
  expect(result.current.aggregate).toBe(settled.aggregate);

  custom.mockResolvedValue(totals(2, 5));
  await act(async () => { await invalidateAuthoredQueries(f.client, [TARGET.modelLabel]); });
  await waitFor(() => {
    expect(result.current.batch.get("channels")?.totalCount).toBe(2);
    expect(result.current.single.totalCount).toBe(2);
    expect(result.current.aggregate.aggregate?.count).toBe(5);
  });
  expect(result.current.batch).not.toBe(settled.batch);
  expect(result.current.single).not.toBe(settled.single);
  expect(result.current.aggregate).not.toBe(settled.aggregate);
});

test("of two group reads one settling decodes only it, and the other's result stays the same object", async () => {
  const answers = new Map<number, (value: ReturnType<typeof response>) => void>();
  const custom = vi.fn(({ meta }: { meta?: { gqlVariables?: { offset?: number } } }) =>
    new Promise<ReturnType<typeof response>>((resolve) => { answers.set(meta?.gqlVariables?.offset ?? 0, resolve); }));
  const f = fixture(custom as never);
  const scopes: readonly GroupByBatchScope[] = [SCOPES[0]!, { key: "next-page", query: { ...SCOPES[0]!.query, page: 2 } }];
  const { result } = renderHook(() => useAngeeGroupByBatch(TARGET, scopes, { document: DOCUMENT }), { wrapper: f.wrapper });
  await waitFor(() => expect(answers.size).toBe(2));
  await act(async () => { answers.get(0)!(response(1)); });
  await waitFor(() => expect(result.current.get("channels")?.totalCount).toBe(1));
  const first = result.current.get("channels");
  const decoded = vi.mocked(extractGroupBy).mock.calls.length;
  await act(async () => { answers.get(50)!(response(2)); });
  await waitFor(() => expect(result.current.get("next-page")?.totalCount).toBe(2));
  expect(result.current.get("channels")).toBe(first);
  expect(vi.mocked(extractGroupBy).mock.calls.slice(decoded).map(([data]) => (data as { totalCount: number }).totalCount))
    .toEqual([2]);

  // A refetch answering equal data moves that result's settle time, and only its.
  const next = result.current.get("next-page")!;
  const requested = custom.mock.calls.length;
  await new Promise((resolve) => { setTimeout(resolve, 5); });
  act(() => result.current.get("next-page")!.refetch());
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(requested + 1));
  await act(async () => { answers.get(50)!(response(2)); });
  await waitFor(() => expect(result.current.get("next-page")?.updatedAt).toBeGreaterThan(next.updatedAt!));
  expect(result.current.get("next-page")).not.toBe(next);
  expect(result.current.get("channels")).toBe(first);
});

test("a group read inserted at the front or removed from the middle decodes nothing else again", async () => {
  const custom = vi.fn(async ({ meta }: { meta?: { gqlVariables?: { offset?: number } } }) =>
    response(1 + (meta?.gqlVariables?.offset ?? 0) / 50));
  const f = fixture(custom as never);
  const page = (index: number): GroupByBatchScope => ({ key: `page-${index}`, query: { ...SCOPES[0]!.query, page: index } });
  const { result, rerender } = renderHook(
    ({ pages }) => useAngeeGroupByBatch(TARGET, pages.map(page), { document: DOCUMENT }),
    { initialProps: { pages: [2, 3, 4] }, wrapper: f.wrapper },
  );
  await waitFor(() => expect([...result.current.values()].every((entry) => entry.updatedAt !== null && !entry.fetching)).toBe(true));
  const settled = result.current;
  const decoded = vi.mocked(extractGroupBy).mock.calls.length;
  rerender({ pages: [1, 2, 3, 4] });
  for (const key of ["page-2", "page-3", "page-4"]) expect(result.current.get(key)).toBe(settled.get(key));
  rerender({ pages: [2, 4] });
  for (const key of ["page-2", "page-4"]) expect(result.current.get(key)).toBe(settled.get(key));
  // Only the inserted page (one group) may have decoded since.
  expect(vi.mocked(extractGroupBy).mock.calls.slice(decoded)
    .filter(([data]) => (data as { totalCount: number }).totalCount !== 1)).toEqual([]);
});

test("a group read whose payload the decoder refuses carries the error beside an intact sibling", async () => {
  const custom = vi.fn(async ({ meta }: { meta?: { gqlVariables?: { offset?: number } } }) =>
    meta?.gqlVariables?.offset ? { data: { messages_groups: [] } } : response(1));
  const f = fixture(custom as never);
  const scopes: readonly GroupByBatchScope[] = [SCOPES[0]!, { key: "next-page", query: { ...SCOPES[0]!.query, page: 2 } }];
  const { result } = renderHook(() => useAngeeGroupByBatch(TARGET, scopes, { document: DOCUMENT }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.get("next-page")?.error?.message).toBe("Grouped response requires a numeric totalCount"));
  expect(result.current.get("next-page")?.buckets).toEqual([]);
  expect(result.current.get("channels")).toMatchObject({ error: null, totalCount: 1 });
  // Reported as a failed read is: once, however often the batch renders.
  await waitFor(() => expect(f.notify).toHaveBeenCalledTimes(1));
  expect(f.notify).toHaveBeenCalledWith(expect.objectContaining({ type: "error", description: "Grouped response requires a numeric totalCount" }));
  expect(f.onError).toHaveBeenCalledTimes(1);
});

test("two facets of one read keep their own results, each keeping its identity while another read settles", async () => {
  let answerOther!: (value: ReturnType<typeof response>) => void;
  const custom = vi.fn(({ meta }: { meta?: { gqlVariables?: { offset?: number } } }) => meta?.gqlVariables?.offset
    ? new Promise<ReturnType<typeof response>>((resolve) => { answerOther = resolve; })
    : Promise.resolve(response(1)));
  const f = fixture(custom as never);
  const facets = [
    { id: "values", ...SCOPES[0]!.query },
    // The same read, its options taken from a key the buckets lack.
    { id: "absent", ...SCOPES[0]!.query, valueKey: "absent" },
    { id: "later", ...SCOPES[0]!.query, page: 2 },
  ];
  const { result } = renderHook(() => useAngeeFacets(TARGET, { document: DOCUMENT, facets }), { wrapper: f.wrapper });
  await waitFor(() => expect(result.current.facets.values?.options).toHaveLength(1));
  expect(custom).toHaveBeenCalledTimes(2);
  expect(result.current.facets.absent?.options).toEqual([]);
  const settled = result.current.facets;
  await act(async () => { answerOther(response(2)); });
  await waitFor(() => expect(result.current.facets.later?.options).toHaveLength(2));
  expect(result.current.facets.values).toBe(settled.values);
  expect(result.current.facets.absent).toBe(settled.absent);
});

test("native aggregate and group polling survives unrelated renders, pauses without focus, and stops on unmount", async () => {
  vi.useFakeTimers();
  focusManager.setFocused(true);
  const custom = vi.fn(async () => ({ data: {
    ...response(1).data, messages_aggregate: { aggregate: { count: 4 } },
  } }));
  const f = fixture(custom);
  const { result, rerender, unmount } = renderHook(({ title }) => ({
    title,
    aggregate: useAngeeAggregate({ ...TARGET, root: "messages_aggregate" }, {
      document: "query Total { messages_aggregate { aggregate { count } } }", refetchInterval: 30_000,
    }),
    grouped: useAngeeGroupBy(TARGET, {
      ...SCOPES[0]!.query, document: DOCUMENT, refetchInterval: 30_000,
    }),
  }), { initialProps: { title: "First" }, wrapper: f.wrapper });
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(result.current.aggregate.aggregate?.count).toBe(4);
  expect(result.current.grouped.totalCount).toBe(1);
  expect(custom).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(14_000); });
  rerender({ title: "Unrelated title" });
  await act(async () => { await vi.advanceTimersByTimeAsync(14_000); });
  expect(custom).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  expect(custom).toHaveBeenCalledTimes(4);
  act(() => { focusManager.setFocused(false); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(custom).toHaveBeenCalledTimes(4);
  act(() => { focusManager.setFocused(true); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(custom).toHaveBeenCalledTimes(6);
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(custom).toHaveBeenCalledTimes(6);
});
