// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { useAuthoredQuery, type TypedDocumentNode } from "@angee/refine";
import { useList, useOne, type MetaQuery } from "@refinedev/core";
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
  const resource = testDataResource("parties.PartyStatement");
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
      invalidateModels: ["parties.PartyStatement"],
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

const resource = testDataResource("notes.Note");
const BODY = gql`query NoteBody { body }` as TypedDocumentNode<{ body: number }, Record<string, never>>;
const BINDINGS = gql`query RecordBindings { bindings }` as TypedDocumentNode<{ bindings: number }, Record<string, never>>;
const WRITE = gql`mutation WriteNote($id: ID!) { write_note(id: $id) { id } }` as TypedDocumentNode<{ write_note: { id: string } }, { id: string }>;

test("mixed resource and authored-only effects reconcile their own native keys without rejecting a committed write", async () => {
  let revision = 0;
  const getList = vi.fn(async () => ({ data: [{ id: "note-1", revision }], total: 1 }));
  const getOne = vi.fn(async ({ id }: { id?: string | number }) => ({ data: { id, revision } }));
  const custom = vi.fn(async ({ meta }: { meta?: MetaQuery }) => {
    if (meta?.gqlQuery === BODY) return { data: { body: revision } };
    if (meta?.gqlQuery === BINDINGS) return { data: { bindings: revision } };
    revision += 1;
    return { data: { write_note: { id: "note-1" } } };
  });
  const { result } = renderHook(() => {
    const list = useList({ resource: resource.roots.list!, dataProviderName: "console" });
    const other = useOne({ resource: resource.roots.list!, id: "note-2", dataProviderName: "console" });
    const body = useAuthoredQuery(BODY, {}, { dataProviderName: "console", models: ["notes.NoteBody"] });
    const bindings = useAuthoredQuery(BINDINGS, {}, {
      dataProviderName: "console", records: [{ model: "notes.Target", id: "target-1" }],
      relatedModels: ["notes.RecordBinding"],
    });
    const [write, state] = useAuthoredResourceMutation(WRITE, {
      dataProviderName: "console", invalidateModels: ["notes.Note", "notes.NoteBody"],
      records: (_data, variables) => [
        { model: "notes.Note", id: variables.id },
        { model: "notes.RecordBinding", id: "binding-1", relatedRecords: [{ model: "notes.Target", id: "target-1" }] },
      ],
    });
    return { list, other, body, bindings, write, state };
  }, { wrapper: ({ children }) => <Provider resources={[resource]} dataProvider={{ getList, getOne, custom }}>{children}</Provider> });
  await waitFor(() => {
    expect(result.current.list.result.data?.[0]?.revision).toBe(0);
    expect(result.current.other.result?.id).toBe("note-2");
    expect(result.current.body.data?.body).toBe(0);
    expect(result.current.bindings.data?.bindings).toBe(0);
  });
  await act(async () => {
    await expect(result.current.write({ id: "note-1" })).resolves.toEqual({ write_note: { id: "note-1" } });
  });
  await waitFor(() => {
    expect(result.current.list.result.data?.[0]?.revision).toBe(1);
    expect(result.current.body.data?.body).toBe(1);
    expect(result.current.bindings.data?.bindings).toBe(1);
  });
  expect(result.current.state.error).toBeNull();
  expect(getOne).toHaveBeenCalledTimes(1);
  expect(custom.mock.calls.filter(([params]) => params.meta?.gqlQuery === BODY)).toHaveLength(2);
  expect(custom.mock.calls.filter(([params]) => params.meta?.gqlQuery === BINDINGS)).toHaveLength(2);
});
