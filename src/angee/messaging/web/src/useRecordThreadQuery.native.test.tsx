// @vitest-environment happy-dom
import { createAngeeChangeLiveProvider } from "@angee/refine";
import { createRefineTestProviders } from "@angee/refine/testing";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useRecordThreadQuery } from "./useRecordThreadQuery";

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
    discussion: useRecordThreadQuery({ modelLabel: "notes.Note", recordId: "nte_1", messageLimit: 50 }),
    follow: useRecordThreadQuery({ modelLabel: "notes.Note", recordId: "nte_1", messageLimit: 1 }),
    other: useRecordThreadQuery({ modelLabel: "notes.Note", recordId: "nte_2", messageLimit: 50 }),
  }), { wrapper: ({ children }: { children: ReactNode }) =>
    <Provider queryClient={client} dataProvider={{ custom }} liveProvider={liveProvider}>{children}</Provider>,
  });
  await waitFor(() => expect(result.current.other.data?.record_thread.unread_count).toBe(0));
  expect(custom).toHaveBeenCalledTimes(3);
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
  expect(custom).toHaveBeenCalledTimes(5);
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
