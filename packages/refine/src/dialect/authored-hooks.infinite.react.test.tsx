// @vitest-environment happy-dom

import * as React from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { ActiveDataProviderNameProvider } from "./data-provider-context";
import { useAuthoredInfiniteQuery } from "./authored-hooks";
import type { TypedDocumentNode } from "../typed-document";

interface ProbeRow {
  id: string;
  text: string;
}

interface ProbeData extends Record<string, unknown> {
  messages: ProbeRow[];
}

interface ProbeVariables extends Record<string, unknown> {
  scope: string;
  limit: number;
  before: string | null;
}

const PAGE_SIZE = 2;
const DOCUMENT = {} as TypedDocumentNode<ProbeData, ProbeVariables>;

const providerMock = vi.hoisted(() => ({
  calls: [] as ProbeVariables[],
  pages: new Map<string, ProbeData>(),
  subscriptions: [] as Array<Record<string, unknown>>,
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useDataProvider: () => () => ({
      custom: async (params: { meta?: unknown }) => {
        const variables = (params.meta as { gqlVariables?: ProbeVariables } | undefined)
          ?.gqlVariables;
        if (variables === undefined) {
          throw new Error("Missing GraphQL variables.");
        }
        providerMock.calls.push(variables);
        const key = variables.before ?? "";
        const page = providerMock.pages.get(key);
        if (page === undefined) {
          throw new Error(`Missing page for cursor "${key}".`);
        }
        return { data: { data: page } };
      },
    }),
    useSubscription: (props: Record<string, unknown>) => {
      providerMock.subscriptions.push(props);
    },
  };
});

beforeEach(() => {
  providerMock.calls = [];
  providerMock.subscriptions = [];
  providerMock.pages = new Map([
    [
      "",
      {
        messages: [
          { id: "1", text: "new" },
          { id: "2", text: "middle" },
        ],
      },
    ],
    [
      "2",
      {
        messages: [
          { id: "2", text: "middle fresh" },
          { id: "3", text: "old" },
        ],
      },
    ],
    [
      "3",
      {
        messages: [
          { id: "3", text: "old fresh" },
        ],
      },
    ],
  ]);
});

describe("useAuthoredInfiniteQuery", () => {
  test("accumulates, dedupes, fetches older pages, and stops at the end", async () => {
    const { result } = renderHook(
      () =>
        useAuthoredInfiniteQuery(
          DOCUMENT,
          { scope: "alpha", limit: PAGE_SIZE, before: null },
          {
            models: ["messaging.Message"],
            getRows: (data) => data.messages,
            getRowId: (row) => row.id,
            getPageParam: (rows) => {
              if (rows.length < PAGE_SIZE) return undefined;
              const oldest = rows.at(-1);
              return oldest ? { before: oldest.id } : undefined;
            },
          },
        ),
      { wrapper: Providers },
    );

    await waitFor(() => {
      expect(result.current.rows.map((row) => row.text)).toEqual([
        "new",
        "middle",
      ]);
    });
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      result.current.fetchOlder();
    });
    await waitFor(() => {
      expect(result.current.rows.map((row) => row.text)).toEqual([
        "new",
        "middle fresh",
        "old",
      ]);
    });
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      result.current.fetchOlder();
    });
    await waitFor(() => {
      expect(result.current.rows.map((row) => row.text)).toEqual([
        "new",
        "middle fresh",
        "old fresh",
      ]);
    });
    expect(result.current.hasMore).toBe(false);

    await act(async () => {
      result.current.fetchOlder();
    });
    expect(providerMock.calls).toEqual([
      { scope: "alpha", limit: PAGE_SIZE, before: null },
      { scope: "alpha", limit: PAGE_SIZE, before: "2" },
      { scope: "alpha", limit: PAGE_SIZE, before: "3" },
    ]);
    expect(providerMock.subscriptions[0]).toMatchObject({
      params: { models: ["messaging.Message"] },
      enabled: true,
    });
  });

  test("keeps rows that slide out of the refetched head page", async () => {
    const client = createQueryClient();
    const { result } = renderHook(
      () =>
        useAuthoredInfiniteQuery(
          DOCUMENT,
          { scope: "alpha", limit: PAGE_SIZE, before: null },
          infiniteOptions(),
        ),
      { wrapper: ({ children }) => <Providers client={client}>{children}</Providers> },
    );

    await waitFor(() => {
      expect(result.current.rows.map((row) => row.id)).toEqual(["1", "2"]);
    });

    await act(async () => {
      result.current.fetchOlder();
    });
    await waitFor(() => {
      expect(result.current.rows.map((row) => row.id)).toEqual(["1", "2", "3"]);
    });

    providerMock.calls = [];
    providerMock.pages = new Map([
      [
        "",
        {
          messages: [
            { id: "new-a", text: "newest a" },
            { id: "new-b", text: "newest b" },
          ],
        },
      ],
      [
        "new-b",
        {
          messages: [
            { id: "1", text: "old head tail after slide" },
            { id: "2", text: "middle after slide" },
          ],
        },
      ],
    ]);

    await act(async () => {
      await client.invalidateQueries();
    });

    await waitFor(() => {
      expect(new Set(result.current.rows.map((row) => row.id))).toEqual(
        new Set(["new-a", "new-b", "1", "2", "3"]),
      );
    });
    expect(providerMock.calls).toEqual([
      { scope: "alpha", limit: PAGE_SIZE, before: null },
      { scope: "alpha", limit: PAGE_SIZE, before: "new-b" },
    ]);
    expect(result.current.rows.find((row) => row.id === "1")?.text).toBe("old head tail after slide");
    expect(result.current.rows.find((row) => row.id === "2")?.text).toBe("middle after slide");
    expect(result.current.rows.find((row) => row.id === "3")?.text).toBe("old");
  });

  test("refetches every held infinite page on invalidation", async () => {
    const client = createQueryClient();
    const { result } = renderHook(
      () =>
        useAuthoredInfiniteQuery(
          DOCUMENT,
          { scope: "alpha", limit: PAGE_SIZE, before: null },
          infiniteOptions(),
        ),
      { wrapper: ({ children }) => <Providers client={client}>{children}</Providers> },
    );

    await waitFor(() => {
      expect(result.current.rows.map((row) => row.id)).toEqual(["1", "2"]);
    });
    await act(async () => {
      result.current.fetchOlder();
    });
    await waitFor(() => {
      expect(result.current.rows.map((row) => row.id)).toEqual(["1", "2", "3"]);
    });

    providerMock.calls = [];
    providerMock.pages = new Map([
      [
        "",
        {
          messages: [
            { id: "1", text: "new refetched" },
            { id: "2", text: "middle refetched" },
          ],
        },
      ],
      [
        "2",
        {
          messages: [
            { id: "2", text: "middle older refetched" },
            { id: "3", text: "old refetched" },
          ],
        },
      ],
    ]);

    await act(async () => {
      await client.invalidateQueries({
        predicate: (query) => query.meta?.angeeModels !== undefined,
      });
    });

    await waitFor(() => {
      expect(providerMock.calls).toEqual([
        { scope: "alpha", limit: PAGE_SIZE, before: null },
        { scope: "alpha", limit: PAGE_SIZE, before: "2" },
      ]);
    });
    expect(result.current.rows.find((row) => row.id === "1")?.text).toBe("new refetched");
    expect(result.current.rows.find((row) => row.id === "2")?.text).toBe("middle older refetched");
    expect(result.current.rows.find((row) => row.id === "3")?.text).toBe("old refetched");
  });
});

function infiniteOptions() {
  return {
    models: ["messaging.Message"],
    getRows: (data: ProbeData) => data.messages,
    getRowId: (row: ProbeRow) => row.id,
    getPageParam: (rows: readonly ProbeRow[]) => {
      if (rows.length < PAGE_SIZE) return undefined;
      const oldest = rows.at(-1);
      return oldest ? { before: oldest.id } : undefined;
    },
  };
}

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function Providers({
  children,
  client,
}: {
  children: ReactNode;
  client?: QueryClient;
}) {
  const [defaultClient] = React.useState(createQueryClient);
  return (
    <QueryClientProvider client={client ?? defaultClient}>
      <ActiveDataProviderNameProvider name="console">
        {children}
      </ActiveDataProviderNameProvider>
    </QueryClientProvider>
  );
}
