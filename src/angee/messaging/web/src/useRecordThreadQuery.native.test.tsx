// @vitest-environment happy-dom
import { createAngeeChangeLiveProvider } from "@angee/refine";
import { createRefineTestProviders } from "@angee/refine/testing";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { RecordFollowingDocument, RecordThreadDocument, SetRecordFollowingDocument } from "./documents";
import { useRecordFollowingQuery, useRecordThreadQuery, useRecordFollowing } from "./useRecordThreadQuery";

const { Provider, createClient, clearClients } = createRefineTestProviders();
afterEach(() => { cleanup(); clearClients(); });

test("a parent ledger event refreshes its discussion and follow reads without a message feed", async () => {
  let count = 0;
  const custom = vi.fn(async () => ({ data: { record_thread: { unread_count: count } } }));
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const sinks: Array<{ next: (value: unknown) => void }> = [];
  const subscribe = vi.fn((_request: unknown, sink: { next: (value: unknown) => void }) => {
    sinks.push(sink);
    return vi.fn();
  });
  const liveProvider = createAngeeChangeLiveProvider({ subscribe, on: vi.fn(() => vi.fn()) } as never,
    [{ schemaName: "console", modelLabel: "notes.Note", roots: { list: "notes", changes: "noteChanged" } }],
    { queryClient: client });
  const { result } = renderHook(() => ({
    discussion: useRecordThreadQuery({ modelLabel: "notes.Note", recordId: "nte_1" }),
    follow: useRecordThreadQuery({ modelLabel: "notes.Note", recordId: "nte_1" }),
    other: useRecordThreadQuery({ modelLabel: "notes.Note", recordId: "nte_2" }),
  }), { wrapper: ({ children }: { children: ReactNode }) =>
    <Provider queryClient={client} dataProvider={{ custom }} liveProvider={liveProvider}>{children}</Provider>,
  });
  await waitFor(() => expect(result.current.other.data?.record_thread.unread_count).toBe(0));
  expect(custom).toHaveBeenCalledTimes(2);
  expect(subscribe).toHaveBeenCalledTimes(1);

  count = 1;
  await act(async () => sinks[0]!.next({ data: { noteChanged: {
    model: "notes.Note", id: "nte_1", action: "update",
  } } }));
  await waitFor(() => {
    expect(result.current.discussion.data?.record_thread.unread_count).toBe(1);
    expect(result.current.follow.data?.record_thread.unread_count).toBe(1);
  });
  expect(result.current.other.data?.record_thread.unread_count).toBe(0);
  expect(custom).toHaveBeenCalledTimes(3);
});

test("an unsaved record neither reads nor subscribes", () => {
  const custom = vi.fn();
  const subscribe = vi.fn(() => "thread-subscription");
  renderHook(() => useRecordThreadQuery({ modelLabel: "notes.Note", recordId: "" }), {
    wrapper: ({ children }: { children: ReactNode }) => <Provider dataProvider={{ custom }}
      liveProvider={{ subscribe, unsubscribe: vi.fn() }}>{children}</Provider>,
  });
  expect(custom).not.toHaveBeenCalled();
  expect(subscribe).not.toHaveBeenCalled();
});

test("the Follow bell reads its compact document and only follows record-related thread changes", async () => {
  const custom = vi.fn(async (_params: { meta?: { gqlQuery?: unknown } }) =>
    ({ data: { record_thread: { error_code: null, is_following: false, subtypes: [] } } }));
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const sinks: Array<{ next: (value: unknown) => void }> = [];
  const subscribe = vi.fn((_request: unknown, sink: { next: (value: unknown) => void }) => {
    sinks.push(sink);
    return vi.fn();
  });
  const liveProvider = createAngeeChangeLiveProvider({ subscribe, on: vi.fn(() => vi.fn()) } as never,
    [
      { schemaName: "console", modelLabel: "notes.Note", roots: { list: "notes", changes: "noteChanged" } },
      { schemaName: "console", modelLabel: "messaging.Thread", roots: { list: "threads", changes: "threadChanged" } },
    ], { queryClient: client });
  renderHook(() => useRecordFollowingQuery({ modelLabel: "notes.Note", recordId: "nte_1" }), {
    wrapper: ({ children }: { children: ReactNode }) =>
      <Provider queryClient={client} dataProvider={{ custom }} liveProvider={liveProvider}>{children}</Provider>,
  });
  await waitFor(() => expect(custom).toHaveBeenCalledOnce());
  expect(custom.mock.calls[0]?.[0].meta?.gqlQuery).toBe(RecordFollowingDocument);
  expect(subscribe).toHaveBeenCalledTimes(2);
  const thread = sinks[1]!;
  await act(async () => thread.next({ data: { threadChanged: {
    model: "messaging.Thread", id: "thd_elsewhere", action: "update",
  } } }));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
  expect(custom).toHaveBeenCalledOnce();
  await act(async () => thread.next({ data: { threadChanged: {
    model: "messaging.Thread", id: "thd_here", action: "update",
    relatedRecords: [{ model: "notes.Note", id: "nte_1" }],
  } } }));
  await waitFor(() => expect(custom).toHaveBeenCalledTimes(2));
});


test("a follow accepts its projection while omitted thread state still reconciles", async () => {
  let reads = 0;
  let finishRefresh!: () => void;
  const refresh = new Promise<void>((resolve) => { finishRefresh = resolve; });
  const custom = vi.fn(async ({ meta }: { meta?: { gqlQuery?: unknown; gqlMutation?: unknown } }) => {
    if (meta?.gqlMutation === SetRecordFollowingDocument) return { data: { set_record_following: {
      error: null, error_code: null, follower_count: 1, is_following: true,
      follower: null, followers: [],
    } } };
    expect(meta?.gqlQuery).toBe(RecordThreadDocument);
    reads += 1;
    if (reads > 1) await refresh;
    return { data: { record_thread: { error_code: null, is_following: reads > 1,
      follower_count: reads > 1 ? 1 : 0, notifications: reads > 1 ? [] : [{ id: "old-derived-row" }],
    } } };
  });
  const { result } = renderHook(() => ({
    read: useRecordThreadQuery({ modelLabel: "notes.Note", recordId: "nte_1" }),
    write: useRecordFollowing("notes.Note", "nte_1"),
  }), { wrapper: ({ children }: { children: ReactNode }) => <Provider dataProvider={{ custom }}>{children}</Provider> });
  await waitFor(() => expect(result.current.read.data?.record_thread.is_following).toBe(false));
  let settled = false;
  await act(async () => {
    await result.current.write[0]({ modelLabel: "notes.Note", recordId: "nte_1", following: true });
    settled = true;
  });
  expect(settled).toBe(true);
  await waitFor(() => expect(result.current.read.data?.record_thread.is_following).toBe(true));
  expect(result.current.read.data?.record_thread.notifications).toHaveLength(1);
  await waitFor(() => expect(reads).toBe(2));
  await act(async () => { finishRefresh(); });
  await waitFor(() => expect(result.current.read.data?.record_thread.notifications).toEqual([]));
});
