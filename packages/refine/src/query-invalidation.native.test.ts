import { InfiniteQueryObserver, QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";

import { keysetFeedOptions, type KeysetFeedRevalidation, type KeysetFeedWindow } from "./dialect/keyset-feed";
import {
  authoredQueryReadsChange,
  acceptQueryData,
  createAuthoredLiveInvalidation,
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
  const live = createAuthoredLiveInvalidation(cache);
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
  const live = createAuthoredLiveInvalidation(cache);
  const observer = new QueryObserver(cache, options);
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    live.push({ model: "workflows.Decision", id: "wdc_other" });
    await vi.advanceTimersByTimeAsync(300);
    expect(requests).toBe(1);
    expect(firstSignal.aborted).toBe(false);
    live.push({ model: "workflows.Decision", id: "wdc_current" });
    await vi.advanceTimersByTimeAsync(300);
    expect(requests).toBe(1);
    expect(firstSignal.aborted).toBe(false);
    first.resolve(1);
    await vi.advanceTimersByTimeAsync(0);
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
  const live = createAuthoredLiveInvalidation(cache);
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
    await vi.advanceTimersByTimeAsync(0);
    expect(requests).toBe(2);
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toBe(2));
  } finally {
    first.resolve(1);
    unsubscribe(); cache.clear();
  }
});

test("imperative model invalidation lets the initial request settle before one fresh read", async () => {
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
        return old.promise;
      }
      return Promise.resolve(["survivor"]);
    },
  };
  const observer = new QueryObserver(cache, options);
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    const invalidated = invalidateAuthoredQueries(cache, ["notes.Note"]);
    expect(oldSignal.aborted).toBe(false);
    expect(requests).toBe(1);
    old.resolve(["survivor", "revoked"]);
    await invalidated;
    expect(requests).toBe(2);
    expect(observer.getCurrentResult().data).toEqual(["survivor"]);
    expect(oldSignal.aborted).toBe(false);
    expect(observer.getCurrentResult().data).toEqual(["survivor"]);
    expect(cache.getQueryState(options.queryKey)?.isInvalidated).toBe(false);
  } finally {
    old.resolve([]);
    unsubscribe(); cache.clear();
  }
});

test.each([true, false])("empty %s-disabled/inactive initial requests settle without refetching", async (disabled) => {
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
  const request = cache.fetchQuery(options);
  try {
    const invalidated = invalidateAuthoredQueries(cache, ["notes.Note"]);
    expect(signal.aborted).toBe(false);
    expect(requests).toBe(1);
    old.resolve(["old-session"]);
    expect(await request).toEqual(["old-session"]);
    await invalidated;
    expect(cache.getQueryState(options.queryKey)).toMatchObject({
      data: ["old-session"], fetchStatus: "idle", isInvalidated: true,
    });
    expect(cache.getQueryCache().find({ queryKey: options.queryKey })?.getObserversCount()).toBe(disabled ? 1 : 0);
    expect(signal.aborted).toBe(false);
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
  await cache.fetchQuery(options); await cache.fetchQuery(inactive);
  const observer = new QueryObserver(cache, { ...options, enabled: false });
  const unsubscribe = observer.subscribe(() => undefined);
  const other = cache.fetchQuery({
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

test("successive imperative invalidations retain an infinite refresh and finish with current pages", async () => {
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
    expect(oldSignal.aborted).toBe(false);
    old.resolve({ rows: ["revoked"], next: 1 });
    await Promise.all([first, second]);
    expect(oldSignal.aborted).toBe(false);
    expect(observer.getCurrentResult().data?.pages.flatMap((page) => page.rows)).toEqual(["current-0", "current-1"]);
  } finally {
    old.resolve({ rows: [], next: undefined });
    unsubscribe(); cache.clear();
  }
});

test("a burst of row changes refetches each affected read once and leaves unrelated reads alone", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createAuthoredLiveInvalidation(cache);
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
  const live = createAuthoredLiveInvalidation(cache);
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

test("slow retained reads commit within one latency and quiet gap while later changes share one follow-up", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createAuthoredLiveInvalidation(cache);
  const queryKey = ["notes", "slow-live"];
  cache.setQueryData(queryKey, ["initial"]);
  let revision = 0;
  const signals: AbortSignal[] = [];
  const observer = new QueryObserver(cache, {
    queryKey,
    meta: { angeeModels: ["notes.Note"] },
    queryFn: ({ signal }: { signal: AbortSignal }) => new Promise<string[]>((resolve) => {
      const value = revision;
      signals.push(signal);
      setTimeout(() => resolve([`revision-${value}`]), 1200);
    }),
  });
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    revision = 1;
    live.push({ model: "notes.Note", id: "note_1" });
    await vi.advanceTimersByTimeAsync(300);
    expect(signals).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(100);
    revision = 2;
    live.push({ model: "notes.Note", id: "note_2" });
    await vi.advanceTimersByTimeAsync(400);
    revision = 3;
    live.push({ model: "notes.Note", id: "note_3" });
    await vi.advanceTimersByTimeAsync(700);
    // 1200 ms read latency exceeds the 400 ms event gap. Its first snapshot
    // still commits by 1200 ms + the initial 300 ms quiet gap.
    expect(observer.getCurrentResult().data).toEqual(["revision-1"]);
    expect(signals[0]?.aborted).toBe(false);
    expect(signals).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(200);
    revision = 4;
    live.push({ model: "notes.Note", id: "note_4" });
    await vi.advanceTimersByTimeAsync(1000);
    expect(observer.getCurrentResult().data).toEqual(["revision-3"]);
    expect(signals).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(1200);
    expect(signals.every((signal) => !signal.aborted)).toBe(true);
    expect(signals).toHaveLength(3);
    expect(observer.getCurrentResult().fetchStatus).toBe("idle");
    expect(observer.getCurrentResult().data).toEqual(["revision-4"]);
  } finally {
    live.clear();
    unsubscribe(); cache.clear();
  }
});

test("a live change lets a matching data-less fetch settle before one follow-up", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createAuthoredLiveInvalidation(cache);
  const old = pending<string[]>();
  let signal!: AbortSignal;
  let reads = 0;
  const observer = new QueryObserver(cache, {
    queryKey: ["notes", "empty-live"], meta: { angeeModels: ["notes.Note"] },
    queryFn: ({ signal: current }: { signal: AbortSignal }) => {
      reads++;
      if (reads === 1) { signal = current; return old.promise; }
      return Promise.resolve(["fresh"]);
    },
  });
  const unsubscribe = observer.subscribe(() => undefined);
  try {
    live.push({ model: "notes.Note", id: "note_1" });
    expect(signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(1);
    old.resolve(["stale"]);
    await vi.advanceTimersByTimeAsync(0);
    expect(signal.aborted).toBe(false);
    expect(reads).toBe(2);
    expect(observer.getCurrentResult().data).toEqual(["fresh"]);
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(2);
  } finally {
    old.resolve([]);
    live.clear(); unsubscribe(); cache.clear();
  }
});

test("17 live changes over several seconds keep one data-less read alive and share one follow-up", async () => {
  vi.useFakeTimers();
  const cache = client();
  const live = createAuthoredLiveInvalidation(cache);
  const first = pending<string>();
  const signals: AbortSignal[] = [];
  let reads = 0;
  const observer = new QueryObserver(cache, {
    queryKey: ["bill-timeline"], meta: { angeeModels: ["accounting.Invoice"] },
    queryFn: ({ signal }: { signal: AbortSignal }) => {
      signals.push(signal);
      return ++reads === 1 ? first.promise : Promise.resolve("latest timeline");
    },
  });
  const stop = observer.subscribe(() => undefined);
  try {
    for (let index = 0; index < 17; index++) {
      live.push({ model: "accounting.Invoice", id: "bill_1", action: "update" });
      await vi.advanceTimersByTimeAsync(400);
      expect(reads).toBe(1);
      expect(signals[0]?.aborted).toBe(false);
    }
    first.resolve("earlier timeline");
    await vi.advanceTimersByTimeAsync(0);
    expect(reads).toBe(2);
    expect(signals.every((signal) => !signal.aborted)).toBe(true);
    expect(observer.getCurrentResult().data).toBe("latest timeline");
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(2);
  } finally {
    first.resolve("cleanup");
    live.clear(); stop(); cache.clear();
  }
});

test("an awaited command settles after one live follow-up while later changes continue", async () => {
  vi.useFakeTimers();
  const cache = client();
  const reads = [pending<string>(), pending<string>(), pending<string>()];
  const key = ["slow-live-command"];
  cache.setQueryData(key, "initial");
  let attempts = 0;
  const slow = new QueryObserver(cache, {
    queryKey: key, meta: { angeeModels: ["notes.Note"] },
    queryFn: () => reads[attempts++]!.promise,
  });
  const commandRead = new QueryObserver(cache, {
    queryKey: ["command-result"], meta: { angeeModels: ["notes.Tag"] },
    queryFn: async () => "current",
  });
  const stops = [slow.subscribe(() => undefined), commandRead.subscribe(() => undefined)];
  const live = createAuthoredLiveInvalidation(cache);
  try {
    void slow.refetch();
    expect(attempts).toBe(1);
    live.push({ model: "notes.Note", id: "note_1" });
    let settled = false;
    const command = live.reconcile({ changes: [{ model: "notes.Tag", id: "tag_1" }] })
      .then(() => { settled = true; });
    await vi.advanceTimersByTimeAsync(300);
    expect(settled).toBe(false);
    reads[0]!.resolve("first");
    await vi.advanceTimersByTimeAsync(0);
    expect(attempts).toBe(2);
    live.push({ model: "notes.Note", id: "note_2" });
    await vi.advanceTimersByTimeAsync(300);
    reads[1]!.resolve("follow-up");
    await vi.advanceTimersByTimeAsync(0);
    expect(attempts).toBe(3);
    expect(settled).toBe(true);
    reads[2]!.resolve("later");
    await command;
  } finally {
    reads.forEach((read) => read.resolve("cleanup"));
    live.clear(); stops.forEach((stop) => stop()); cache.clear();
  }
});

test("a slow in-flight read commits before one local-command follow-up supersedes it", async () => {
  const cache = client();
  const old = pending<string>();
  const fresh = pending<string>();
  const key = ["command-settle"];
  cache.setQueryData(key, "initial");
  let signal!: AbortSignal;
  let attempts = 0;
  const seen: string[] = [];
  const observer = new QueryObserver(cache, {
    queryKey: key, meta: { angeeModels: ["notes.Note"] },
    queryFn: ({ signal: current }: { signal: AbortSignal }) => {
      attempts++;
      if (attempts === 1) { signal = current; return old.promise; }
      return fresh.promise;
    },
  });
  const stop = observer.subscribe((result) => { if (result.data) seen.push(result.data); });
  const live = createAuthoredLiveInvalidation(cache);
  try {
    void observer.refetch();
    const command = live.batch(async (scope) => {
      await scope!.reconcile({ changes: [{ model: "notes.Note", id: "note_1" }] });
    });
    expect(signal.aborted).toBe(false);
    expect(attempts).toBe(1);
    old.resolve("obsolete");
    await vi.waitFor(() => expect(attempts).toBe(2));
    expect(seen).toContain("obsolete");
    expect(observer.getCurrentResult().data).toBe("obsolete");
    expect(signal.aborted).toBe(false);
    fresh.resolve("fresh");
    await command;
    expect(attempts).toBe(2);
    expect(observer.getCurrentResult().data).toBe("fresh");
  } finally {
    old.resolve("cleanup"); fresh.resolve("cleanup");
    live.clear(); stop(); cache.clear();
  }
});

test("a full live refresh stays full when targeted changes join across flushes", async () => {
  vi.useFakeTimers();
  const cache = client();
  const old = pending<string>();
  const key = ["feed"];
  cache.setQueryData(key, "initial");
  let attempts = 0;
  let received: unknown;
  const observer = new QueryObserver(cache, {
    queryKey: key, meta: { angeeModels: ["notes.Note"] },
    queryFn: ({ meta }) => {
      attempts++;
      if (attempts === 1) return old.promise;
      received = meta?.angeeChanges;
      return Promise.resolve("fresh");
    },
  });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache,
    (query, change) => query.queryKey[0] === "feed" && change.model === "notes.Tag");
  try {
    void observer.refetch();
    live.push({ model: "notes.Note", id: "note_1", action: "update" });
    await vi.advanceTimersByTimeAsync(300);
    live.push({ model: "notes.Tag", id: "tag_1", action: "update" });
    await vi.advanceTimersByTimeAsync(300);
    old.resolve("old");
    await vi.advanceTimersByTimeAsync(0);
    expect(received).toEqual([]);
    expect(attempts).toBe(2);
    expect(observer.getCurrentResult().data).toBe("fresh");
  } finally {
    old.resolve("cleanup");
    live.clear(); stop(); cache.clear();
  }
});

test("ready reconciles a read fetched before the subscription request", async () => {
  vi.useFakeTimers();
  const cache = client();
  let reads = 0;
  const observer = new QueryObserver(cache, {
    queryKey: ["ready", "retained"], meta: { angeeModels: ["notes.Note"] },
    queryFn: async () => ++reads,
  });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    await vi.advanceTimersByTimeAsync(0);
    expect(reads).toBe(1);
    const started = live.start("notes.Note");
    live.ready(started);
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(2);
  } finally { live.clear(); stop(); cache.clear(); }
});

test("a post-ready refetch still pending at flush needs no recovery fetch", async () => {
  vi.useFakeTimers();
  const cache = client();
  let reads = 0;
  const later = pending<number>();
  const observer = new QueryObserver(cache, {
    queryKey: ["ready", "pending-refetch"], meta: { angeeModels: ["notes.Note"] },
    queryFn: () => ++reads === 1 ? Promise.resolve(1) : later.promise,
  });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    await vi.advanceTimersByTimeAsync(0);
    expect(observer.getCurrentResult().data).toBe(1);
    const started = live.start("notes.Note");
    live.ready(started);
    const refetch = observer.refetch();
    expect(reads).toBe(2);
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(2);
    later.resolve(2);
    await refetch;
    await vi.advanceTimersByTimeAsync(0);
    expect(reads).toBe(2);
  } finally { later.resolve(2); live.clear(); stop(); cache.clear(); }
});

test("a retained read refetched after ready needs no second recovery fetch", async () => {
  vi.useFakeTimers();
  const cache = client();
  let reads = 0;
  const observer = new QueryObserver(cache, {
    queryKey: ["ready", "refetched"], meta: { angeeModels: ["notes.Note"] },
    queryFn: async () => ++reads,
  });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    await vi.advanceTimersByTimeAsync(0);
    const started = live.start("notes.Note");
    live.ready(started);
    await observer.refetch();
    expect(reads).toBe(2);
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(2);
  } finally { live.clear(); stop(); cache.clear(); }
});

test("a full authored row-change refetch before ready covers the retained read", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["ready", "authored-row-change"];
  cache.setQueryData(key, 1);
  let reads = 1;
  const observer = new QueryObserver(cache, {
    queryKey: key, meta: { angeeModels: ["notes.Note"] }, queryFn: async () => ++reads,
  });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    const started = live.start("notes.Note");
    void live.reconcile({ changes: [{ model: "notes.Note", id: "one", action: "update" }] });
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(2);
    live.ready(started);
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(2);
  } finally { live.clear(); stop(); cache.clear(); }
});

test("an accepted response after ready still needs an authoritative recovery fetch", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["ready", "accepted"];
  let reads = 0;
  const observer = new QueryObserver(cache, {
    queryKey: key, meta: { angeeModels: ["notes.Note"] },
    queryFn: async () => ({ title: `fetched ${++reads}` }),
  });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    await vi.advanceTimersByTimeAsync(0);
    expect(reads).toBe(1);
    const started = live.start("notes.Note");
    live.ready(started);
    await acceptQueryData(cache, key, { title: "accepted" }, { complete: true });
    await vi.advanceTimersByTimeAsync(300);
    expect(reads).toBe(2);
    expect(observer.getCurrentResult().data).toEqual({ title: "fetched 2" });
  } finally { live.clear(); stop(); cache.clear(); }
});

test("ready merged with a row change fully rereads a held keyset feed", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["ready-feed", "gap-change"];
  const row = { id: "message-1" };
  const window = vi.fn(async () => ({
    rows: [row, { id: "message-2" }], count: 2, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  }));
  const revalidate = vi.fn(async () => ({ rows: [row], absent_ids: [] }));
  cache.setQueryData(key, {
    pages: [{ rows: [row], through: null, hasOlder: false, count: 1 }],
    pageParams: [{ start: true }],
  });
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 2, revalidateModels: ["messaging.Message"], window, revalidate,
  });
  const observer = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    const started = live.start("messaging.Message");
    live.push({ model: "messaging.Message", id: row.id, action: "update" });
    live.ready(started);
    await vi.advanceTimersByTimeAsync(300);
    expect(revalidate).not.toHaveBeenCalled();
    expect(window).toHaveBeenCalledTimes(1);
    expect(observer.getCurrentResult().data?.pages[0]?.rows.map(({ id }) => id))
      .toEqual(["message-1", "message-2"]);
  } finally { live.clear(); stop(); cache.clear(); }
});

test("ready leaves a held keyset feed to its row change revalidation", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["ready-feed", "same-window"];
  const row = { id: "message-1" };
  const windowResult: KeysetFeedWindow<typeof row> = {
    rows: [row], count: 1, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  };
  const first = pending<KeysetFeedWindow<typeof row>>();
  const seen: unknown[] = [];
  let windows = 0;
  let signal!: AbortSignal;
  const revalidate = vi.fn(async () => ({ rows: [row], absent_ids: [] }));
  cache.setQueryData(key, {
    pages: [{ rows: [row], through: null, hasOlder: false, count: 1 }],
    pageParams: [{ start: true }],
  });
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 2, revalidateModels: ["messaging.Message"], revalidate,
    window(_before, _through, _limit, context) {
      windows++;
      seen.push(context.meta?.angeeChanges);
      if (windows === 1) { signal = context.signal; return first.promise; }
      return Promise.resolve(windowResult);
    },
  });
  const observer = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    const started = live.start("messaging.Message");
    void observer.refetch();
    live.push({ model: "messaging.Message", id: row.id, action: "update" });
    live.ready(started);
    await vi.advanceTimersByTimeAsync(300);
    expect(windows).toBe(1);
    expect(signal.aborted).toBe(false);
    first.resolve(windowResult);
    await vi.advanceTimersByTimeAsync(0);
    expect(windows).toBe(1);
    expect(seen).toEqual([undefined]);
    expect(revalidate).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(300);
    expect(windows).toBe(1);
  } finally {
    first.resolve(windowResult);
    live.clear(); stop(); cache.clear();
  }
});

test("ready during targeted keyset revalidation waits, then fully rereads once", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["ready-feed", "later-window"];
  const row = { id: "message-1" };
  const windowResult: KeysetFeedWindow<typeof row> = {
    rows: [row], count: 1, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  };
  const targeted = pending<KeysetFeedRevalidation<typeof row>>();
  const seen: unknown[] = [];
  let signal!: AbortSignal;
  const window = vi.fn(async (_before: string | null, _through: string | null, _limit: number,
    context: { meta?: { angeeChanges?: unknown } }) => {
    seen.push(context.meta?.angeeChanges);
    return windowResult;
  });
  const revalidate = vi.fn((_ids: string[], context: { signal: AbortSignal }) => {
    signal = context.signal;
    return targeted.promise;
  });
  cache.setQueryData(key, {
    pages: [{ rows: [row], through: null, hasOlder: false, count: 1 }],
    pageParams: [{ start: true }],
  });
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 2, revalidateModels: ["messaging.Message"], window, revalidate,
  });
  const observer = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    const started = live.start("messaging.Message");
    live.push({ model: "messaging.Message", id: row.id, action: "update" });
    await vi.advanceTimersByTimeAsync(300);
    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(window).not.toHaveBeenCalled();
    live.ready(started);
    await vi.advanceTimersByTimeAsync(300);
    expect(signal.aborted).toBe(false);
    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(window).not.toHaveBeenCalled();
    targeted.resolve({ rows: [row], absent_ids: [] });
    await vi.advanceTimersByTimeAsync(0);
    expect(window).toHaveBeenCalledTimes(1);
    expect(seen).toEqual([[]]);
    await vi.advanceTimersByTimeAsync(300);
    expect(window).toHaveBeenCalledTimes(1);
  } finally {
    targeted.resolve({ rows: [row], absent_ids: [] });
    live.clear(); stop(); cache.clear();
  }
});

test("a completed targeted keyset revalidation does not cover a retained feed at ready", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["ready-feed", "completed-targeted"];
  const row = { id: "message-1" };
  const window = vi.fn(async () => ({
    rows: [row], count: 1, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  }));
  const revalidate = vi.fn(async () => ({ rows: [row], absent_ids: [] }));
  cache.setQueryData(key, {
    pages: [{ rows: [row], through: null, hasOlder: false, count: 1 }],
    pageParams: [{ start: true }],
  });
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 2, revalidateModels: ["messaging.Message"], window, revalidate,
  });
  const observer = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    const started = live.start("messaging.Message");
    live.push({ model: "messaging.Message", id: row.id, action: "update" });
    await vi.advanceTimersByTimeAsync(300);
    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(window).not.toHaveBeenCalled();
    live.ready(started);
    await vi.advanceTimersByTimeAsync(300);
    expect(window).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(300);
    expect(window).toHaveBeenCalledTimes(1);
  } finally { live.clear(); stop(); cache.clear(); }
});

test("a throwing delivery still records a completed row-only feed for ready recovery", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["ready-feed", "throwing-delivery"];
  const row = { id: "message-1" };
  const targeted = pending<KeysetFeedRevalidation<typeof row>>();
  const window = vi.fn(async () => ({
    rows: [row], count: 1, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  }));
  const revalidate = vi.fn(() => targeted.promise);
  const deliver = vi.fn(() => { throw new Error("consumer delivery failed"); });
  cache.setQueryData(key, {
    pages: [{ rows: [row], through: null, hasOlder: false, count: 1 }],
    pageParams: [{ start: true }],
  });
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 2, revalidateModels: ["messaging.Message"], window, revalidate,
  });
  const feed = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stop = feed.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    const started = live.start("messaging.Message");
    live.push({ model: "messaging.Message", id: row.id, action: "update" }, deliver);
    await vi.advanceTimersByTimeAsync(300);
    expect(deliver).toHaveBeenCalledTimes(1);
    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(window).not.toHaveBeenCalled();
    targeted.resolve({ rows: [row], absent_ids: [] });
    await vi.advanceTimersByTimeAsync(0);
    expect(feed.getCurrentResult().isFetching).toBe(false);
    live.ready(started);
    await vi.advanceTimersByTimeAsync(300);
    expect(window).toHaveBeenCalledTimes(1);
  } finally {
    targeted.resolve({ rows: [row], absent_ids: [] });
    live.clear(); stop(); cache.clear();
  }
});

test("ready retains a completed row-only feed but skips a completed full read while another read is pending", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["ready-feed", "batch-gap"];
  const row = { id: "message-1" };
  const slow = pending<number>();
  const window = vi.fn(async () => ({
    rows: [row], count: 1, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  }));
  const revalidate = vi.fn(async () => ({ rows: [row], absent_ids: [] }));
  cache.setQueryData(key, {
    pages: [{ rows: [row], through: null, hasOlder: false, count: 1 }],
    pageParams: [{ start: true }],
  });
  cache.setQueryData(["ready-feed", "slow"], 0);
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 2, revalidateModels: ["messaging.Message"], window, revalidate,
  });
  const feed = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stopFeed = feed.subscribe(() => undefined);
  const slowRead = vi.fn(() => slow.promise);
  const other = new QueryObserver(cache, {
    queryKey: ["ready-feed", "slow"], meta: { angeeModels: ["messaging.Message"] }, queryFn: slowRead,
  });
  const stopOther = other.subscribe(() => undefined);
  const fastRead = vi.fn(async () => 1);
  const fast = new QueryObserver(cache, {
    queryKey: ["ready-feed", "fast"], meta: { angeeModels: ["messaging.Message"] }, queryFn: fastRead,
  });
  const stopFast = fast.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    await vi.advanceTimersByTimeAsync(0);
    expect(fastRead).toHaveBeenCalledTimes(1);
    const started = live.start("messaging.Message");
    live.push({ model: "messaging.Message", id: row.id, action: "update" });
    await vi.advanceTimersByTimeAsync(300);
    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(window).not.toHaveBeenCalled();
    expect(slowRead).toHaveBeenCalledTimes(1);
    expect(fastRead).toHaveBeenCalledTimes(2);
    expect(feed.getCurrentResult().isFetching).toBe(false);
    live.ready(started);
    await vi.advanceTimersByTimeAsync(300);
    expect(window).toHaveBeenCalledTimes(1);
    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(slowRead).toHaveBeenCalledTimes(1);
    expect(other.getCurrentResult().isFetching).toBe(true);
    expect(fastRead).toHaveBeenCalledTimes(2);
    slow.resolve(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(fastRead).toHaveBeenCalledTimes(2);
    expect(window).toHaveBeenCalledTimes(1);
  } finally {
    slow.resolve(1);
    live.clear(); stopFast(); stopOther(); stopFeed(); cache.clear();
  }
});

test("coalesced live updates revalidate only retained keyset rows", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["live-feed", "retained"];
  const rows = ["message-1", "message-2", "message-3"].map((id) => ({ id, body: "old" }));
  const windowResult: KeysetFeedWindow<(typeof rows)[number]> = {
    rows, count: rows.length, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  };
  const window = vi.fn(async () => windowResult);
  const revalidate = vi.fn(async (ids: string[]) => ({
    rows: rows.filter((row) => ids.includes(row.id)).map((row) => ({ ...row, body: "fresh" })),
    absent_ids: [] as string[],
  }));
  cache.setQueryData(key, {
    pages: [{ rows, through: null, hasOlder: false, count: rows.length }],
    pageParams: [{ start: true }],
  });
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 3, revalidateModels: ["messaging.Message"], window, revalidate,
  });
  const observer = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    live.push({ model: "messaging.Message", id: "message-1", action: "update" });
    live.push({ model: "messaging.Message", id: "message-3", action: "update" });
    live.push({ model: "messaging.Message", id: "message-1", action: "update" });
    await vi.advanceTimersByTimeAsync(300);
    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(revalidate.mock.calls[0]?.[0]).toEqual(["message-1", "message-3"]);
    expect(window).not.toHaveBeenCalled();
    expect(observer.getCurrentResult().data?.pages[0]?.rows.map((row) => row.body))
      .toEqual(["fresh", "old", "fresh"]);
  } finally {
    live.clear(); stop(); cache.clear();
  }
});

test("a data-less keyset load revalidates its newly held row after a live update", async () => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["live-feed", "first-load"];
  const row = { id: "message-1", body: "old" };
  const initial: KeysetFeedWindow<typeof row> = {
    rows: [row], count: 1, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  };
  const first = pending<KeysetFeedWindow<typeof row>>();
  const window = vi.fn(() => first.promise);
  const revalidate = vi.fn(async (ids: string[]) => ({
    rows: ids.map((id) => ({ id, body: "fresh" })), absent_ids: [] as string[],
  }));
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 2, revalidateModels: ["messaging.Message"], window, revalidate,
  });
  const observer = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    expect(window).toHaveBeenCalledTimes(1);
    live.push({ model: "messaging.Message", id: row.id, action: "update" });
    await vi.advanceTimersByTimeAsync(300);
    expect(window).toHaveBeenCalledTimes(1);
    expect(revalidate).not.toHaveBeenCalled();
    first.resolve(initial);
    await vi.advanceTimersByTimeAsync(0);
    expect(window).toHaveBeenCalledTimes(1);
    expect(revalidate).toHaveBeenCalledTimes(1);
    expect(revalidate.mock.calls[0]?.[0]).toEqual([row.id]);
    expect(observer.getCurrentResult().data?.pages[0]?.rows).toEqual([{ id: row.id, body: "fresh" }]);
  } finally {
    first.resolve(initial);
    live.clear(); stop(); cache.clear();
  }
});

test.each([
  ["unheld update", "message-2", "update"],
  ["create", "message-1", "create"],
  ["delete", "message-1", "delete"],
] as const)("a live %s fully rereads the keyset window", async (_case, id, action) => {
  vi.useFakeTimers();
  const cache = client();
  const key = ["live-feed", action, id];
  const row = { id: "message-1" };
  const windowResult: KeysetFeedWindow<typeof row> = {
    rows: [row], count: 1, older_cursor: null, has_older: false,
    has_more_in_window: false, has_older_than_through: false,
  };
  const window = vi.fn(async () => windowResult);
  const revalidate = vi.fn(async () => ({ rows: [row], absent_ids: [] }));
  cache.setQueryData(key, {
    pages: [{ rows: [row], through: null, hasOlder: false, count: 1 }],
    pageParams: [{ start: true }],
  });
  const options = keysetFeedOptions(cache, {
    queryKey: key, pageSize: 2, revalidateModels: ["messaging.Message"], window, revalidate,
  });
  const observer = new InfiniteQueryObserver(cache, { ...options, meta: { angeeModels: ["messaging.Message"] } });
  const stop = observer.subscribe(() => undefined);
  const live = createAuthoredLiveInvalidation(cache);
  try {
    live.push({ model: "messaging.Message", id, action });
    await vi.advanceTimersByTimeAsync(300);
    expect(window).toHaveBeenCalledTimes(1);
    expect(revalidate).not.toHaveBeenCalled();
  } finally {
    live.clear(); stop(); cache.clear();
  }
});


test("complete acceptance resolves before an older exact read and restores its stale settle", async () => {
  const cache = client();
  const obsolete = pending<{ title: string }>();
  const key = ["detail", { fields: ["title"] }];
  let signal!: AbortSignal;
  const read = cache.fetchQuery({ queryKey: key, queryFn: ({ signal: current }) => {
    signal = current;
    return obsolete.promise;
  } });
  const acceptance = acceptQueryData(cache, key, { title: "accepted" });
  expect(signal.aborted).toBe(false);
  const evidence = await acceptance;
  expect(cache.getQueryData(key)).toEqual({ title: "accepted" });
  obsolete.resolve({ title: "old" });
  await read;
  await Promise.resolve();
  expect(signal.aborted).toBe(false);
  expect(cache.getQueryData(key)).toEqual({ title: "accepted" });
  expect(evidence?.marker.dataUpdateCount).toBe(evidence?.query.state.dataUpdateCount);
  cache.clear();
});

test("partial and no-op acceptance remain distinguishable", async () => {
  const cache = client();
  const key = ["detail", { fields: ["title"] }];
  await acceptQueryData(cache, key, { title: "accepted" });
  const query = cache.getQueryCache().find({ queryKey: key, exact: true });
  const count = query?.state.dataUpdateCount;
  await acceptQueryData(cache, key, (current) => current);
  expect(query?.state.dataUpdateCount).toBe(count);
  await acceptQueryData(cache, key, { title: "partial" }, { complete: false });
  expect(query?.state.isInvalidated).toBe(true);
  expect(query?.meta?.angeeAcceptedResponse).toMatchObject({ complete: false });
  cache.clear();
});

test("partial acceptance stays stale after an older read settles until its exact follow-up", async () => {
  const cache = client();
  const key = ["partial-detail"];
  const obsolete = pending<{ title: string }>();
  const authoritative = pending<{ title: string }>();
  let reads = 0;
  let signal!: AbortSignal;
  cache.setQueryData(key, { title: "before" });
  const observer = new QueryObserver(cache, { queryKey: key,
    meta: { angeeModels: ["notes.Note"] },
    queryFn: ({ signal: current }) => {
      if (++reads === 1) { signal = current; return obsolete.promise; }
      return authoritative.promise;
    },
  });
  const stop = observer.subscribe(() => undefined);
  try {
    const oldRead = observer.refetch();
    await acceptQueryData(cache, key, { title: "partial" }, { complete: false });
    expect(cache.getQueryData(key)).toEqual({ title: "partial" });
    expect(observer.getCurrentQuery().state.isInvalidated).toBe(true);
    const revalidated = invalidateAuthoredQueries(cache, ["notes.Note"]);
    obsolete.resolve({ title: "old" });
    await oldRead;
    await Promise.resolve();
    expect(signal.aborted).toBe(false);
    expect(reads).toBe(2);
    expect(cache.getQueryData(key)).toEqual({ title: "partial" });
    expect(observer.getCurrentQuery().state.isInvalidated).toBe(true);
    authoritative.resolve({ title: "fresh" });
    await revalidated;
    expect(cache.getQueryData(key)).toEqual({ title: "fresh" });
    expect(observer.getCurrentQuery().state.isInvalidated).toBe(false);
    expect(reads).toBe(2);
  } finally {
    obsolete.resolve({ title: "cleanup" });
    authoritative.resolve({ title: "cleanup" });
    stop(); cache.clear();
  }
});

test("an explicit batch flushes successful item effects once after a later item fails", async () => {
  const cache = client();
  let reads = 0;
  const observer = new QueryObserver(cache, {
    queryKey: ["batch"], queryFn: async () => ++reads,
    meta: { angeeModels: ["notes.Note"] },
  });
  const stop = observer.subscribe(() => undefined);
  await observer.refetch();
  const before = reads;
  const live = createAuthoredLiveInvalidation(cache);
  const failure = new Error("second item denied");
  try {
    await expect(live.batch(async (scope) => {
      await scope!.reconcile({ changes: [{ model: "notes.Note", id: "first" }] });
      expect(reads).toBe(before);
      throw failure;
    })).rejects.toBe(failure);
    expect(reads).toBe(before + 1);
  } finally { live.clear(); stop(); cache.clear(); }
});

test("authentication suspension settles buffered reconciliation without replaying it", async () => {
  vi.useFakeTimers();
  const cache = client();
  const invalidate = vi.spyOn(cache, "invalidateQueries");
  const live = createAuthoredLiveInvalidation(cache);
  const generation = live.generation;
  const settled = live.reconcile({ changes: [{ model: "notes.Note" }] });
  live.clear();
  await settled;
  await vi.advanceTimersByTimeAsync(300);
  expect(live.generation).toBe(generation + 1);
  expect(invalidate.mock.calls.filter(([filters]) => filters?.refetchType === "active")).toHaveLength(0);
  cache.clear();
});


test("held command transport cannot postpone a continuous live burst past its max wait", async () => {
  vi.useFakeTimers();
  const cache = client();
  const heldTransport = pending<void>();
  let reads = 0;
  cache.setQueryData(["live-during-command"], 0);
  const observer = new QueryObserver(cache, { queryKey: ["live-during-command"], queryFn: async () => ++reads,
    meta: { angeeModels: ["notes.Note"] } });
  const stop = observer.subscribe(() => undefined);
  const scheduler = createAuthoredLiveInvalidation(cache);
  let settled = false;
  const command = scheduler.batch(async (scope) => {
    await heldTransport.promise;
    await scope!.reconcile({ changes: [{ model: "notes.Note", id: "local" }] });
  }).then(() => { settled = true; });
  try {
    for (let index = 0; index < 10; index++) {
      scheduler.push({ model: "notes.Note", id: `remote-${index}` });
      await vi.advanceTimersByTimeAsync(200);
    }
    expect(reads).toBe(1);
    expect(settled).toBe(false);
    heldTransport.resolve();
    await command;
    expect(reads).toBe(2);
  } finally { scheduler.clear(); stop(); cache.clear(); }
});
