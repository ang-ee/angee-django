// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { Refine, useList, type DataProvider } from "@refinedev/core";
import { QueryClient, keepPreviousData } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useAngeeAggregate, type TypedDocumentNode } from "@angee/refine";

import { useInvalidateDataResource } from "./resource-operations";

// Registered the way `refineResourceFromDataResource` registers every data
// resource: the list root as `name`, `<schema>:<modelLabel>` as `identifier`.
const RESOURCE = {
  schemaName: "public",
  modelLabel: "notes.Note",
  appLabel: "notes",
  modelName: "note",
  roots: { list: "notes", aggregate: "notes_aggregate" },
  typeNames: { node: "NoteType" },
  recordRepresentation: "title",
  capabilities: ["list", "aggregate"],
  fields: [],
  aggregateFields: [],
} as unknown as Parameters<ReturnType<typeof useInvalidateDataResource>>[0];

const AGGREGATE_TARGET = {
  dataProviderName: "public",
  root: "notes_aggregate",
  modelLabel: "notes.Note",
};
const AGGREGATE_DOCUMENT = {} as TypedDocumentNode<
  { notes_aggregate: { count: number } },
  Record<string, never>
>;

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
});

function fixture() {
  const getList = vi.fn(async () => ({ data: [{ id: "note-a" }], total: 1 }));
  const custom = vi.fn(async () => ({ data: { notes_aggregate: { count: 1 } } }));
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, placeholderData: keepPreviousData },
    },
  });
  clients.push(client);
  const provider = {
    getApiUrl: () => "test://query",
    getList,
    getOne: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    deleteOne: vi.fn(),
    custom,
  } as unknown as DataProvider;
  function Providers({ children }: { children: ReactNode }) {
    return (
      <Refine
        dataProvider={{ default: provider, public: provider }}
        resources={[
          {
            name: "notes",
            identifier: "public:notes.Note",
            meta: { dataProviderName: "public" },
          },
        ]}
        options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}
      >
        {children}
      </Refine>
    );
  }
  return { client, getList, custom, wrapper: Providers };
}

test("the owner refreshes both the keyed list and the model's custom reads", async () => {
  const f = fixture();
  const { result } = renderHook(
    () => ({
      // Keyed exactly as `useAngeeListBatch` keys it: on the identifier.
      list: useList({
        resource: "public:notes.Note",
        dataProviderName: "public",
      }),
      aggregate: useAngeeAggregate(AGGREGATE_TARGET, { document: AGGREGATE_DOCUMENT }),
      invalidateDataResource: useInvalidateDataResource(),
    }),
    { wrapper: f.wrapper },
  );

  await waitFor(() => expect(f.getList).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(1));

  await act(async () => {
    await result.current.invalidateDataResource(RESOURCE, "note-a");
  });

  // The list answers because the owner passes the identifier, not the list root.
  await waitFor(() => expect(f.getList).toHaveBeenCalledTimes(2));
  // The aggregate answers because the owner asks for the model's authored reads.
  await waitFor(() => expect(f.custom).toHaveBeenCalledTimes(2));
});
