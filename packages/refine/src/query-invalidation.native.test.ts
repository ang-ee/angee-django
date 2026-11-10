import { InfiniteQueryObserver, isCancelledError, QueryClient, QueryObserver } from "@tanstack/react-query";
import { keys } from "@refinedev/core";
import { afterEach, expect, test, vi } from "vitest";

import {
  authoredQueryReadsChange,
  createLiveInvalidation,
  invalidateAuthoredQueries,
} from "./query-invalidation";

afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

test("a related-only model with no target rows is fail-closed", () => {
  expect(authoredQueryReadsChange({
    angeeModels: ["workflows.StepRun"],
    angeeRelatedModels: ["workflows.StepRun"],
  }, "workflows.StepRun", "wsr_unrelated")).toBe(false);
});

test("a related-only feed refreshes its record interest without subscribing to that record's model", async () => {
  vi.useFakeTimers();
  const cache = client();
  let reads = 0;
  const observer = new QueryObserver(cache, {
    queryKey: ["timeline", "note"],
    meta: { angeeModels: ["workflows.WorkflowRun"], angeeRelatedModels: ["workflows.WorkflowRun"],
      angeeRecords: [{ model: "notes.Note", id: "nte_current" }] },
    queryFn: async () => ++reads,
  });
  const unsubscribe = observer.subscribe(() => undefined);
  const live = createLiveInvalidation(cache);
  await vi.advanceTimersByTimeAsync(0);
  live.push({ model: "workflows.WorkflowRun", id: "wfr_other", relatedRecords: [{ model: "notes.Note", id: "nte_other" }] });
  await vi.advanceTimersByTimeAsync(300);
  expect(reads).toBe(1);
  live.push({ model: "workflows.WorkflowRun", id: "wfr_current", relatedRecords: [{ model: "notes.Note", id: "nte_current" }] });
  await vi.advanceTimersByTimeAsync(300);
  expect(reads).toBe(2);
  live.clear(); unsubscribe(); cache.clear();
});

test("an unrelated row event does not starve an exact record's pending first request", async () => {
  const cache = client();
  const first = pending<number>();
  let requests = 0;
  let firstSignal!: AbortSignal;
  const options = {
    queryKey: ["decision", "wdc_current"],
    meta: {
      angeeModels: ["workflows.Decision"],
      angeeRecords: [{ model: "workflows.Decision", id: "wdc_current" }],
    },
    queryFn: ({ signal }: { signal: AbortSignal }) => {
      requests++;
      if (requests === 1) {
        firstSignal = signal;
        return first.promise;
      }
      return Promise.resolve(requests);
    },
  };
  vi.useFakeTimers();
  const live = createLiveInvalidation(cache);
  const observer = new QueryObserver(cache, options);
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    live.push({ model: "workflows.Decision", id: "wdc_other" });
    await vi.advanceTimersByTimeAsync(300);
    expect(requests).toBe(1);
    expect(firstSignal.aborted).toBe(false);
    live.push({ model: "workflows.Decision", id: "wdc_current" });
    await vi.advanceTimersByTimeAsync(300);
    // The matching change waits for the running request instead of cancelling it.
    expect(requests).toBe(1);
    expect(firstSignal.aborted).toBe(false);
    first.resolve(1);
    await vi.advanceTimersByTimeAsync(300);
    expect(requests).toBe(2);
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toBe(2));
  } finally {
    first.resolve(1);
    unsubscribe(); cache.clear();
  }
});

test("a related child refreshes an exact parent without unrelated-child starvation", async () => {
  const cache = client();
  const first = pending<number>();
  let requests = 0;
  let firstSignal!: AbortSignal;
  const options = {
    queryKey: ["run-inspection", "wfr_current"],
    meta: {
      angeeModels: ["workflows.StepRun", "workflows.WorkflowRun"],
      angeeRecords: [{ model: "workflows.WorkflowRun", id: "wfr_current" }],
    },
    queryFn: ({ signal }: { signal: AbortSignal }) => {
      requests++;
      if (requests === 1) {
        firstSignal = signal;
        return first.promise;
      }
      return Promise.resolve(requests);
    },
  };
  vi.useFakeTimers();
  const live = createLiveInvalidation(cache);
  const observer = new QueryObserver(cache, options);
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    live.push({
      model: "workflows.StepRun", id: "wsr_other",
      relatedRecords: [{ model: "workflows.WorkflowRun", id: "wfr_other" }],
    });
    await vi.advanceTimersByTimeAsync(300);
    expect(requests).toBe(1);
    expect(firstSignal.aborted).toBe(false);
    live.push({
      model: "workflows.StepRun", id: "wsr_child",
      relatedRecords: [{ model: "workflows.WorkflowRun", id: "wfr_current" }],
    });
    await vi.advanceTimersByTimeAsync(300);
    expect(requests).toBe(1);
    expect(firstSignal.aborted).toBe(false);
    first.resolve(1);
    await vi.advanceTimersByTimeAsync(300);
    expect(requests).toBe(2);
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toBe(2));
  } finally {
    first.resolve(1);
    unsubscribe(); cache.clear();
  }
});

test("a caller write during the initial request cancels its snapshot and discards a late response", async () => {
  const cache = client();
  const old = pending<string[]>();
  let requests = 0;
  let oldSignal!: AbortSignal;
  const options = {
    queryKey: ["notes", "active"],
    meta: { angeeModels: ["notes.Note"] },
    queryFn: ({ signal }: { signal: AbortSignal }) => {
      requests++;
      if (requests === 1) {
        oldSignal = signal;
        // The transport deliberately ignores abort, as Hasura may do.
        return old.promise;
      }
      return Promise.resolve(["survivor"]);
    },
  };
  const observer = new QueryObserver(cache, options);
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    const invalidated = invalidateAuthoredQueries(cache, ["notes.Note"]);
    // Cancellation must happen before awaiting the old transport.
    expect(oldSignal.aborted).toBe(true);
    await invalidated;
    expect(requests).toBe(2);
    expect(observer.getCurrentResult().data).toEqual(["survivor"]);
    old.resolve(["survivor", "revoked"]);
    await old.promise;
    expect(observer.getCurrentResult().data).toEqual(["survivor"]);
    expect(cache.getQueryState(options.queryKey)?.isInvalidated).toBe(false);
  } finally {
    old.resolve([]);
    unsubscribe(); cache.clear();
  }
});

test.each([true, false])("empty %s-disabled/inactive initial requests cancel without refetching", async (disabled) => {
  const cache = client();
  const old = pending<string[]>();
  let requests = 0;
  let signal!: AbortSignal;
  const options = {
    queryKey: ["notes", disabled ? "disabled" : "inactive"],
    meta: { angeeModels: ["notes.Note"] },
    queryFn: (context: { signal: AbortSignal }) => {
      requests++; signal = context.signal;
      return old.promise;
    },
  };
  const observer = disabled ? new QueryObserver(cache, { ...options, enabled: false }) : undefined;
  const unsubscribe = observer?.subscribe(() => undefined);
  const request = cache.query(options).catch((error: unknown) => error);
  try {
    await invalidateAuthoredQueries(cache, ["notes.Note"]);
    expect(signal.aborted).toBe(true);
    expect(isCancelledError(await request)).toBe(true);
    expect(requests).toBe(1);
    expect(cache.getQueryState(options.queryKey)).toMatchObject({
      data: undefined, fetchStatus: "idle", isInvalidated: true,
    });
    expect(cache.getQueryCache().find({ queryKey: options.queryKey })?.getObserversCount()).toBe(disabled ? 1 : 0);
    old.resolve(["old-session"]); await old.promise;
    expect(cache.getQueryData(options.queryKey)).toBeUndefined();
  } finally {
    old.resolve([]);
    unsubscribe?.(); cache.clear();
  }
});

test("populated disabled/inactive queries retain data and unrelated initial queries remain pending", async () => {
  const cache = client();
  const unrelated = pending<string[]>();
  let requests = 0;
  let signal!: AbortSignal;
  const options = {
    queryKey: ["notes", "disabled"],
    meta: { angeeModels: ["notes.Note"] },
    queryFn: async () => { requests++; return ["current"]; },
  };
  const inactive = { ...options, queryKey: ["notes", "inactive"] };
  await cache.query(options); await cache.query(inactive);
  const observer = new QueryObserver(cache, { ...options, enabled: false });
  const unsubscribe = observer.subscribe(() => undefined);
  const other = cache.query({
    queryKey: ["tags"], meta: { angeeModels: ["notes.Tag"] },
    queryFn: (context) => { signal = context.signal; return unrelated.promise; },
  });
  try {
    await invalidateAuthoredQueries(cache, ["notes.Note"]);
    expect(requests).toBe(2);
    for (const queryKey of [options.queryKey, inactive.queryKey]) {
      expect(cache.getQueryState(queryKey)).toMatchObject({ data: ["current"], isInvalidated: true });
    }
    expect(signal.aborted).toBe(false);
    expect(cache.getQueryState(["tags"])?.isInvalidated).toBe(false);
    unrelated.resolve(["tag"]); await other;
    expect(cache.getQueryData(["tags"])).toEqual(["tag"]);
  } finally {
    unrelated.resolve([]);
    unsubscribe(); cache.clear();
  }
});

test("successive caller writes cancel a retained infinite refresh and commit the latest complete pages", async () => {
  const cache = client();
  const old = pending<{ rows: string[]; next: number | undefined }>();
  const started = pending<void>();
  let revision = "initial";
  let oldSignal!: AbortSignal;
  const options = {
    queryKey: ["notes", "pages"], meta: { angeeModels: ["notes.Note"] },
    initialPageParam: 0,
    queryFn: async ({ pageParam, signal }: { pageParam: number; signal: AbortSignal }) => {
      if (revision === "stale") { oldSignal = signal; started.resolve(); return old.promise; }
      return { rows: [`${revision}-${pageParam}`], next: pageParam === 0 ? 1 : undefined };
    },
    getNextPageParam: (page: { next: number | undefined }) => page.next,
  };
  const observer = new InfiniteQueryObserver(cache, options);
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    await observer.refetch(); await observer.fetchNextPage();
    revision = "stale";
    const first = invalidateAuthoredQueries(cache, ["notes.Note"]);
    await started.promise;
    revision = "current";
    const second = invalidateAuthoredQueries(cache, ["notes.Note"]);
    await Promise.all([first, second]);
    expect(oldSignal.aborted).toBe(true);
    expect(observer.getCurrentResult().data?.pages.flatMap((page) => page.rows)).toEqual(["current-0", "current-1"]);
    old.resolve({ rows: ["revoked"], next: 1 }); await old.promise;
    expect(observer.getCurrentResult().data?.pages.flatMap((page) => page.rows)).toEqual(["current-0", "current-1"]);
  } finally {
    old.resolve({ rows: [], next: undefined });
    unsubscribe(); cache.clear();
  }
});

test("a burst of row changes refetches each affected read once and leaves unrelated reads alone", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createLiveInvalidation(cache);
  const counts = { inbox: 0, accounts: 0 };
  const inbox = new QueryObserver(cache, {
    queryKey: ["inbox"], meta: { angeeModels: ["messaging.Message", "messaging.Part"] },
    queryFn: async () => ++counts.inbox,
  });
  const accounts = new QueryObserver(cache, {
    queryKey: ["accounts"], meta: { angeeModels: ["integrate.Integration"] },
    queryFn: async () => ++counts.accounts,
  });
  const stop = [inbox.subscribe(() => undefined), accounts.subscribe(() => undefined)];
  try {
    await vi.waitFor(() => expect(counts).toEqual({ inbox: 1, accounts: 1 }));
    for (let message = 0; message < 20; message++) {
      live.push({ model: "messaging.Message", id: `msg_${message}` });
      live.push({ model: "messaging.Part", id: `prt_${message}`, relatedRecords: [{ model: "messaging.Message", id: `msg_${message}` }] });
      await vi.advanceTimersByTimeAsync(10);
    }
    expect(counts.inbox).toBe(1);
    await vi.advanceTimersByTimeAsync(300);
    await vi.waitFor(() => expect(counts.inbox).toBe(2));
    expect(counts.accounts).toBe(1);
  } finally {
    stop.forEach((unsubscribe) => unsubscribe()); cache.clear();
  }
});

test("a continuous change stream still flushes within the max wait", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createLiveInvalidation(cache);
  let requests = 0;
  const observer = new QueryObserver(cache, {
    queryKey: ["inbox"], meta: { angeeModels: ["messaging.Message"] },
    queryFn: async () => ++requests,
  });
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    await vi.waitFor(() => expect(requests).toBe(1));
    for (let tick = 0; tick < 20; tick++) {
      live.push({ model: "messaging.Message", id: `msg_${tick}` });
      await vi.advanceTimersByTimeAsync(200);
    }
    // 4 s of changes every 200 ms: two max-wait flushes, never one per change.
    expect(requests).toBe(3);
  } finally {
    unsubscribe(); cache.clear();
  }
});

test("a live change lets a running refresh commit, then refetches once it settles", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createLiveInvalidation(cache);
  const running = pending<string[]>();
  let revision = "initial";
  let runningSignal!: AbortSignal;
  const observer = new QueryObserver(cache, {
    queryKey: ["notes", "live"],
    meta: { angeeModels: ["notes.Note"] },
    queryFn: ({ signal }: { signal: AbortSignal }) => {
      if (revision === "running") { runningSignal = signal; return running.promise; }
      return Promise.resolve([revision]);
    },
  });
  const seen: unknown[] = [];
  const unsubscribe = observer.subscribe((result) => { seen.push(result.data); });
  try {
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["initial"]));
    revision = "running";
    void observer.refetch();
    await vi.waitFor(() => expect(runningSignal).toBeDefined());
    revision = "current";
    live.push({ model: "notes.Note", id: "note_changed" });
    await vi.advanceTimersByTimeAsync(300);
    expect(runningSignal.aborted).toBe(false);
    running.resolve(["snapshot"]);
    // The change arrived after that read started, so it refetches as it settles.
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["current"]));
    expect(seen).toContainEqual(["snapshot"]);
    expect(seen.findIndex((data) => JSON.stringify(data) === '["snapshot"]'))
      .toBeLessThan(seen.findIndex((data) => JSON.stringify(data) === '["current"]'));
  } finally {
    running.resolve([]);
    unsubscribe(); cache.clear();
  }
});

test("a continuous stream never starves a read slower than its gaps", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createLiveInvalidation(cache);
  let requests = 0;
  let commits = 0;
  const observer = new QueryObserver(cache, {
    queryKey: ["inbox", "groups"],
    meta: { angeeModels: ["messaging.Message"] },
    // Each read takes 2.5 s: longer than the max wait and than any gap below.
    queryFn: () => {
      const request = ++requests;
      return new Promise<number>((resolve) => { setTimeout(() => resolve(request), 2500); });
    },
  });
  const unsubscribe = observer.subscribe((result) => { if (result.isSuccess) commits++; });
  try {
    for (let tick = 0; tick < 100; tick++) {
      live.push({ model: "messaging.Message", id: `msg_${tick}` });
      await vi.advanceTimersByTimeAsync(100);
    }
    // 10 s of changes every 100 ms: reads keep completing back to back.
    expect(commits).toBeGreaterThanOrEqual(3);
    await vi.advanceTimersByTimeAsync(6000);
    // The last read started after the last change.
    expect(observer.getCurrentResult().data).toBe(requests);
    expect(observer.getCurrentResult().fetchStatus).toBe("idle");
  } finally {
    unsubscribe(); cache.clear();
  }
});

test("authored and resource reads share one burst and settle tracking without touching another provider", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createLiveInvalidation(cache);
  const running = pending<number>();
  const scope = keys().data("console").resource("console:notes.Note").get();
  const counts = { authored: 0, resource: 0, other: 0 };
  const authored = new QueryObserver(cache, {
    queryKey: ["note-summary"], meta: { angeeModels: ["notes.Note"] },
    queryFn: async () => ++counts.authored,
  });
  const resource = new QueryObserver(cache, {
    queryKey: keys().data("console").resource("console:notes.Note").action("one").id("note_1").get(),
    queryFn: () => ++counts.resource === 1 ? running.promise : Promise.resolve(counts.resource),
  });
  const other = new QueryObserver(cache, {
    queryKey: keys().data("public").resource("console:notes.Note").action("one").id("note_1").get(),
    queryFn: async () => ++counts.other,
  });
  const stop = [authored.subscribe(() => undefined), resource.subscribe(() => undefined), other.subscribe(() => undefined)];
  const callback = vi.fn(() => {
    void cache.invalidateQueries({ queryKey: scope, type: "active" }, { cancelRefetch: false });
  });
  const delivery = { queryKey: scope, callback };
  try {
    await vi.advanceTimersByTimeAsync(0);
    for (let event = 0; event < 10; event++) live.push({ model: "notes.Note", id: `note_${event}` }, [delivery]);
    await vi.advanceTimersByTimeAsync(299);
    expect(counts).toEqual({ authored: 1, resource: 1, other: 1 });
    expect(callback).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(counts).toEqual({ authored: 2, resource: 1, other: 1 });
    expect(callback).toHaveBeenCalledTimes(1);
    running.resolve(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(counts).toEqual({ authored: 2, resource: 2, other: 1 });
    expect(resource.getCurrentResult().data).toBe(2);
  } finally {
    live.clear();
    running.resolve(1);
    stop.forEach((unsubscribe) => unsubscribe()); cache.clear();
  }
});
