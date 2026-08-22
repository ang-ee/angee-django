// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { invalidateAuthoredQueries } from "../query-invalidation";
import type { TypedDocumentNode } from "../typed-document";
import { ActiveDataProviderNameProvider } from "./data-provider-context";
import {
  useAuthoredQueryBatch,
  type AuthoredQueryBatchScope,
} from "./authored-hooks";

interface ProbeData extends Record<string, unknown> {
  folders: readonly { id: string; drive: string; parent: string }[];
}

interface ProbeVariables extends Record<string, unknown> {
  drive: string;
  parent: string;
}

const DOCUMENT = {} as TypedDocumentNode<ProbeData, ProbeVariables>;

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

afterEach(() => {
  cleanup();
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
    expect([...result.current.values()].every((entry) => entry.fetching)).toBe(true);

    await act(async () => {
      beta.resolve(data("child-b", "drive-a", "folder-b"));
    });
    await waitFor(() => {
      expect(result.current.get("folder-b")?.data?.folders[0]?.id).toBe("child-b");
    });
    expect(result.current.get("folder-a")?.data).toBeUndefined();
    expect(result.current.get("folder-a")?.fetching).toBe(true);

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

    const settledSurface = result.current;
    const settledRefetch = result.current.get("folder-a")?.refetch;
    rerender();
    expect(result.current).toBe(settledSurface);
    expect(result.current.get("folder-a")?.refetch).toBe(settledRefetch);
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
    expect(result.current.get("shared")?.fetching).toBe(true);

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
