// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import {
  ModelMetadataProvider,
  refineResourcesFromDataResources,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { type TypedDocumentNode } from "@angee/refine";
import { Refine, type DataProvider, useList, useOne } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import gql from "graphql-tag";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useAuthoredResourceMutation } from "./authored-resource-mutation";

interface DecideResult {
  decide: { id: string };
}

interface DecideVariables extends Record<string, unknown> {
  id: string;
}

const DECIDE = gql`
  mutation Decide($id: ID!) {
    decide(id: $id) { id }
  }
` as TypedDocumentNode<DecideResult, DecideVariables>;

const clients: QueryClient[] = [];

afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
});

test("an authored resource mutation refreshes the declared resource's list and real-id detail only", async () => {
  let name = "Suggested";
  const getList = vi.fn(async () => ({
    data: [{ id: "handle-1", name }],
    total: 1,
  }));
  const custom = vi.fn(async () => {
    name = "Confirmed";
    return { data: { decide: { id: "handle-1" } } };
  });
  const getOne = vi.fn(async ({ id }: { id: string | number }) => ({
    data: {
      id,
      name: id === "handle-1" ? name : "Unrelated",
    },
  }));
  const resource = testDataResource("parties.PartyHandle");
  const unrelated = testDataResource("notes.Note");
  const metadata = schemaFieldMetadataFromDataResources([resource, unrelated]);
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  clients.push(client);
  const provider = {
    getApiUrl: () => "test://query",
    getList,
    getOne,
    create: vi.fn(),
    update: vi.fn(),
    deleteOne: vi.fn(),
    custom,
  } as unknown as DataProvider;

  function Providers({ children }: { children: ReactNode }) {
    return (
      <Refine
        resources={[...refineResourcesFromDataResources([resource, unrelated])]}
        dataProvider={{ default: provider, console: provider }}
        options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}
      >
        <ModelMetadataProvider metadata={metadata}>
          {children}
        </ModelMetadataProvider>
      </Refine>
    );
  }

  const { result } = renderHook(() => {
    const list = useList<{ id: string; name: string }>({
      resource: resource.roots.list!,
      dataProviderName: "console",
    });
    const detail = useOne<{ id: string; name: string }>({
      resource: resource.roots.list!,
      id: "handle-1",
      dataProviderName: "console",
    });
    const unrelatedDetail = useOne<{ id: string; name: string }>({
      resource: unrelated.roots.list!,
      id: "note-1",
      dataProviderName: "console",
    });
    const [decide] = useAuthoredResourceMutation(DECIDE, {
      dataProviderName: "console",
      invalidateModels: ["parties.PartyHandle"],
    });
    return { decide, detail, list, unrelatedDetail };
  }, { wrapper: Providers });

  await waitFor(() => expect(result.current.list.result.data?.[0]?.name).toBe("Suggested"));
  await waitFor(() => expect(result.current.detail.result?.name).toBe("Suggested"));
  await waitFor(() => expect(result.current.unrelatedDetail.result?.name).toBe("Unrelated"));

  await act(async () => {
    await result.current.decide({ id: "handle-1" });
  });

  await waitFor(() => expect(result.current.list.result.data?.[0]?.name).toBe("Confirmed"));
  await waitFor(() => expect(result.current.detail.result?.name).toBe("Confirmed"));
  expect(getList).toHaveBeenCalledTimes(2);
  expect(getOne.mock.calls.filter(([params]) => params.id === "handle-1")).toHaveLength(2);
  expect(getOne.mock.calls.filter(([params]) => params.id === "note-1")).toHaveLength(1);
});
