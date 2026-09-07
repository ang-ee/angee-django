// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { createAngeeChangeLiveProvider } from "../provider";
import { invalidateAuthoredQueries } from "../query-invalidation";
import { useAngeeGroupByBatch, type GroupByBatchScope } from "./hooks";

const TARGET = { dataProviderName: "console", root: "messages_groups", modelLabel: "messaging.Message" };
const DOCUMENT = "query Groups { messages_groups { key { channel_id } aggregate { count } } totalCount }";
const SCOPES: readonly GroupByBatchScope[] = [{ key: "channels", query: {
  dimensions: [{ input: "CHANNEL", key: "channel_id" }], page: 1, pageSize: 50,
} }];
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); });

function response(count: number) {
  return { data: { messages_groups: Array.from({ length: count }, (_, index) => ({
    key: { channel_id: `channel-${index}` }, aggregate: { count: index + 1 },
  })), totalCount: count } };
}
function fixture(custom = vi.fn(async () => response(1))) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  const sinks: { next: (value: unknown) => void }[] = [];
  const disposers: ReturnType<typeof vi.fn>[] = [];
  const subscribe = vi.fn((_request: unknown, sink: { next: (value: unknown) => void }) => {
    sinks.push(sink);
    const dispose = vi.fn();
    disposers.push(dispose);
    return dispose;
  });
  const liveProvider = createAngeeChangeLiveProvider({ subscribe, on: vi.fn(() => () => undefined) } as never,
    [{ schemaName: "console", modelLabel: "messaging.Message", roots: { list: "messages", changes: "messageChanged" } }],
    { queryClient: client });
  const provider = { getApiUrl: () => "test://group-live", custom, getList: vi.fn(), getOne: vi.fn(),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  function wrapper({ children }: { children: ReactNode }) {
    return <Refine dataProvider={{ default: provider, console: provider }} liveProvider={liveProvider}
      options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>{children}</Refine>;
  }
  const change = () => sinks.at(-1)!.next({ data: { messageChanged: {
    model: "messaging.Message", id: "message-a", action: "create",
  } } });
  return { wrapper, client, custom, subscribe, disposers, change };
}

test("group-only views refresh lanes and counts through native live model invalidation without leaf queries", async () => {
  const f = fixture();
  const { result, rerender } = renderHook(({ scopes, enabled }) =>
    useAngeeGroupByBatch(TARGET, scopes, { document: DOCUMENT, enabled }),
    { initialProps: { scopes: SCOPES, enabled: true }, wrapper: f.wrapper });
  await waitFor(() => expect(result.current.get("channels")?.totalCount).toBe(1));
  await waitFor(() => expect(f.subscribe).toHaveBeenCalledTimes(1));
  expect(f.client.getQueryCache().findAll({ queryKey: ["angee", "group-by"] })[0]?.meta)
    .toEqual({ angeeModels: ["messaging.Message"] });
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
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
  expect(f.subscribe).toHaveBeenCalledTimes(1);
  f.custom.mockResolvedValue(response(3));
  await act(async () => { f.change(); });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(4));
  unmount();
  expect(f.disposers[0]).toHaveBeenCalledTimes(1);
});

test("a live event during initial group discovery restarts the native query and ignores its late stale response", async () => {
  let accept!: (value: ReturnType<typeof response>) => void;
  const pending = new Promise<ReturnType<typeof response>>((resolve) => { accept = resolve; });
  const f = fixture(vi.fn().mockReturnValueOnce(pending).mockResolvedValue(response(2)));
  const { result } = renderHook(() => useAngeeGroupByBatch(TARGET, SCOPES, { document: DOCUMENT }), { wrapper: f.wrapper });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  await act(async () => { f.change(); });
  await waitFor(() => expect(result.current.get("channels")?.totalCount).toBe(2));
  await act(async () => { accept(response(1)); });
  expect(result.current.get("channels")?.totalCount).toBe(2);
});
