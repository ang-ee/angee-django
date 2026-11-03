// @vitest-environment happy-dom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { refineResourceIdentifier, type ModelMetadata, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { createAngeeChangeLiveProvider } from "@angee/refine";
import { keys } from "@refinedev/core";
import { QueryObserver } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { createUiTestProviders } from "../../testing";
import { useBoardLaneState, type ResolvedBoardLaneSource } from "./resource-view-board-lanes";

vi.mock("../../feedback", async (original) => ({
  ...(await original<typeof import("../../feedback")>()),
  useToast: () => ({ danger: vi.fn() }),
}));
vi.mock("../../i18n", async (original) => ({
  ...(await original<typeof import("../../i18n")>()),
  useUiT: () => (key: string) => key,
}));
vi.mock("../relation/relation-options", async (original) => ({
  ...(await original<typeof import("../relation/relation-options")>()),
  useRelationOptions: () => ({ options: [], rows: [], list: { fetching: false } }),
}));

const { Provider, createClient, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); });

test.each([
  { withProvider: true, intent: false },
  { withProvider: false, intent: false },
  { withProvider: true, intent: true },
])("board drag save lets a held list read settle before one reconciliation refetch (provider=$withProvider intent=$intent)", async ({ withProvider, intent }) => {
  const resource = testDataResource("crm.Lead", { roots: { list: "crmLeads" } });
  const model = { resource, fields: { stage: { name: "stage", kind: "relation", updatable: true },
    sort_order: { name: "sort_order", kind: "scalar", updatable: true },
    task_order: { name: "task_order", kind: "scalar", updatable: true } } } as unknown as ModelMetadata;
  const source = { field: "stage", relation: { resource: "crm.Stage", labelField: "name", canCreate: false },
    fieldMetadata: { name: "stage", kind: "relation", nullable: true },
    ...(intent ? { rankField: "sort_order", rankInputField: "task_order" } : {}) } as ResolvedBoardLaneSource;
  type Lead = { id: string; stage: { id: string; name: string } | string; sort_order: number };
  const row: Lead = { id: "lead-1", stage: { id: "new", name: "New" }, sort_order: 1024 };
  const freshRow: Lead = { ...row, stage: "qualified", sort_order: 1792 };
  let rows = [row];
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const key = keys().data("console").resource(refineResourceIdentifier(resource)).action("list").get();
  client.setQueryData(key, { data: [row], total: 1 });
  let finishOld!: (value: { data: Row[]; total: number }) => void;
  let finishFresh!: (value: { data: Row[]; total: number }) => void;
  const signals: AbortSignal[] = [];
  const read = vi.fn(({ signal }: { signal: AbortSignal }) => {
    signals.push(signal);
    return new Promise<{ data: Row[]; total: number }>((resolve) => {
      if (signals.length === 1) finishOld = resolve;
      else finishFresh = resolve;
    });
  });
  const observer = new QueryObserver(client, { queryKey: key, queryFn: read });
  const stop = observer.subscribe(() => undefined);
  const older = observer.refetch();
  const update = vi.fn(async () => ({ data: freshRow }));
  const liveProvider = withProvider ? createAngeeChangeLiveProvider(undefined, [], { queryClient: client }) : undefined;
  const wrapper = ({ children }: { children: ReactNode }) => <Provider resources={[resource]} queryClient={client}
    liveProvider={liveProvider} dataProvider={{ update }}>{children}</Provider>;
  const hook = renderHook(() => useBoardLaneState({ laneSource: source, modelMetadata: model,
    rows, enabled: true }), { wrapper });
  try {
    let move!: Promise<void>;
    act(() => { move = hook.result.current.onCardMove!(row, "qualified",
      intent ? { preceding: [], following: [] } : undefined); });
    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(signals).toHaveLength(1);
    expect(signals[0]?.aborted).toBe(false);
    expect(hook.result.current.optimisticPlacementByRowId.get("lead-1")?.laneId).toBe("qualified");
    await act(async () => { finishOld({ data: [row], total: 1 }); await older; });
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(signals[0]?.aborted).toBe(false);
    expect(signals[1]?.aborted).toBe(false);
    if (intent) expect(hook.result.current.optimisticPlacementByRowId.get("lead-1")?.laneId).toBe("qualified");
    await act(async () => { finishFresh({ data: [freshRow], total: 1 }); await move; });
    rows = [freshRow];
    hook.rerender();
    await waitFor(() => expect(hook.result.current.optimisticPlacementByRowId.size).toBe(0));
    expect(read).toHaveBeenCalledTimes(2);
  } finally {
    liveProvider?.setEnabled(false);
    stop();
  }
});
