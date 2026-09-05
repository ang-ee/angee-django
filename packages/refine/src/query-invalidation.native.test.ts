import { InfiniteQueryObserver, isCancelledError, QueryClient, QueryObserver } from "@tanstack/react-query";
import { expect, test } from "vitest";

import { invalidateAuthoredQueries } from "./query-invalidation";

function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

test("a model event during the initial request replaces its snapshot and discards a late response", async () => {
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
  const request = cache.fetchQuery(options).catch((error: unknown) => error);
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

test("successive events cancel a retained infinite refresh and commit the latest complete pages", async () => {
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
