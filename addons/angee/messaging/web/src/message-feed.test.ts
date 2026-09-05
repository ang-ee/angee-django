import { InfiniteQueryObserver, QueryClient } from "@tanstack/react-query";
import { describe, expect, test, vi } from "vitest";

import { messageFeedOptions, messageFeedRows } from "./message-feed";

const message = (position: number, values: { id?: string; body?: string; visible?: boolean } = {}) => ({
  id: `message-${position}`,
  feed_order_key: `v1:${String(position * 1000).padStart(12, "0")}`,
  position,
  body: "old",
  visible: true,
  ...values,
});

function fixture(gcTime = Infinity) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  let rows = Array.from({ length: 8 }, (_, index) => message(8 - index));
  let deny = false;
  let invalidPartition: "incomplete" | "duplicate" | "extra" | undefined;
  let pause: Promise<void> | undefined;
  let beforeWindow: ((before: string | null, through: string | null) => void) | undefined;
  const calls: Array<{ kind: string; ids?: string[]; limit?: number; before?: string | null; through?: string | null }> = [];
  const scoped = () => {
    if (deny) throw new Error("Unreadable root scope");
    return rows.filter((row) => row.visible).sort((left, right) => right.position - left.position);
  };
  const cursor = (position: number | undefined) => position === undefined ? null : `opaque:${position}`;
  // Decoding is exclusively in this server double. Production never parses cuts.
  const cut = (value: string | null) => value === null ? null : Number(value.slice("opaque:".length));
  const queryKey = ["angee", "authored", "message-feed", "actor-a", "thread-a"];
  const options = messageFeedOptions(client, {
    queryKey,
    pageSize: 2,
    async window(before, through, limit, context) {
      calls.push({ kind: "window", before, through, limit });
      await pause;
      context.signal.throwIfAborted();
      beforeWindow?.(before, through);
      const all = scoped();
      const upper = cut(before);
      const lower = cut(through);
      const matching = all.filter((row) => (upper === null || row.position < upper) && (lower === null || row.position >= lower));
      const messages = matching.slice(0, limit).map((row) => ({ ...row }));
      const oldest = messages.at(-1)?.position;
      return {
        messages, count: all.length, older_cursor: cursor(oldest),
        has_older: oldest !== undefined && all.some((row) => row.position < oldest),
        has_more_in_window: matching.length > limit,
        has_older_than_through: lower !== null && all.some((row) => row.position < lower),
      };
    },
    async revalidate(ids, context) {
      calls.push({ kind: "revalidate", ids });
      await pause;
      context.signal.throwIfAborted();
      const messages = scoped().filter((row) => ids.includes(row.id)).map((row) => ({ ...row }));
      const absent_ids = ids.filter((id) => !messages.some((row) => row.id === id));
      if (invalidPartition === "incomplete") absent_ids.pop();
      if (invalidPartition === "duplicate") messages.push(messages[0]!);
      if (invalidPartition === "extra") absent_ids.push("not-requested");
      return { messages, absent_ids };
    },
  });
  const observer = new InfiniteQueryObserver(client, { ...options, gcTime });
  const unsubscribe = observer.subscribe(() => {});
  const read = () => observer.getCurrentResult().data;
  return {
    client, options, observer, calls, read,
    ids: () => messageFeedRows(read()).map((row) => row.id),
    set: (next: typeof rows) => { rows = next; },
    deny: () => { deny = true; },
    invalidPartition: (value: typeof invalidPartition) => { invalidPartition = value; },
    pause: (pending: Promise<void>) => { pause = pending; },
    beforeWindow: (callback: NonNullable<typeof beforeWindow>) => { beforeWindow = callback; },
    invalidate: () => client.invalidateQueries({ queryKey, exact: true }),
    stop: () => { unsubscribe(); observer.destroy(); },
    close: () => { unsubscribe(); observer.destroy(); client.clear(); },
  };
}

describe("native message history retention", () => {
  test("moving heads preserve loaded survivors, original cuts and older continuation", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch();
      await feed.observer.fetchNextPage();
      const cuts = feed.read()!.pageParams;
      feed.set(Array.from({ length: 12 }, (_, index) => message(12 - index)));
      await feed.invalidate();
      expect(feed.read()!.pageParams).toEqual(cuts);
      expect(feed.ids()).toEqual([12, 11, 10, 9, 8, 7, 6, 5].map((id) => `message-${id}`));
      await feed.observer.fetchNextPage({ cancelRefetch: false });
      expect(feed.ids()).toEqual([12, 11, 10, 9, 8, 7, 6, 5, 4, 3].map((id) => `message-${id}`));
      expect(feed.client.getQueryCache().getAll()).toHaveLength(1);
    } finally { feed.close(); }
  });

  test("revalidates edited and moved rows, removing revoked/deleted rows without duplicate page ownership", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage();
      feed.set([
        message(0, { id: "message-8", body: "edited below loaded history" }),
        message(7, { visible: false }),
        message(50, { id: "message-5" }), message(4), message(3), message(2), message(1),
      ]);
      await feed.invalidate();
      expect(feed.ids()).toEqual(["message-5", "message-8"]);
      expect(messageFeedRows(feed.read()).at(-1)?.body).toBe("edited below loaded history");
      expect(feed.read()!.pages.flatMap((page) => page.messages.map((row) => row.id))).toEqual(["message-8", "message-5"]);
      while (feed.observer.getCurrentResult().hasNextPage) await feed.observer.fetchNextPage({ cancelRefetch: false });
      expect(feed.ids()).toEqual(["message-5", "message-4", "message-3", "message-2", "message-1", "message-8"]);
      expect(feed.read()!.pages.flatMap((page) => page.messages)).toHaveLength(6);
    } finally { feed.close(); }
  });

  test("a fresh row moving between windows uses its later observation and reconciles ownership on the next refresh", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage();
      const originals = Array.from({ length: 8 }, (_, index) => message(8 - index));
      feed.set([message(9, { body: "first observation" }), ...originals]);
      feed.beforeWindow((before, through) => {
        if (before === "opaque:7" && through === "opaque:5") {
          feed.set([message(6.5, { id: "message-9", body: "later observation" }), ...originals]);
        }
      });
      await feed.invalidate();
      expect(feed.read()!.pages.flatMap((page) => page.messages).filter((row) => row.id === "message-9")).toHaveLength(2);
      expect(feed.ids()).toEqual(["message-8", "message-7", "message-9", "message-6", "message-5"]);
      expect(messageFeedRows(feed.read()).find((row) => row.id === "message-9")?.body).toBe("later observation");
      await feed.invalidate();
      expect(feed.read()!.pages.flatMap((page) => page.messages).filter((row) => row.id === "message-9")).toHaveLength(1);
      expect(feed.ids()).toEqual(["message-8", "message-7", "message-9", "message-6", "message-5"]);
    } finally { feed.close(); }
  });

  test("an empty fixed window neither terminates cached refetch nor loses older paging", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage(); await feed.observer.fetchNextPage();
      feed.set([message(20), message(4), message(3), message(2), message(1)]);
      await feed.invalidate();
      expect(feed.read()!.pages).toHaveLength(3);
      expect(feed.read()!.pages[1]!.messages).toEqual([]);
      expect(feed.ids()).toEqual(["message-20", "message-4", "message-3"]);
      await feed.observer.fetchNextPage({ cancelRefetch: false });
      expect(feed.ids()).toEqual(["message-20", "message-4", "message-3", "message-2", "message-1"]);
    } finally { feed.close(); }
  });

  test("older continuation survives after all currently loaded messages disappear", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage();
      feed.set([message(4), message(3), message(2), message(1)]);
      await feed.invalidate();
      expect(feed.ids()).toEqual([]);
      expect(feed.observer.getCurrentResult().hasNextPage).toBe(true);
      await feed.observer.fetchNextPage({ cancelRefetch: false });
      expect(feed.ids()).toEqual(["message-4", "message-3"]);
    } finally { feed.close(); }
  });

  test.each(["incomplete", "duplicate", "extra"] as const)("%s revalidation rejects the whole refetch and preserves the native prior data", async (partition) => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage();
      const previous = feed.read();
      feed.set([message(8), message(4)]); feed.invalidPartition(partition);
      await expect(feed.observer.refetch({ throwOnError: true })).rejects.toThrow("partition");
      expect(feed.read()).toBe(previous);
      expect(feed.observer.getCurrentResult().isRefetchError).toBe(true);
    } finally { feed.close(); }
  });

  test("root denial is an error and does not turn retained rows into authoritative absence", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); const previous = feed.read(); feed.deny();
      await expect(feed.observer.refetch({ throwOnError: true })).rejects.toThrow("Unreadable root");
      expect(feed.read()).toBe(previous);
      expect(feed.observer.getCurrentResult().isRefetchError).toBe(true);
    } finally { feed.close(); }
  });

  test("bounds every request and checks each retained ID once per successful refresh", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage(); await feed.observer.fetchNextPage();
      feed.set(Array.from({ length: 14 }, (_, index) => message(14 - index))); feed.calls.length = 0;
      await feed.invalidate();
      const windows = feed.calls.filter((call) => call.kind === "window");
      const validations = feed.calls.filter((call) => call.kind === "revalidate");
      expect(windows).toHaveLength(6);
      expect(validations).toHaveLength(3);
      expect(windows.every((call) => call.limit === 2)).toBe(true);
      expect(validations.every((call) => call.ids!.length <= 2)).toBe(true);
      expect(new Set(validations.flatMap((call) => call.ids!)).size).toBe(6);
    } finally { feed.close(); }
  });

  test("older fetch with cancelRefetch false joins authoritative refresh instead of cancelling it", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage();
      let release!: () => void;
      feed.pause(new Promise<void>((resolve) => { release = resolve; }));
      feed.set([message(8), message(6), message(5), message(4), message(3)]);
      const refresh = feed.invalidate();
      const older = feed.observer.fetchNextPage({ cancelRefetch: false });
      release(); await Promise.all([refresh, older]);
      expect(feed.ids()).toEqual(["message-8", "message-6", "message-5"]);
      expect(feed.read()!.pages).toHaveLength(2);
      await feed.observer.fetchNextPage({ cancelRefetch: false });
      expect(feed.ids()).toEqual(["message-8", "message-6", "message-5", "message-4", "message-3"]);
    } finally { feed.close(); }
  });

  test("native cancellation discards late refresh data", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); const previous = feed.read();
      let release!: () => void;
      feed.pause(new Promise<void>((resolve) => { release = resolve; }));
      feed.set([message(20)]);
      const refresh = feed.invalidate();
      await feed.client.cancelQueries({ queryKey: feed.options.queryKey });
      release(); await refresh;
      expect(feed.read()).toBe(previous);
    } finally { feed.close(); }
  });

  test("native cache removal aborts a pending read and leaves no row store", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch();
      let release!: () => void;
      feed.pause(new Promise<void>((resolve) => { release = resolve; }));
      const refresh = feed.invalidate();
      feed.client.removeQueries({ queryKey: feed.options.queryKey });
      release(); await refresh;
      expect(feed.client.getQueryCache().getAll()).toHaveLength(0);
      expect(feed.client.getQueryData(feed.options.queryKey)).toBeUndefined();
    } finally { feed.close(); }
  });

  test("native observer teardown and GC remove every retained page", async () => {
    const feed = fixture(5);
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage();
      feed.stop();
      await vi.waitFor(() => expect(feed.client.getQueryCache().getAll()).toHaveLength(0));
    } finally { feed.close(); }
  });

  test("actor and scope keys do not borrow loaded history", async () => {
    const feed = fixture();
    try {
      await feed.observer.refetch(); await feed.observer.fetchNextPage();
      const otherActor = ["angee", "authored", "message-feed", "actor-b", "thread-a"];
      const otherScope = ["angee", "authored", "message-feed", "actor-a", "thread-b"];
      expect(feed.client.getQueryData(otherActor)).toBeUndefined();
      expect(feed.client.getQueryData(otherScope)).toBeUndefined();
      expect(feed.ids()).toHaveLength(4);
    } finally { feed.close(); }
  });
});
