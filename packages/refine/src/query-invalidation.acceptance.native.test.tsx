// @vitest-environment happy-dom
import { act, cleanup, renderHook } from "@testing-library/react";
import { isCancelledError, QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { createAngeeChangeLiveProvider } from "./provider";
import { createAuthoredLiveInvalidation, acceptQueryData, useAcceptQueryData, useReconcileMutationResponse } from "./query-invalidation";
import { createRefineTestProviders } from "./testing";

const { Provider, createClient, clearClients } = createRefineTestProviders();
afterEach(() => { cleanup(); clearClients(); vi.clearAllTimers(); vi.useRealTimers(); });

test("actual complete acceptance settles before held derived reads without rereading its exact projection", async () => {
  vi.useFakeTimers();
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const detailKey = ["accepted-detail"];
  const detailRead = vi.fn(async () => ({ title: "old" }));
  let release!: (value: string) => void;
  const derivedRead = vi.fn(() => new Promise<string>((resolve) => { release = resolve; }));
  client.setQueryData(detailKey, { title: "old" });
  client.setQueryData(["derived"], "old total");
  const detail = new QueryObserver(client, { queryKey: detailKey, queryFn: detailRead, meta: { angeeModels: ["notes.Note"] } });
  const derived = new QueryObserver(client, { queryKey: ["derived"], queryFn: derivedRead, meta: { angeeModels: ["notes.Note"] } });
  const stopDetail = detail.subscribe(() => undefined);
  const stopDerived = derived.subscribe(() => undefined);
  const liveProvider = createAngeeChangeLiveProvider({ subscribe: () => () => undefined, on: () => () => undefined } as never, [], { queryClient: client });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={liveProvider}>{children}</Provider>;
  const hook = renderHook(() => useReconcileMutationResponse(), { wrapper });
  try {
    await act(async () => {
      await hook.result.current.batch(async (reconciliation) => {
        await hook.result.current({ title: "accepted" }, {}, {
          reconciliation,
          invalidateModels: ["notes.Note"],
          acceptResponse: (data) => acceptQueryData(client, detailKey, data),
        });
        await hook.result.current(undefined, undefined, { queryKeys: [detailKey], reconciliation });
      });
    });
    expect(client.getQueryData(detailKey)).toEqual({ title: "accepted" });
    expect(detailRead).not.toHaveBeenCalled();
    expect(derived.getCurrentResult().isStale).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(detailRead).not.toHaveBeenCalled();
    expect(derivedRead).toHaveBeenCalledOnce();
    expect(derived.getCurrentResult().fetchStatus).toBe("fetching");
    await act(async () => { release("new total"); await vi.advanceTimersByTimeAsync(0); });
    expect(derived.getCurrentResult().data).toBe("new total");
  } finally { liveProvider.setEnabled(false); stopDetail(); stopDerived(); }
});

test("local command still cancels a data-less read on arrival", async () => {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  let finish!: (value: string) => void;
  const first = new Promise<string>((resolve) => { finish = resolve; });
  let signal!: AbortSignal;
  let reads = 0;
  const observer = new QueryObserver(client, {
    queryKey: ["command-detail"], meta: { angeeModels: ["notes.Note"] },
    queryFn: ({ signal: current }: { signal: AbortSignal }) => {
      if (++reads === 1) { signal = current; return first; }
      return Promise.resolve("current");
    },
  });
  const stop = observer.subscribe(() => undefined);
  const scheduler = createAuthoredLiveInvalidation(client);
  try {
    await scheduler.batch(async (scope) => {
      await scope!.reconcile({ changes: [{ model: "notes.Note", id: "n1" }] });
      expect(signal.aborted).toBe(true);
    });
    expect(reads).toBe(2);
    expect(observer.getCurrentResult().data).toBe("current");
    finish("obsolete");
    await first;
    expect(observer.getCurrentResult().data).toBe("current");
  } finally {
    finish("cleanup");
    scheduler.clear(); stop(); client.clear();
  }
});


test.each(["before", "during"])("actor suspension %s exact cancellation rejects the captured old acceptance", async (when) => {
  const client = createClient();
  const liveProvider = createAngeeChangeLiveProvider({ subscribe: () => () => undefined, on: () => () => undefined } as never, [], { queryClient: client });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={liveProvider}>{children}</Provider>;
  const hook = renderHook(() => useAcceptQueryData(), { wrapper });
  const oldAccept = hook.result.current;
  let release!: () => void;
  const cancelled = new Promise<void>((resolve) => { release = resolve; });
  if (when === "during") vi.spyOn(client, "cancelQueries").mockImplementationOnce(() => cancelled);
  const suspend = () => {
    liveProvider.setEnabled(false);
    client.removeQueries();
    liveProvider.setEnabled(true);
    hook.rerender();
  };
  if (when === "before") act(suspend);
  const completion = oldAccept(["private-detail"], { secret: "old actor" });
  const failure = completion.catch((error: unknown) => error);
  if (when === "during") act(suspend);
  release();
  expect(isCancelledError(await failure)).toBe(true);
  expect(client.getQueryData(["private-detail"])).toBeUndefined();
  await act(async () => { await hook.result.current(["private-detail"], { secret: "current actor" }); });
  expect(client.getQueryData(["private-detail"])).toEqual({ secret: "current actor" });
  liveProvider.setEnabled(false);
});


test("repeated local-only suspension invalidates a callback captured while already suspended", async () => {
  const client = createClient();
  const provider = createAngeeChangeLiveProvider(undefined, [], { queryClient: client });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={provider}>{children}</Provider>;
  const hook = renderHook(() => useAcceptQueryData(), { wrapper });
  act(() => { provider.setEnabled(false); hook.rerender(); });
  const suspendedAccept = hook.result.current;
  act(() => { provider.setEnabled(false); provider.setEnabled(true); hook.rerender(); });
  const failure = await suspendedAccept(["private"], "old actor").catch((error: unknown) => error);
  expect(isCancelledError(failure)).toBe(true);
  expect(client.getQueryData(["private"])).toBeUndefined();
  await act(async () => { await hook.result.current(["private"], "new actor"); });
  expect(client.getQueryData(["private"])).toBe("new actor");
  provider.setEnabled(false);
});


test("batch acceptance excludes only its fallback; a later independent live change still reads", async () => {
  vi.useFakeTimers();
  const client = createClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  const key = ["exact-form", { fields: ["title"] }];
  client.setQueryData(key, { title: "accepted" });
  const read = vi.fn(async () => ({ title: "remote" }));
  const observer = new QueryObserver(client, { queryKey: key, queryFn: read,
    meta: { angeeModels: ["notes.Note"], angeeRecords: [{ model: "notes.Note", id: "n1" }] } });
  const stop = observer.subscribe(() => undefined);
  const scheduler = createAuthoredLiveInvalidation(client);
  try {
    await scheduler.batch(async (scope) => {
      await scope!.reconcile({ changes: [{ model: "notes.Note", id: "n1" }],
        accepted: new Set([client.getQueryCache().find({ queryKey: key, exact: true })!.queryHash]), background: true });
      await scope!.reconcile({ changes: [], queryKeys: [key] });
    });
    expect(read).not.toHaveBeenCalled();
    scheduler.push({ model: "notes.Note", id: "n1" });
    await vi.advanceTimersByTimeAsync(300);
    expect(read).toHaveBeenCalledOnce();
    expect(observer.getCurrentResult().data).toEqual({ title: "remote" });
  } finally { scheduler.clear(); stop(); }
});


test.each([true, false])("exact fallback respects no-effects response completeness (%s)", async (complete) => {
  const client = createClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  const key = ["form-projection", { fields: ["title", "count"] }];
  client.setQueryData(key, { title: "before", count: 1 });
  const read = vi.fn(async () => ({ title: "authoritative", count: 2 }));
  const observer = new QueryObserver(client, { queryKey: key, queryFn: read });
  const stop = observer.subscribe(() => undefined);
  const provider = createAngeeChangeLiveProvider(undefined, [], { queryClient: client });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={provider}>{children}</Provider>;
  const hook = renderHook(() => useReconcileMutationResponse(), { wrapper });
  try {
    await act(async () => { await hook.result.current.batch(async (reconciliation) => {
      await hook.result.current(undefined, undefined, {
        reconciliation,
        acceptResponse: () => acceptQueryData(client, key, complete
          ? { title: "accepted", count: 2 } : { title: "accepted", count: 1 }, { complete }),
      });
      await hook.result.current(undefined, undefined, { queryKeys: [key], reconciliation });
    }); });
    expect(read).toHaveBeenCalledTimes(complete ? 0 : 1);
    expect(observer.getCurrentResult().data?.title).toBe(complete ? "accepted" : "authoritative");
  } finally { provider.setEnabled(false); stop(); }
});


test.each([true, false])("overlapping acceptance callbacks exclude only returned exact evidence (A returns evidence=%s)", async (returnEvidence) => {
  const client = createClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  const aKey = ["concurrent-A"];
  const bKey = ["concurrent-B"];
  client.setQueryData(aKey, { title: "before A" });
  client.setQueryData(bKey, { title: "before B" });
  const readA = vi.fn(async () => ({ title: "canonical A" }));
  let releaseReadB!: () => void;
  const readB = vi.fn(() => new Promise<{ title: string }>((resolve) => { releaseReadB = () => resolve({ title: "canonical B" }); }));
  const observers = [
    new QueryObserver(client, { queryKey: aKey, queryFn: readA, meta: { angeeModels: ["notes.Note"], angeeRecords: [{ model: "notes.Note", id: "A" }] } }),
    new QueryObserver(client, { queryKey: bKey, queryFn: readB, meta: { angeeModels: ["notes.Note"], angeeRecords: [{ model: "notes.Note", id: "B" }] } }),
  ];
  const stops = observers.map((observer) => observer.subscribe(() => undefined));
  const provider = createAngeeChangeLiveProvider(undefined, [], { queryClient: client });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={provider}>{children}</Provider>;
  const hook = renderHook(() => useReconcileMutationResponse(), { wrapper });
  let acceptedA!: () => void;
  const aAccepted = new Promise<void>((resolve) => { acceptedA = resolve; });
  let resumeA!: () => void;
  const aHeld = new Promise<void>((resolve) => { resumeA = resolve; });
  let completionA!: Promise<void>;
  try {
    act(() => { completionA = hook.result.current.batch(async (reconciliation) => {
      await hook.result.current(undefined, undefined, { reconciliation, invalidateModels: ["notes.Note"],
        acceptResponse: async () => {
          const evidence = await acceptQueryData(client, aKey, { title: "accepted A" });
          acceptedA();
          await aHeld;
          return returnEvidence ? evidence : undefined;
        },
      });
      await hook.result.current(undefined, undefined, { reconciliation, queryKeys: [aKey] });
    }); });
    await aAccepted;
    await act(async () => { await hook.result.current.batch(async (reconciliation) => {
      await hook.result.current(undefined, undefined, { reconciliation,
        records: [{ model: "notes.Note", id: "B" }],
        acceptResponse: () => acceptQueryData(client, bKey, { title: "accepted B" }),
      });
      await hook.result.current(undefined, undefined, { reconciliation, queryKeys: [bKey] });
    }); });
    expect(readA).not.toHaveBeenCalled();
    expect(readB).not.toHaveBeenCalled();
    await act(async () => { resumeA(); await completionA; });
    expect(readA).toHaveBeenCalledTimes(returnEvidence ? 0 : 1);
    // B completed during A's asynchronous callback. Its marker is not evidence
    // of an A-owned adoption, so A's later broad effect still reconciles B.
    expect(readB).toHaveBeenCalledOnce();
    expect(observers[1]!.getCurrentResult().fetchStatus).toBe("fetching");
    await act(async () => { releaseReadB(); });
    expect(client.getQueryData(bKey)).toEqual({ title: "canonical B" });
  } finally { provider.setEnabled(false); stops.forEach((stop) => stop()); }
});


test("native numeric mutation IDs reconcile exact string row and related interests", async () => {
  const client = createClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  const reads = [vi.fn(async () => "current"), vi.fn(async () => "current"), vi.fn(async () => "unrelated")];
  const interests = [
    { angeeModels: ["notes.Note"], angeeRecords: [{ model: "notes.Note", id: "42" }] },
    { angeeModels: ["notes.Binding"], angeeRelatedModels: ["notes.Binding"], angeeRecords: [{ model: "notes.Target", id: "99" }] },
    { angeeModels: ["notes.Note"], angeeRecords: [{ model: "notes.Note", id: "43" }] },
  ];
  const observers = interests.map((meta, index) => {
    const queryKey = ["numeric-effect", index];
    client.setQueryData(queryKey, "before");
    return new QueryObserver(client, { queryKey, queryFn: reads[index]!, meta });
  });
  const stops = observers.map((observer) => observer.subscribe(() => undefined));
  const provider = createAngeeChangeLiveProvider(undefined, [], { queryClient: client });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={provider}>{children}</Provider>;
  const hook = renderHook(() => useReconcileMutationResponse(), { wrapper });
  try {
    await act(async () => { await hook.result.current.batch(async (reconciliation) => {
      await hook.result.current(undefined, undefined, { reconciliation, records: [
        { model: "notes.Note", id: 42 },
        { model: "notes.Binding", id: 7, relatedRecords: [{ model: "notes.Target", id: 99 }] },
      ] });
    }); });
    expect(reads[0]).toHaveBeenCalledOnce();
    expect(reads[1]).toHaveBeenCalledOnce();
    expect(reads[2]).not.toHaveBeenCalled();
  } finally { provider.setEnabled(false); stops.forEach((stop) => stop()); }
});
