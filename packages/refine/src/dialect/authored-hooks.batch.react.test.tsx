// @vitest-environment happy-dom

import { parse } from "graphql";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { createLiveInvalidation, invalidateAuthoredQueries } from "../query-invalidation";
import type { TypedDocumentNode } from "../typed-document";
import { ActiveDataProviderNameProvider } from "./data-provider-context";
import {
  useAuthoredQueryBatch,
  type AuthoredQueryBatchScope,
} from "./authored-hooks";
import { labelBatchResults } from "./query-batch";

interface ProbeData extends Record<string, unknown> {
  folders: readonly { id: string; drive: string; parent: string }[];
}

interface ProbeVariables extends Record<string, unknown> {
  drive: string;
  parent: string;
}

const DOCUMENT = parse("query Probe($drive: String!, $parent: String!) { folders { id drive parent } }") as TypedDocumentNode<ProbeData, ProbeVariables>;

const batchMock = vi.hoisted(() => ({
  calls: [] as ProbeVariables[],
  handlers: new Map<string, () => Promise<ProbeData>>(),
  subscriptions: [] as Array<Record<string, unknown>>,
  activeSubscriptions: 0,
  subscriptionTeardowns: 0,
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  const { useEffect } = await import("react");
  return {
    ...actual,
    useDataProvider: () => () => ({
      custom: async (params: { meta?: unknown }) => {
        const variables = (params.meta as { gqlVariables?: ProbeVariables })
          .gqlVariables;
        if (!variables) throw new Error("Missing authored batch variables.");
        batchMock.calls.push(variables);
        const handler = batchMock.handlers.get(requestKey(variables));
        if (!handler) {
          throw new Error(`Missing response for ${requestKey(variables)}.`);
        }
        return { data: { data: await handler() } };
      },
    }),
    useSubscription: (props: Record<string, unknown>) => {
      batchMock.subscriptions.push(props);
      const models = (props.params as { models?: readonly string[] } | undefined)
        ?.models?.join("\0") ?? "";
      useEffect(() => {
        if (!props.enabled) return;
        batchMock.activeSubscriptions += 1;
        return () => {
          batchMock.activeSubscriptions -= 1;
          batchMock.subscriptionTeardowns += 1;
        };
      }, [models, props.channel, props.enabled]);
    },
  };
});

beforeEach(() => {
  batchMock.calls = [];
  batchMock.handlers = new Map();
  batchMock.subscriptions = [];
  batchMock.activeSubscriptions = 0;
  batchMock.subscriptionTeardowns = 0;
});

describe("useAuthoredQueryBatch", () => {
  test("starts simultaneous per-item queries and resolves them by caller key", async () => {
    const alpha = deferred<ProbeData>();
    const beta = deferred<ProbeData>();
    batchMock.handlers.set("drive-a/folder-a", () => alpha.promise);
    batchMock.handlers.set("drive-a/folder-b", () => beta.promise);
    const client = createQueryClient();
    const scopes = [
      scope("folder-a", "drive-a", "folder-a"),
      scope("folder-b", "drive-a", "folder-b"),
    ];

    const { result, rerender } = renderHook(() => useAuthoredQueryBatch(scopes), {
      wrapper: ({ children }) => <Providers client={client}>{children}</Providers>,
    });

    await waitFor(() => expect(batchMock.calls).toHaveLength(2));
    expect([...result.current.values()].every((entry) => entry.isFetching)).toBe(true);

    await act(async () => {
      beta.resolve(data("child-b", "drive-a", "folder-b"));
    });
    await waitFor(() => {
      expect(result.current.get("folder-b")?.data?.folders[0]?.id).toBe("child-b");
    });
    expect(result.current.get("folder-a")?.data).toBeUndefined();
    expect(result.current.get("folder-a")?.isFetching).toBe(true);

    await act(async () => {
      alpha.resolve(data("child-a", "drive-a", "folder-a"));
    });
    await waitFor(() => {
      expect(result.current.get("folder-a")?.data?.folders[0]?.id).toBe("child-a");
      expect(result.current.get("folder-b")?.data?.folders[0]?.id).toBe("child-b");
    });
    expect(batchMock.subscriptions[0]).toMatchObject({
      params: { models: ["storage.Folder"] },
      enabled: true,
    });

    const settledRefetch = result.current.get("folder-a")?.refetch;
    rerender();
    expect(result.current.get("folder-a")?.refetch).toBe(settledRefetch);
  });

  test("keeps the Map and its entries while nothing changed, and shares every unchanged entry when one did", async () => {
    const alpha = deferred<ProbeData>();
    const beta = deferred<ProbeData>();
    batchMock.handlers.set("drive-a/folder-a", () => alpha.promise);
    batchMock.handlers.set("drive-a/folder-b", () => beta.promise);
    batchMock.handlers.set("drive-a/folder-c", async () => data("child-c", "drive-a", "folder-c"));
    const client = createQueryClient();
    // Equal scopes declared afresh on every render, as a caller's derivation does.
    const { result, rerender } = renderHook(
      ({ parents }) => useAuthoredQueryBatch(parents.map((parent) => scope(parent, "drive-a", parent))),
      {
        initialProps: { parents: ["folder-a", "folder-b"] },
        wrapper: ({ children }) => <Providers client={client}>{children}</Providers>,
      },
    );
    await waitFor(() => expect(batchMock.calls).toHaveLength(2));
    const pending = result.current;
    rerender({ parents: ["folder-a", "folder-b"] });
    expect(result.current).toBe(pending);
    expect(result.current.get("folder-a")).toBe(pending.get("folder-a"));

    await act(async () => {
      beta.resolve(data("child-b", "drive-a", "folder-b"));
    });
    await waitFor(() => {
      expect(result.current.get("folder-b")?.data?.folders[0]?.id).toBe("child-b");
    });
    const settled = result.current;
    expect(settled).not.toBe(pending);
    expect(settled.get("folder-b")).not.toBe(pending.get("folder-b"));
    expect(settled.get("folder-a")).toBe(pending.get("folder-a"));
    rerender({ parents: ["folder-a", "folder-b"] });
    expect(result.current).toBe(settled);
    expect(result.current.get("folder-b")).toBe(settled.get("folder-b"));

    rerender({ parents: ["folder-a", "folder-b", "folder-c"] });
    const added = result.current;
    expect(added).not.toBe(settled);
    expect([...added.keys()]).toEqual(["folder-a", "folder-b", "folder-c"]);
    expect(added.get("folder-b")).toBe(settled.get("folder-b"));
    rerender({ parents: ["folder-a", "folder-b"] });
    expect(result.current).not.toBe(added);
    expect([...result.current.keys()]).toEqual(["folder-a", "folder-b"]);
  });

  test("the render that enables a read reports it fetching", () => {
    batchMock.handlers.set("drive-a/folder-a", () => new Promise<ProbeData>(() => undefined));
    const client = createQueryClient();
    const seen: (boolean | undefined)[] = [];
    const { rerender } = renderHook(({ enabled }) => {
      const entry = useAuthoredQueryBatch([scope("folder-a", "drive-a", "folder-a")], { enabled }).get("folder-a");
      seen.push(entry?.isFetching);
    }, {
      initialProps: { enabled: false },
      wrapper: ({ children }) => <Providers client={client}>{children}</Providers>,
    });
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((fetching) => fetching === false)).toBe(true);
    const enabledAt = seen.length;
    rerender({ enabled: true });
    expect(seen[enabledAt]).toBe(true);
    expect(seen.slice(enabledAt).every((fetching) => fetching === true)).toBe(true);
  });

  test("the render after a read's cache entry is removed reports the rebuilt read fetching", async () => {
    let reads = 0;
    batchMock.handlers.set("drive-a/folder-a", () => {
      reads += 1;
      return reads === 1 ? Promise.resolve(data("child-a", "drive-a", "folder-a")) : new Promise<ProbeData>(() => undefined);
    });
    const client = createQueryClient();
    const seen: { data: unknown; fetching: boolean | undefined }[] = [];
    const { rerender } = renderHook(({ tick }) => {
      const entry = useAuthoredQueryBatch([scope("folder-a", "drive-a", "folder-a")]).get("folder-a");
      seen.push({ data: entry?.data, fetching: entry?.isFetching });
      return tick;
    }, {
      initialProps: { tick: 0 },
      wrapper: ({ children }) => <Providers client={client}>{children}</Providers>,
    });
    await waitFor(() => expect(seen.at(-1)).toMatchObject({ fetching: false, data: data("child-a", "drive-a", "folder-a") }));
    act(() => { client.removeQueries(); });
    const removedAt = seen.length;
    rerender({ tick: 1 });
    expect(seen[removedAt]).toEqual({ data: undefined, fetching: true });
  });

  test("a read going stale renders its batch's owner no more", async () => {
    vi.useFakeTimers();
    try {
      batchMock.handlers.set("drive-a/folder-a", async () => data("child-a", "drive-a", "folder-a"));
      const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 1_000 } } });
      let renders = 0;
      const { result } = renderHook(() => {
        renders += 1;
        return useAuthoredQueryBatch([scope("folder-a", "drive-a", "folder-a")]).get("folder-a");
      }, { wrapper: ({ children }) => <Providers client={client}>{children}</Providers> });
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(result.current?.data?.folders[0]?.id).toBe("child-a");
      const query = client.getQueryCache().getAll()[0]!;
      expect(query.isStale()).toBe(false);
      const settled = renders;
      await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
      expect(query.isStale()).toBe(true);
      expect(renders).toBe(settled);
    } finally {
      vi.useRealTimers();
    }
  });

  test("a failed attempt Query retries renders its batch's owner no more", async () => {
    vi.useFakeTimers();
    try {
      let fail!: (error: Error) => void;
      let attempts = 0;
      batchMock.handlers.set("drive-a/folder-a", () => {
        attempts += 1;
        return attempts === 1
          ? new Promise<ProbeData>((_resolve, reject) => { fail = reject; })
          : Promise.resolve(data("child-a", "drive-a", "folder-a"));
      });
      const client = new QueryClient({ defaultOptions: { queries: { retry: 1, retryDelay: 1_000 } } });
      let renders = 0;
      const { result } = renderHook(() => {
        renders += 1;
        return useAuthoredQueryBatch([scope("folder-a", "drive-a", "folder-a")]).get("folder-a");
      }, { wrapper: ({ children }) => <Providers client={client}>{children}</Providers> });
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });
      expect(result.current?.isFetching).toBe(true);
      const fetching = renders;
      await act(async () => { fail(new Error("try again")); await vi.advanceTimersByTimeAsync(10); });
      expect(client.getQueryCache().getAll()[0]?.state.fetchFailureCount).toBe(1);
      expect(renders).toBe(fetching);
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
      expect(attempts).toBe(2);
      expect(result.current?.data?.folders[0]?.id).toBe("child-a");
    } finally {
      vi.useRealTimers();
    }
  });

  test("a label whose request changes reads the new request's result", async () => {
    batchMock.handlers.set("drive-a/folder-a", async () => data("child-a", "drive-a", "folder-a"));
    batchMock.handlers.set("drive-a/folder-b", async () => data("child-b", "drive-a", "folder-b"));
    const client = createQueryClient();
    const { result, rerender } = renderHook(
      ({ parent }) => useAuthoredQueryBatch([scope("label", "drive-a", parent), scope("other", "drive-a", "folder-b")]),
      { initialProps: { parent: "folder-a" }, wrapper: ({ children }) => <Providers client={client}>{children}</Providers> },
    );
    await waitFor(() => {
      expect(result.current.get("label")?.data?.folders[0]?.id).toBe("child-a");
      expect(result.current.get("other")?.data?.folders[0]?.id).toBe("child-b");
    });
    rerender({ parent: "folder-b" });
    expect(result.current.get("label")?.data?.folders[0]?.id).toBe("child-b");
    // Two labels of one request read one entry.
    expect(result.current.get("label")).toBe(result.current.get("other"));
  });

  test("scopes reordered keep each label's result", async () => {
    batchMock.handlers.set("drive-a/folder-a", async () => data("child-a", "drive-a", "folder-a"));
    batchMock.handlers.set("drive-a/folder-b", async () => data("child-b", "drive-a", "folder-b"));
    const client = createQueryClient();
    const { result, rerender } = renderHook(
      ({ parents }) => useAuthoredQueryBatch(parents.map((parent) => scope(parent, "drive-a", parent))),
      { initialProps: { parents: ["folder-a", "folder-b"] }, wrapper: ({ children }) => <Providers client={client}>{children}</Providers> },
    );
    await waitFor(() => expect([...result.current.values()].every((entry) => entry.data !== undefined)).toBe(true));
    rerender({ parents: ["folder-b", "folder-a"] });
    expect([...result.current.keys()]).toEqual(["folder-b", "folder-a"]);
    expect(result.current.get("folder-a")?.data?.folders[0]?.id).toBe("child-a");
    expect(result.current.get("folder-b")?.data?.folders[0]?.id).toBe("child-b");
  });

  test("a refetch answering equal data moves the entry's settle time and gives a new entry", async () => {
    batchMock.handlers.set("drive-a/folder-a", async () => data("child-a", "drive-a", "folder-a"));
    const client = createQueryClient();
    const { result } = renderHook(
      () => useAuthoredQueryBatch([scope("folder-a", "drive-a", "folder-a")]),
      { wrapper: ({ children }) => <Providers client={client}>{children}</Providers> },
    );
    await waitFor(() => expect(result.current.get("folder-a")?.isFetching).toBe(false));
    const settled = result.current.get("folder-a")!;
    await new Promise((resolve) => { setTimeout(resolve, 5); });
    await act(async () => { await settled.refetch(); });
    await waitFor(() => expect(result.current.get("folder-a")?.dataUpdatedAt).toBeGreaterThan(settled.dataUpdatedAt));
    expect(result.current.get("folder-a")).not.toBe(settled);
    // Query shares equal data, so only its settle time moved.
    expect(result.current.get("folder-a")?.data).toBe(settled.data);
  });

  test("a label without a result fails, naming the hook, rather than read another request's", () => {
    expect(() => labelBatchResults("useAuthoredQueryBatch", [["folder-a", 0], ["folder-b", 1]], ["only one"]))
      .toThrow('useAuthoredQueryBatch: "folder-b" has no result among the batch\'s 1.');
  });

  test("keys same-parent reads by drive and never exposes the previous key as placeholder data", async () => {
    batchMock.handlers.set("drive-a/shared", async () =>
      data("drive-a-child", "drive-a", "shared"),
    );
    const driveB = deferred<ProbeData>();
    batchMock.handlers.set("drive-b/shared", () => driveB.promise);
    const client = createQueryClient();
    const { result, rerender } = renderHook(
      ({ drive }) =>
        useAuthoredQueryBatch([scope("shared", drive, "shared")]),
      {
        initialProps: { drive: "drive-a" },
        wrapper: ({ children }) => <Providers client={client}>{children}</Providers>,
      },
    );

    await waitFor(() => {
      expect(result.current.get("shared")?.data?.folders[0]?.id).toBe(
        "drive-a-child",
      );
    });
    rerender({ drive: "drive-b" });
    await waitFor(() => {
      expect(batchMock.calls).toContainEqual({ drive: "drive-b", parent: "shared" });
    });
    expect(result.current.get("shared")?.data).toBeUndefined();
    expect(result.current.get("shared")?.isFetching).toBe(true);

    await act(async () => {
      driveB.resolve(data("drive-b-child", "drive-b", "shared"));
    });
    await waitFor(() => {
      expect(result.current.get("shared")?.data?.folders[0]?.id).toBe(
        "drive-b-child",
      );
    });
  });

  test("isolates a failed entry beside a successful sibling and retries it", async () => {
    let attempt = 0;
    batchMock.handlers.set("drive-a/folder-a", async () => {
      attempt += 1;
      if (attempt === 1) throw new Error("children denied");
      return data("child-a", "drive-a", "folder-a");
    });
    batchMock.handlers.set("drive-a/folder-b", async () =>
      data("child-b", "drive-a", "folder-b"),
    );
    const client = createQueryClient();
    const { result } = renderHook(
      () =>
        useAuthoredQueryBatch([
          scope("folder-a", "drive-a", "folder-a"),
          scope("folder-b", "drive-a", "folder-b"),
        ]),
      { wrapper: ({ children }) => <Providers client={client}>{children}</Providers> },
    );

    await waitFor(() => {
      expect(result.current.get("folder-a")?.error?.message).toBe("children denied");
      expect(result.current.get("folder-b")?.data?.folders[0]?.id).toBe("child-b");
    });
    act(() => {
      result.current.get("folder-a")?.refetch();
    });
    await waitFor(() => {
      expect(result.current.get("folder-a")?.data?.folders[0]?.id).toBe("child-a");
    });
    expect(attempt).toBe(2);
  });

  test("refreshes active entries through authored changes invalidation metadata", async () => {
    let child = "before-change";
    batchMock.handlers.set("drive-a/folder-a", async () =>
      data(child, "drive-a", "folder-a"),
    );
    const client = createQueryClient();
    const { result } = renderHook(
      () => useAuthoredQueryBatch([scope("folder-a", "drive-a", "folder-a")]),
      { wrapper: ({ children }) => <Providers client={client}>{children}</Providers> },
    );

    await waitFor(() => {
      expect(result.current.get("folder-a")?.data?.folders[0]?.id).toBe(
        "before-change",
      );
    });
    child = "after-change";
    await act(async () => {
      // `folderChanged` delivery is covered by the provider/schema tests; this
      // direct call proves batch cache metadata responds to that plumbing.
      await invalidateAuthoredQueries(client, ["storage.Folder"]);
    });
    await waitFor(() => {
      expect(result.current.get("folder-a")?.data?.folders[0]?.id).toBe(
        "after-change",
      );
    });
    expect(batchMock.calls).toHaveLength(2);
  });

  test("related-record changes invalidate only the matching batch entry", async () => {
    for (const id of ["folder-a", "folder-b"]) {
      batchMock.handlers.set(`drive-a/${id}`, async () => data(id, "drive-a", id));
    }
    const client = createQueryClient();
    renderHook(() => useAuthoredQueryBatch(["folder-a", "folder-b"].map((id) => ({
      ...scope(id, "drive-a", id),
      models: ["storage.Folder", "workflows.WorkflowRun"],
      records: [{ model: "storage.Folder", id }],
      relatedModels: ["workflows.WorkflowRun"],
    }))), { wrapper: ({ children }) => <Providers client={client}>{children}</Providers> });
    await waitFor(() => expect(batchMock.calls).toHaveLength(2));
    const live = createLiveInvalidation(client);
    await act(async () => {
      live.push({ model: "workflows.WorkflowRun", id: "run-1",
        relatedRecords: [{ model: "storage.Folder", id: "folder-a" }] });
    });
    await waitFor(() => expect(batchMock.calls).toHaveLength(3));
    expect(batchMock.calls[2]?.parent).toBe("folder-a");
    live.clear();
  });

  test("tears down live interest when the batch becomes empty", async () => {
    batchMock.handlers.set("drive-a/folder-a", async () =>
      data("child-a", "drive-a", "folder-a"),
    );
    const client = createQueryClient();
    const { rerender } = renderHook(
      ({ scopes }) => useAuthoredQueryBatch(scopes),
      {
        initialProps: {
          scopes: [scope("folder-a", "drive-a", "folder-a")],
        },
        wrapper: ({ children }) => <Providers client={client}>{children}</Providers>,
      },
    );

    await waitFor(() => expect(batchMock.activeSubscriptions).toBe(1));
    rerender({ scopes: [] });
    await waitFor(() => {
      expect(batchMock.activeSubscriptions).toBe(0);
      expect(batchMock.subscriptionTeardowns).toBe(1);
    });
    expect(batchMock.subscriptions.at(-1)).toMatchObject({ enabled: false });
  });
});

function scope(
  key: string,
  drive: string,
  parent: string,
): AuthoredQueryBatchScope<typeof DOCUMENT> {
  return {
    key,
    document: DOCUMENT,
    variables: { drive, parent },
    models: ["storage.Folder"],
  };
}

function requestKey(variables: ProbeVariables): string {
  return `${variables.drive}/${variables.parent}`;
}

function data(id: string, drive: string, parent: string): ProbeData {
  return { folders: [{ id, drive, parent }] };
}

function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function Providers({
  children,
  client,
}: {
  children: ReactNode;
  client: QueryClient;
}) {
  return (
    <QueryClientProvider client={client}>
      <ActiveDataProviderNameProvider name="console">
        {children}
      </ActiveDataProviderNameProvider>
    </QueryClientProvider>
  );
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve = (_value: T): void => undefined;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}
