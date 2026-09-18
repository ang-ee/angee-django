// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { QueryClient, keepPreviousData } from "@tanstack/react-query";
import { parse } from "graphql";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useAngeeAggregate } from "./hooks";
import { invalidateAuthoredQueries } from "../query-invalidation";

const DOCUMENT = parse("query NotesAggregate { notes_aggregate { count } }");
const TARGET = {
  dataProviderName: "default",
  root: "notes_aggregate",
  modelLabel: "notes.Note",
};

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
});

function fixture() {
  const custom = vi.fn(async () => ({ data: { notes_aggregate: { count: 4 } } }));
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, placeholderData: keepPreviousData },
    },
  });
  clients.push(client);
  const provider = {
    getApiUrl: () => "test://query",
    getList: vi.fn(),
    getOne: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    deleteOne: vi.fn(),
    custom,
  } as unknown as DataProvider;
  function Providers({ children }: { children: ReactNode }) {
    return (
      <Refine
        dataProvider={{ default: provider }}
        options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}
      >
        {children}
      </Refine>
    );
  }
  return { client, custom, wrapper: Providers };
}

function aggregateQueryMeta(client: QueryClient): unknown {
  return client
    .getQueryCache()
    .getAll()
    .map((query) => query.meta)
    .find((meta) => Array.isArray((meta as { angeeModels?: unknown })?.angeeModels));
}

test("a footer aggregate registers its model so a write refetches the total", async () => {
  const f = fixture();
  renderHook(() => useAngeeAggregate(TARGET, { document: DOCUMENT }), {
    wrapper: f.wrapper,
  });

  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));
  expect((aggregateQueryMeta(f.client) as { angeeModels: string[] })?.angeeModels)
    .toEqual(["notes.Note"]);

  // The delete path invalidates by model; without the registration above this
  // custom query carries no resource key and nothing can reach it.
  await act(async () => {
    await invalidateAuthoredQueries(f.client, ["notes.Note"]);
  });
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));

  // An unrelated model must not refetch it.
  await act(async () => {
    await invalidateAuthoredQueries(f.client, ["iam.User"]);
  });
  expect(f.custom).toHaveBeenCalledTimes(2);
});
