// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useCustom, useInfiniteList, useList, useMany, useOne, type MetaQuery } from "@refinedev/core";
import { StrictMode, type ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { createAngeeHasuraDataProvider } from "./provider";
import { createRefineTestProviders } from "./testing";

const { Provider, createClient, clearClients } = createRefineTestProviders();
afterEach(() => { cleanup(); clearClients(); });
const readers = [
  ["useCustom", () => useCustom({ url: "", method: "get", meta: { operation: "notes", fields: ["id"] } }).query],
  ["useList", () => useList({ resource: "notes" }).query],
  ["useOne", () => useOne({ resource: "notes", id: "one" }).query],
  ["useMany", () => useMany({ resource: "notes", ids: ["one"] }).query],
  ["useInfiniteList", () => useInfiniteList({ resource: "notes" }).query],
] as const;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

test.each(readers)("%s shares pending Hasura transport across StrictMode and remount", async (_name, useRead) => {
  const pending = deferred<Response>();
  const request = vi.fn(() => pending.promise);
  const dataProvider = createAngeeHasuraDataProvider({
    url: "https://example.invalid/graphql", auth: (fetch) => fetch, fetch: request,
  });
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const wrapper = ({ children }: { children: ReactNode }) => <StrictMode><Provider dataProvider={dataProvider} queryClient={client}>{children}</Provider></StrictMode>;
  const first = renderHook(() => useRead().isSuccess, { wrapper });
  expect(request).toHaveBeenCalledTimes(1);
  first.unmount();
  const second = renderHook(() => useRead().isSuccess, { wrapper });
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve(new Response(JSON.stringify({ data: {
    notes: [{ id: "one" }], notes_by_pk: { id: "one" },
    notes_aggregate: { aggregate: { count: 1 } },
  } }), { headers: { "Content-Type": "application/json" } })); });
  await waitFor(() => expect(second.result.current).toBe(true));
  expect(request).toHaveBeenCalledTimes(1);
});

test.each(readers)("%s cancels on unmount when its provider consumes the signal", async (_name, useRead) => {
  const pending = deferred<{ data: { id: string }[]; total: number }>();
  let signal: AbortSignal | undefined;
  const request = vi.fn(({ meta }: { meta?: MetaQuery }) => {
    signal = meta?.signal;
    return pending.promise;
  });
  const dataProvider = { custom: request, getList: request, getOne: request, getMany: request };
  const client = createClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider dataProvider={dataProvider} queryClient={client}>{children}</Provider>;
  const first = renderHook(() => useRead().isSuccess, { wrapper });
  const query = client.getQueryCache().getAll().find((entry) => entry.state.fetchStatus === "fetching");
  expect(signal?.aborted).toBe(false);
  first.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => { pending.resolve({ data: [{ id: "late" }], total: 1 }); });
  expect(query?.state.data).toBeUndefined();
  expect(request).toHaveBeenCalledTimes(1);
});
