// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { type TypedDocumentNode } from "@angee/refine";
import { useList, useOne } from "@refinedev/core";
import gql from "graphql-tag";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";

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

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://query",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } },
});

afterEach(() => {
  cleanup();
  clearClients();
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
  const dataProvider = { getList, getOne, custom };
  function Providers({ children }: { children: ReactNode }) {
    return <Provider resources={[resource, unrelated]} dataProvider={dataProvider}>{children}</Provider>;
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
