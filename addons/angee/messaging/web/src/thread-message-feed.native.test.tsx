// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { Refine, type DataProvider, type LiveProvider } from "@refinedev/core";
import { QueryClient, keepPreviousData } from "@tanstack/react-query";
import { authoredQueryReadsAnyModel, createAngeeChangeLiveProvider } from "@angee/refine";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const auth = vi.hoisted(() => ({ actor: "actor-a" as string | undefined }));
vi.mock("@angee/app", async (original) => ({
  ...(await original<typeof import("@angee/app")>()),
  useAuth: () => ({ user: auth.actor ? { id: auth.actor } : null }),
}));

import { ThreadTranscriptDocument, ThreadTranscriptRevalidateDocument } from "./documents";
import { messageFeedRows } from "./message-feed";
import { useThreadMessageFeed } from "./thread-message-feed";

const clients: QueryClient[] = [];
beforeEach(() => { auth.actor = "actor-a"; });
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });

const row = (id: number) => ({ id: `message-${id}`, feed_order_key: `v1:000${id}`, preview: `Message ${id}` });
const page = (messages = [row(3), row(2)], more = true) => ({
  messages, count: 4, older_cursor: messages.length ? `opaque-${messages.at(-1)!.id}` : null,
  has_older: more, has_more_in_window: false, has_older_than_through: more,
});

function fixture(nativeLive = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, placeholderData: keepPreviousData } } });
  clients.push(client);
  let refreshed = false;
  let denied = false;
  const custom = vi.fn(async ({ meta }: { meta: { gqlQuery: unknown; gqlVariables: Record<string, unknown>; signal: AbortSignal } }) => {
    if (denied) throw new Error("Unreadable thread");
    const variables = meta.gqlVariables;
    if (meta.gqlQuery === ThreadTranscriptRevalidateDocument) {
      const ids = variables.ids as string[];
      return { data: { thread_message_feed_revalidate: {
        messages: ids.filter((id) => id !== "message-2").map((id) => row(Number(id.slice(-1)))),
        absent_ids: ids.filter((id) => id === "message-2"),
      } } };
    }
    expect(meta.gqlQuery).toBe(ThreadTranscriptDocument);
    return { data: { thread_message_feed: variables.beforeCursor
      ? page([row(1)], false)
      : page(refreshed ? [row(4), row(3)] : undefined),
    } };
  });
  const provider = { getApiUrl: () => "test://messages", getList: vi.fn(), getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(), custom } as unknown as DataProvider;
  const subscribe = vi.fn<LiveProvider["subscribe"]>(() => "messages");
  const unsubscribe = vi.fn();
  const roots = [
    ["messaging.Message", "messageChanged"], ["messaging.Thread", "threadChanged"],
    ["parties.Handle", "handleChanged"], ["parties.Party", "partyChanged"], ["storage.File", "fileChanged"],
  ] as const;
  const sinks = new Map<string, { next(value: unknown): void }>();
  const nativeProvider = createAngeeChangeLiveProvider({
    subscribe: ({ query }: { query: string }, sink: { next(value: unknown): void }) => {
      const root = roots.find(([, field]) => query.includes(`angee_${field} `))![1];
      sinks.set(root, sink);
      return () => { sinks.delete(root); };
    },
    on: () => () => undefined,
  } as never, roots.map(([modelLabel, changes]) => ({ schemaName: "console", modelLabel, roots: { changes } })), { queryClient: client });
  const onError = vi.fn(async () => ({}));
  const notify = vi.fn();
  function Providers({ children }: { children: ReactNode }) {
    return <Refine dataProvider={provider} liveProvider={nativeLive ? nativeProvider : { subscribe, unsubscribe }}
      authProvider={{ login: async () => ({ success: true }), logout: async () => ({ success: true }), check: async () => ({ authenticated: true }), onError }}
      notificationProvider={{ open: notify, close: vi.fn() }}
      options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>{children}</Refine>;
  }
  return { client, custom, subscribe, unsubscribe, onError, notify, wrapper: Providers,
    refresh: () => { refreshed = true; }, deny: () => { denied = true; },
    emit: (model: string) => {
      const root = roots.find(([label]) => label === model)![1];
      expect(sinks.has(root)).toBe(true);
      sinks.get(root)!.next({ data: { [root]: { model, id: "changed", action: "update" } } });
    } };
}

test("real Refine transport sends fixed cuts and revalidation documents through one native Query entry", async () => {
  const feed = fixture();
  const { result, unmount } = renderHook(() => useThreadMessageFeed("thread-a"), { wrapper: feed.wrapper });
  await waitFor(() => expect(messageFeedRows(result.current.data)).toHaveLength(2));
  expect(feed.custom.mock.calls[0]?.[0].meta.gqlVariables).toEqual({ threadId: "thread-a", beforeCursor: null, throughCursor: null, limit: 50 });
  await act(async () => { await result.current.fetchNextPage({ cancelRefetch: false }); });
  expect(feed.custom.mock.calls[1]?.[0].meta.gqlVariables).toMatchObject({ beforeCursor: "opaque-message-2", throughCursor: null });
  feed.refresh();
  await act(async () => { await feed.client.invalidateQueries({ predicate: (query) => authoredQueryReadsAnyModel(query.meta, ["messaging.Reaction"]) }); });
  await waitFor(() => expect(messageFeedRows(result.current.data).map((message) => message.id)).toEqual(["message-4", "message-3", "message-1"]));
  const revalidations = feed.custom.mock.calls.filter(([request]) => request.meta.gqlQuery === ThreadTranscriptRevalidateDocument);
  expect(revalidations.map(([request]) => request.meta.gqlVariables.ids)).toEqual([["message-3", "message-2"], ["message-1"]]);
  expect(feed.custom.mock.calls[2]?.[0].meta.gqlVariables).toMatchObject({ beforeCursor: null, throughCursor: "opaque-message-2" });
  expect(feed.client.getQueryCache().findAll({ queryKey: ["angee", "authored"] })).toHaveLength(1);
  expect(feed.subscribe).toHaveBeenCalledWith(expect.objectContaining({ params: { models: [
    "messaging.Message", "messaging.Reaction", "messaging.Thread", "parties.Handle", "parties.Party", "storage.File",
  ] } }));
  expect(feed.custom.mock.calls[0]?.[0].meta.signal).toBeInstanceOf(AbortSignal);
  unmount(); expect(feed.unsubscribe).toHaveBeenCalledWith("messages");
});

test.each(["parties.Handle", "parties.Party", "messaging.Thread", "storage.File"])("actual %s change events revalidate the transcript projection", async (model) => {
  const feed = fixture(true);
  const { result } = renderHook(() => useThreadMessageFeed("thread-a"), { wrapper: feed.wrapper });
  await waitFor(() => expect(messageFeedRows(result.current.data)).toHaveLength(2));
  feed.refresh();
  act(() => feed.emit(model));
  await waitFor(() => expect(messageFeedRows(result.current.data).map((message) => message.id)).toEqual(["message-4", "message-3"]));
  expect(feed.custom.mock.calls.some(([request]) => request.meta.gqlQuery === ThreadTranscriptRevalidateDocument)).toBe(true);
});

test("resolved actor and thread changes never expose another native entry's rows", async () => {
  const feed = fixture();
  const { result, rerender } = renderHook(({ thread }) => useThreadMessageFeed(thread), { wrapper: feed.wrapper, initialProps: { thread: "thread-a" } });
  await waitFor(() => expect(result.current.data).toBeDefined());
  auth.actor = undefined; rerender({ thread: "thread-a" });
  expect(result.current.data).toBeUndefined(); expect(result.current.isFetching).toBe(false);
  expect(feed.custom).toHaveBeenCalledTimes(1);
  auth.actor = "actor-b"; rerender({ thread: "thread-a" });
  expect(result.current.data).toBeUndefined();
  await waitFor(() => expect(feed.custom).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(result.current.data).toBeDefined());
  rerender({ thread: "thread-b" }); expect(result.current.data).toBeUndefined();
  await waitFor(() => expect(feed.custom).toHaveBeenCalledTimes(3));
  expect(feed.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "message-feed", "actor-a"] })).toHaveLength(1);
  expect(feed.client.getQueryCache().findAll({ queryKey: ["angee", "authored", "message-feed", "actor-b"] })).toHaveLength(2);
});

test("native retained-data errors invoke Refine auth and notification policy once", async () => {
  const feed = fixture();
  const { result } = renderHook(() => ({ first: useThreadMessageFeed("thread-a"), second: useThreadMessageFeed("thread-a") }), { wrapper: feed.wrapper });
  await waitFor(() => expect(result.current.first.data).toBeDefined());
  feed.deny();
  await act(async () => { await result.current.first.refetch(); });
  await waitFor(() => expect(result.current.first.isRefetchError).toBe(true));
  expect(result.current.first.data).toBe(result.current.second.data);
  expect(messageFeedRows(result.current.first.data)).toHaveLength(2);
  await waitFor(() => expect(feed.onError).toHaveBeenCalledTimes(1));
  expect(feed.notify).toHaveBeenCalledTimes(1);
});

test("cancelling a provider request that ignores abort prevents subsequent window and ID batches", async () => {
  const feed = fixture();
  const { result } = renderHook(() => useThreadMessageFeed("thread-a"), { wrapper: feed.wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  const previous = result.current.data;
  let release!: (value: Awaited<ReturnType<typeof feed.custom>>) => void;
  feed.custom.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const refresh = result.current.refetch();
  await waitFor(() => expect(feed.custom).toHaveBeenCalledTimes(2));
  await act(async () => { await feed.client.cancelQueries({ queryKey: ["angee", "authored"] }); });
  await act(async () => {
    release({ data: { thread_message_feed: { ...page([row(5), row(4)]), has_more_in_window: true } } });
    await refresh;
  });
  expect(feed.custom).toHaveBeenCalledTimes(2);
  expect(result.current.data).toBe(previous);
});
