// @vitest-environment happy-dom
import { act, cleanup, renderHook } from "@testing-library/react";
import { createAngeeChangeLiveProvider, useReconcileMutationResponse, OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import { keys } from "@refinedev/core";
import { QueryObserver } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { createUiTestProviders } from "../../testing";
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
    <OperationDocumentsProvider documents={{ console: { actions: { refresh_source: "mutation Refresh($id: String!) { refresh_source(id: $id) { ok message } }" } } }}>{children}</OperationDocumentsProvider>
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
    <OperationDocumentsProvider documents={{ console: { actions: { refresh_source: "mutation Refresh($id: String!) { refresh_source(id: $id) { ok message } }" } } }}>{children}</OperationDocumentsProvider>
  </Provider>;
  const records = [{ model: "notes.Note", id: covered ? "n1" : "another-record" }];
  const hook = renderHook(() => {
    const reconcile = useReconcileMutationResponse();
    const plain = useRecordAction(async () => { await reconcile(undefined, undefined, { records }); return "Synced"; });
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
    return useRecordAction(async () => { await reconcile(undefined, undefined, { records: [{ model: "notes.Note", id: "n1" }] }); }, { refresh: true });
  }, { wrapper });
  try {
    await act(async () => { await hook.result.current({ record: { id: "n1" }, values: {}, refresh,
      refreshQueryKey: ["exact-form"], update: async () => null, prompt: async () => null }); });
    expect(refresh).toHaveBeenCalledOnce();
  } finally { provider.setEnabled(false); }
});
