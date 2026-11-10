// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useCustom, useInfiniteList, useList, useMany, useOne, type MetaQuery } from "@refinedev/core";
import { StrictMode, type ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { createAngeeChangeLiveProvider, createAngeeHasuraDataProvider } from "./provider";
import { createRefineTestProviders } from "./testing";

const { Provider, createClient, clearClients } = createRefineTestProviders();
afterEach(() => { cleanup(); clearClients(); vi.clearAllTimers(); vi.useRealTimers(); });
const readers = [
  ["useCustom", () => useCustom({ url: "", method: "get", meta: { operation: "notes", fields: ["id"] } }).query],
  ["useList", () => useList({ resource: "notes" }).query],
  ["useOne", () => useOne({ resource: "notes", id: "one" }).query],
  ["useMany", () => useMany({ resource: "notes", ids: ["one"] }).query],
  ["useInfiniteList", () => useInfiniteList({ resource: "notes" }).query],
] as const;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

test.each(readers)("%s shares pending Hasura transport across StrictMode and remount", async (_name, useRead) => {
  const pending = deferred<Response>();
  const request = vi.fn(() => pending.promise);
  const dataProvider = createAngeeHasuraDataProvider({
    url: "https://example.invalid/graphql", auth: (fetch) => fetch, fetch: request,
  });
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const wrapper = ({ children }: { children: ReactNode }) => <StrictMode><Provider dataProvider={dataProvider} queryClient={client}>{children}</Provider></StrictMode>;
  const first = renderHook(() => useRead().isSuccess, { wrapper });
  expect(request).toHaveBeenCalledTimes(1);
  first.unmount();
  const second = renderHook(() => useRead().isSuccess, { wrapper });
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve(new Response(JSON.stringify({ data: {
    notes: [{ id: "one" }], notes_by_pk: { id: "one" },
    notes_aggregate: { aggregate: { count: 1 } },
  } }), { headers: { "Content-Type": "application/json" } })); });
  await waitFor(() => expect(second.result.current).toBe(true));
  expect(request).toHaveBeenCalledTimes(1);
});

test.each(readers)("%s cancels on unmount when its provider consumes the signal", async (_name, useRead) => {
  const pending = deferred<{ data: { id: string }[]; total: number }>();
  let signal: AbortSignal | undefined;
  const request = vi.fn(({ meta }: { meta?: MetaQuery }) => {
    signal = meta?.signal;
    return pending.promise;
  });
  const dataProvider = { custom: request, getList: request, getOne: request, getMany: request };
  const client = createClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider dataProvider={dataProvider} queryClient={client}>{children}</Provider>;
  const first = renderHook(() => useRead().isSuccess, { wrapper });
  const query = client.getQueryCache().getAll().find((entry) => entry.state.fetchStatus === "fetching");
  expect(signal?.aborted).toBe(false);
  first.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => { pending.resolve({ data: [{ id: "late" }], total: 1 }); });
  expect(query?.state.data).toBeUndefined();
  expect(request).toHaveBeenCalledTimes(1);
});

test.each(["initial", "refresh"])("resource live bursts let a running %s request settle before rereading", async (phase) => {
  vi.useFakeTimers();
  const pending = deferred<{ data: { id: string; title: string } }>();
  let slow = phase === "initial";
  let signal: AbortSignal | undefined;
  const getOne = vi.fn(({ meta }: { meta?: MetaQuery }) => {
    if (slow) {
      signal = meta?.signal;
      return pending.promise;
    }
    return Promise.resolve({ data: { id: "note_1", title: "current" } });
  });
  let emit: () => void = () => { throw new Error("No change subscription"); };
  const subscribe = vi.fn((_payload: unknown, sink: { next: (result: { data: unknown }) => void }) => {
    emit = () => sink.next({ data: { noteChanged: { model: "notes.Note", id: "note_1", action: "update" } } });
    return () => undefined;
  });
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const liveProvider = createAngeeChangeLiveProvider(
    { subscribe, on: () => () => undefined } as never,
    [{ schemaName: "console", modelLabel: "notes.Note", identifier: "console:notes.Note",
      roots: { list: "notes", changes: "noteChanged" } }],
    { queryClient: client },
  );
  const onLiveEvent = vi.fn();
  const dataProvider = { getOne };
  const wrapper = ({ children }: { children: ReactNode }) => <Provider
    dataProvider={dataProvider} queryClient={client} providerNames={["console"]}
    resources={[{ name: "notes", identifier: "console:notes.Note", meta: { dataProviderName: "console" } }]}
    liveProvider={liveProvider} options={{ liveMode: "auto" }}
  >{children}</Provider>;
  const hook = renderHook(() => {
    const first = useOne({ resource: "console:notes.Note", id: "note_1", onLiveEvent }).query;
    // Two callbacks share a read. Neither may cancel the other's refresh.
    useOne({ resource: "console:notes.Note", id: "note_1", onLiveEvent });
    return first;
  }, { wrapper });
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    if (phase === "refresh") {
      slow = true;
      act(() => { void hook.result.current.refetch(); });
    }
    expect(signal?.aborted).toBe(false);
    const before = getOne.mock.calls.length;
    await act(async () => {
      for (let event = 0; event < 20; event++) {
        emit();
        await vi.advanceTimersByTimeAsync(100);
      }
    });
    expect(subscribe).toHaveBeenCalledTimes(1);
    expect(onLiveEvent).toHaveBeenCalledTimes(2);
    expect(signal?.aborted).toBe(false);
    expect(getOne).toHaveBeenCalledTimes(before);
    slow = false;
    await act(async () => {
      pending.resolve({ data: { id: "note_1", title: "snapshot" } });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getOne).toHaveBeenCalledTimes(before + 1);
    expect(hook.result.current.data?.data.title).toBe("current");
    expect(hook.result.current.fetchStatus).toBe("idle");
    // A later idle burst starts one read even with two mounted consumers.
    await act(async () => { emit(); await vi.advanceTimersByTimeAsync(300); });
    expect(getOne).toHaveBeenCalledTimes(before + 2);
  } finally {
    pending.resolve({ data: { id: "note_1", title: "snapshot" } });
    hook.unmount();
  }
});
