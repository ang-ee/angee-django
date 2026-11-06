// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useAuthoredMutation, useAcceptQueryData, type TypedDocumentNode, createAngeeChangeLiveProvider, useReconcileMutationResponse, OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import { gql } from "graphql-tag";
import { keys } from "@refinedev/core";
import { QueryObserver } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { createUiTestProviders } from "../../testing";
import { ToastProvider } from "../../feedback";
import { useRecordAction, useRecordActionMutation } from "./record-action";
import type { ActionContext } from "../page";

vi.mock("@tanstack/react-router", async (original) => ({
  ...(await original<object>()), useNavigate: () => vi.fn(),
}));
const { Provider, createClient, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });

test.each([
  { settle: false, refreshOption: undefined }, { settle: false, refreshOption: false }, { settle: false, refreshOption: true },
  { settle: true, refreshOption: undefined }, { settle: true, refreshOption: false }, { settle: true, refreshOption: true },
])("uncovered native record action preserves default/custom refresh (settle=$settle refresh=$refreshOption)", async ({ settle, refreshOption }) => {
  let lastSynced = "before";
  const custom = vi.fn(async () => { lastSynced = "after"; return { data: { refresh_source: { ok: true, message: "Synced" } } }; });
  const client = createClient();
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} dataProvider={{ custom }}>
    <OperationDocumentsProvider documents={{ console: { actions: { refresh_source: "mutation Refresh($id: String!) { refresh_source(id: $id) { ok message } }" } } }}><ToastProvider>{children}</ToastProvider></OperationDocumentsProvider>
  </Provider>;
  let shownLastSynced = "before";
  const refresh = vi.fn(() => { shownLastSynced = lastSynced; });
  const afterSuccess = vi.fn();
  const context: ActionContext = { record: { id: "src-1" }, values: {}, refresh,
    update: async () => null, prompt: async () => null };
  const hook = renderHook(() => useRecordActionMutation("refresh_source", {
    dataProviderName: "console", settle, afterSuccess,
    ...(refreshOption === undefined ? {} : { refresh: refreshOption }),
  }), { wrapper });
  await act(async () => { await hook.result.current[0](context); });
  expect(custom).toHaveBeenCalledOnce();
  expect(refresh).toHaveBeenCalledTimes(refreshOption === false ? 0 : 1);
  expect(shownLastSynced).toBe(refreshOption === false ? "before" : "after");
  expect(afterSuccess).toHaveBeenCalledOnce();
});


test.each([
  { generated: false, covered: true }, { generated: false, covered: false },
  { generated: true, covered: true }, { generated: true, covered: false },
])("native form default completion reads once (generated=$generated covered=$covered)", async ({ generated, covered }) => {
  const client = createClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  const key = keys().data("console").resource("notes_Note").action("one").id("n1").params({ fields: ["id", "title"] }).get();
  client.setQueryData(key, { data: { id: "n1", title: "before" } });
  const read = vi.fn(async () => ({ data: { id: "n1", title: "after" } }));
  const observer = new QueryObserver(client, { queryKey: key, queryFn: read,
    meta: { angeeModels: ["notes.Note"], angeeRecords: [{ model: "notes.Note", id: "n1" }] } });
  const stop = observer.subscribe(() => undefined);
  const provider = createAngeeChangeLiveProvider(undefined, [], { queryClient: client });
  const custom = vi.fn(async () => ({ data: { refresh_source: { ok: true, message: "Synced" } } }));
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={provider} dataProvider={{ custom }}>
    <OperationDocumentsProvider documents={{ console: { actions: { refresh_source: "mutation Refresh($id: String!) { refresh_source(id: $id) { ok message } }" } } }}><ToastProvider>{children}</ToastProvider></OperationDocumentsProvider>
  </Provider>;
  const records = [{ model: "notes.Note", id: covered ? "n1" : "another-record" }];
  const hook = renderHook(() => {
    const reconcile = useReconcileMutationResponse();
    const plain = useRecordAction(async (_id, context) => { await reconcile(undefined, undefined, { records, reconciliation: context.reconciliation }); return "Synced"; });
    const [mutation] = useRecordActionMutation("refresh_source", { dataProviderName: "console", records });
    return generated ? mutation : plain;
  }, { wrapper });
  const refresh = vi.fn(async () => observer.refetch());
  const context: ActionContext = { record: { id: "n1" }, values: {}, refresh, refreshQueryKey: key,
    update: async () => null, prompt: async () => null };
  try {
    await act(async () => { await hook.result.current(context); });
    expect(read).toHaveBeenCalledOnce();
    expect(refresh).not.toHaveBeenCalled();
    expect(observer.getCurrentResult().data?.data.title).toBe("after");
  } finally { provider.setEnabled(false); stop(); }
});

test("explicit true preserves a custom imperative reload even with exact effect coverage", async () => {
  const client = createClient();
  const provider = createAngeeChangeLiveProvider(undefined, [], { queryClient: client });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={provider}>{children}</Provider>;
  const refresh = vi.fn(async () => undefined);
  const hook = renderHook(() => {
    const reconcile = useReconcileMutationResponse();
    return useRecordAction(async (_id, context) => { await reconcile(undefined, undefined, { reconciliation: context.reconciliation, records: [{ model: "notes.Note", id: "n1" }] }); }, { refresh: true });
  }, { wrapper });
  try {
    await act(async () => { await hook.result.current({ record: { id: "n1" }, values: {}, refresh,
      refreshQueryKey: ["exact-form"], update: async () => null, prompt: async () => null }); });
    expect(refresh).toHaveBeenCalledOnce();
  } finally { provider.setEnabled(false); }
});


function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

test.each([
  { first: "A", acceptedB: false }, { first: "B", acceptedB: false },
  { first: "A", acceptedB: true }, { first: "B", acceptedB: true },
] as const)("overlapping native command scopes settle independently ($first first, B accepted=$acceptedB)", async ({ first, acceptedB }) => {
  type Saved = { saved: { id: string; title: string } };
  type Response = { data: Saved };
  const document = gql`mutation Save($id: String!) { saved: save_note(id: $id) { id title } }` as TypedDocumentNode<Saved, { id: string }>;
  const client = createClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  const transport = { A: deferred<Response>(), B: deferred<Response>() };
  const reads = { A: deferred<{ data: Saved["saved"] }>(), B: deferred<{ data: Saved["saved"] }>() };
  const key = (id: string) => keys().data("console").resource("notes_Note").action("one").id(id).params({ fields: ["id", "title"] }).get();
  const read = { A: vi.fn(() => reads.A.promise), B: vi.fn(() => reads.B.promise) };
  for (const id of ["A", "B"] as const) client.setQueryData(key(id), { data: { id, title: "before" } });
  const observers = (["A", "B"] as const).map((id) => new QueryObserver(client, { queryKey: key(id), queryFn: read[id],
    meta: { angeeModels: ["notes.Note"], angeeRecords: [{ model: "notes.Note", id }] } }));
  const stops = observers.map((observer) => observer.subscribe(() => undefined));
  const provider = createAngeeChangeLiveProvider(undefined, [], { queryClient: client });
  const custom = vi.fn((input: { meta?: { gqlVariables?: Record<string, unknown> } }) => transport[input.meta?.gqlVariables?.id === "A" ? "A" : "B"].promise);
  const wrapper = ({ children }: { children: ReactNode }) => <Provider queryClient={client} liveProvider={provider} dataProvider={{ custom }}>{children}</Provider>;
  const hook = renderHook(() => {
    const accept = useAcceptQueryData();
    const records = (_data: Saved | undefined, variables: { id: string }) => [{ model: "notes.Note", id: variables.id }];
    const [saveA] = useAuthoredMutation(document, { dataProviderName: "console", records });
    const [saveB] = useAuthoredMutation(document, { dataProviderName: "console", records,
      ...(acceptedB ? { acceptResponse: (data: Saved | undefined) => accept(key("B"), { data: data!.saved }) } : {}),
    });
    const a = useRecordAction(async (id, context) => { await saveA({ id }, { reconciliation: context.reconciliation }); });
    const b = useRecordAction(async (id, context) => { await saveB({ id }, { reconciliation: context.reconciliation }); });
    return { A: a, B: b };
  }, { wrapper });
  const context = (id: string): ActionContext => ({ record: { id }, values: {}, refreshQueryKey: key(id),
    refresh: vi.fn(), update: async () => null, prompt: async () => null });
  const settled = { A: false, B: false };
  const completions: Partial<Record<"A" | "B", Promise<unknown>>> = {};
  const second: "A" | "B" = first === "A" ? "B" : "A";
  try {
    act(() => {
      completions.A = hook.result.current.A(context("A")).then(() => { settled.A = true; });
      completions.B = hook.result.current.B(context("B")).then(() => { settled.B = true; });
    });
    await waitFor(() => expect(custom).toHaveBeenCalledTimes(2));
    for (const id of [first, second] as const) {
      await act(async () => { transport[id].resolve({ data: { saved: { id, title: "accepted" } } }); });
      if (id === "B" && acceptedB) {
        await waitFor(() => expect(settled.B).toBe(true));
        expect(read.B).not.toHaveBeenCalled();
      } else {
        await waitFor(() => expect(read[id]).toHaveBeenCalledOnce());
        expect(settled[id]).toBe(false);
        await act(async () => { reads[id].resolve({ data: { id, title: "canonical" } }); await completions[id]; });
      }
      expect(settled[id]).toBe(true);
      if (id === first) {
        expect(settled[second]).toBe(false);
        expect(read[second]).not.toHaveBeenCalled();
      }
    }
    for (const mutation of client.getMutationCache().getAll()) {
      const input = mutation.state.variables as { values?: Record<string, unknown>; meta?: object } | undefined;
      expect(Object.keys(input?.values ?? {})).toEqual(["id"]);
      expect(Object.getOwnPropertySymbols(input?.meta ?? {})).toHaveLength(0);
      expect((mutation.state.context as { execution?: unknown } | undefined)?.execution).toBeUndefined();
    }
  } finally { provider.setEnabled(false); stops.forEach((stop) => stop()); }
});
