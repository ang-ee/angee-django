// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { keys } from "@refinedev/core";
import { afterEach, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { ActiveDataProviderNameProvider } from "./data-provider-context";
import { OperationDocumentsProvider } from "../operation-documents";
import { createRefineTestProviders } from "../testing";
import { useActionMutation } from "./hooks";
import type { ActionOutcome } from "../operations";

const { Provider, createClient, clearClients } = createRefineTestProviders();
afterEach(() => { cleanup(); clearClients(); });
const ACTION_DOCUMENT = "mutation Probe($id: String) { run_probe(id: $id) { ok message id } }";

function fixture(response: Record<string, unknown> = { ok: true, message: "Done" }, active = true) {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const custom = vi.fn(async () => ({ data: { run_probe: response } }));
  const wrapper = ({ children }: { children: ReactNode }) => {
    const documents = <OperationDocumentsProvider documents={{ console: { actions: { run_probe: ACTION_DOCUMENT } } }}>{children}</OperationDocumentsProvider>;
    return <Provider queryClient={client} dataProvider={{ custom }} providerNames={["console"]}>
      {active ? <ActiveDataProviderNameProvider name="console">{documents}</ActiveDataProviderNameProvider> : documents}
    </Provider>;
  };
  const seed = (queryKey: readonly unknown[], model?: string) => {
    client.setQueryData(queryKey, { retained: true });
    const query = client.getQueryCache().find({ queryKey, exact: true })!;
    if (model) query.setOptions({ ...query.options, meta: { angeeModels: [model] } });
    return query;
  };
  return { custom, client, wrapper, seed };
}

test("actions resolve the document/provider from the active schema and return the created record", async () => {
  const f = fixture({ ok: true, message: "Created.", id: "tr_9" });
  const { result } = renderHook(() => useActionMutation("run_probe"), { wrapper: f.wrapper });
  let outcome: ActionOutcome | undefined;
  await act(async () => { outcome = await result.current[0]("rec_1"); });
  expect(outcome).toEqual({ ok: true, message: "Created.", id: "tr_9" });
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({ payload: { id: "rec_1" }, meta: expect.objectContaining({ gqlMutation: ACTION_DOCUMENT }) }));
});

test.each(["round", null])("actions bind only their declared target argument: %s", async (idArgument) => {
  const f = fixture();
  const { result } = renderHook(() => useActionMutation("run_probe", { idArgument }), { wrapper: f.wrapper });
  await act(async () => { await result.current[0]("round_1", { proposals: ["proposal_2"] }); });
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({ payload: idArgument === null
    ? { proposals: ["proposal_2"] } : { round: "round_1", proposals: ["proposal_2"] } }));
});

test("explicit provider options resolve an action without ambient schema context", async () => {
  const f = fixture(undefined, false);
  const { result } = renderHook(() => useActionMutation("run_probe", { dataProviderName: "console" }), { wrapper: f.wrapper });
  await act(async () => { await result.current[0]("rec_2"); });
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({ payload: { id: "rec_2" } }));
});

test.each([false, true])("recorded domain failure effects preserve invalidateOnFailure=%s", async (invalidateOnFailure) => {
  const f = fixture({ ok: false, message: "Not allowed.", validation_errors: { __all__: ["Permission denied."] } });
  const resource = f.seed(keys().data("console").resource("records").action("list").get());
  const authored = f.seed(["authored-agent"], "agents.Agent");
  const untouched = f.seed(["another-model"], "messaging.Thread");
  const acceptResponse = vi.fn();
  const { result } = renderHook(() => useActionMutation("run_probe", {
    invalidates: [{ dataProviderName: "console", resource: "records", invalidates: ["list"] }],
    invalidateModels: ["agents.Agent"], invalidateOnFailure, acceptResponse,
  }), { wrapper: f.wrapper });
  let outcome: ActionOutcome | undefined;
  await act(async () => { outcome = await result.current[0]("rec_1"); });
  expect(outcome).toEqual({ ok: false, message: "Not allowed.", validationErrors: { __all__: ["Permission denied."] } });
  expect(resource.state.isInvalidated).toBe(invalidateOnFailure);
  expect(authored.state.isInvalidated).toBe(invalidateOnFailure);
  expect(untouched.state.isInvalidated).toBe(false);
  expect(acceptResponse).not.toHaveBeenCalled();
});

test("successful actions reconcile native and declared authored interests", async () => {
  const f = fixture();
  const native = f.seed(keys().data("console").resource("records").action("list").get());
  const authored = f.seed(["channel"], "messaging.Channel");
  const unrelated = f.seed(["thread"], "messaging.Thread");
  const { result } = renderHook(() => useActionMutation("run_probe", {
    invalidates: [{ dataProviderName: "console", resource: "records", invalidates: ["list"] }],
    invalidateModels: ["messaging.Channel"],
  }), { wrapper: f.wrapper });
  await act(async () => { await result.current[0]("chn_1"); });
  expect(native.state.isInvalidated).toBe(true);
  expect(authored.state.isInvalidated).toBe(true);
  expect(unrelated.state.isInvalidated).toBe(false);
});

test("transient generated actions reject overlap and remove secret input/results after settlement", async () => {
  const f = fixture();
  let release!: (response: { data: { run_probe: Record<string, unknown> } }) => void;
  const response = new Promise<{ data: { run_probe: Record<string, unknown> } }>((resolve) => { release = resolve; });
  f.custom.mockImplementationOnce(() => response);
  const { result } = renderHook(() => useActionMutation("run_probe", { transient: true }), { wrapper: f.wrapper });
  let completion!: ReturnType<typeof result.current[0]>;
  act(() => { completion = result.current[0]("chn_1", { password: "one-use-secret" }); });
  await waitFor(() => expect(result.current[1].fetching).toBe(true));
  await expect(result.current[0]("chn_1", { password: "another" })).rejects.toThrow("Transient mutations cannot overlap another mutation.");
  expect(f.custom).toHaveBeenCalledOnce();
  expect(f.custom).toHaveBeenCalledWith(expect.objectContaining({ payload: { id: "chn_1", password: "one-use-secret" } }));
  await act(async () => { release({ data: { run_probe: { ok: true, message: "Accepted." } } }); await completion; });
  await waitFor(() => expect(f.client.getMutationCache().getAll()).toHaveLength(0));
  expect(result.current[1].fetching).toBe(false);
  expect(result.current[1].error).toBeNull();
});
