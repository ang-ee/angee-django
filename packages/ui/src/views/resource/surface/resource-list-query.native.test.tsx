// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { refineResourceIdentifier } from "@angee/metadata";
import { testDataResource, testResourceQuery } from "@angee/metadata/testing";
import { createAngeeChangeLiveProvider, createAngeeHasuraDataProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { createUiTestProviders } from "../../../testing";
import { useResourceListQuery } from "./resource-list-query";

const resource = testDataResource("storage.File", {
  roots: { changes: "fileChanged", aggregate: "files_aggregate" },
  typeNames: { filter: "FileBoolExp", order: "FileOrderBy" },
  query: testResourceQuery({ fields: { id: { kind: "scalar", scalar: "ID", values: [], nullable: false, filter: { field: "id", scalar: "ID", values: [], operators: ["inList"] } } } }),
});
const { Provider, createClient, clearClients } = createUiTestProviders();
afterEach(() => { cleanup(); clearClients(); vi.useRealTimers(); });

test.each([
  { retained: false, declaration: "exact", action: "update" },
  { retained: true, declaration: "exact", action: "update" },
  { retained: true, declaration: "exact", action: "create" },
  { retained: true, declaration: "exact", action: "delete" },
  { retained: false, declaration: "related", action: "update" },
  { retained: true, declaration: "related", action: "create" },
  { retained: true, declaration: "related", action: "delete" },
  { retained: false, declaration: "broad", action: "update" },
  { retained: true, declaration: "broad", action: "update" },
  { retained: true, declaration: "shared-before", action: "update" },
  { retained: true, declaration: "shared-after", action: "update" },
])("native list interests keep held reads and reconcile $action ($declaration retained=$retained)", async ({ retained, declaration, action }) => {
  type Transport = NonNullable<Parameters<typeof createAngeeChangeLiveProvider>[0]>;
  let deliver!: Parameters<Transport["subscribe"]>[1]["next"];
  const attempts: { signal: AbortSignal | null | undefined; resolve: (response: Response) => void }[] = [];
  const fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((resolve, reject) => {
    attempts.push({ signal: init?.signal, resolve });
    init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  }));
  const dataProvider = createAngeeHasuraDataProvider({ url: "https://example.invalid/graphql", auth: (base) => base, fetch });
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const liveProvider = createAngeeChangeLiveProvider({
    subscribe: (_request, sink) => { deliver = sink.next; return () => undefined; },
    on: () => () => undefined,
  }, [{ ...resource, resourceIdentifier: refineResourceIdentifier(resource) }], { queryClient: client });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider resources={[resource]} dataProvider={dataProvider}
    queryClient={client} liveProvider={liveProvider} options={{ liveMode: "auto" }}>{children}</Provider>;
  const options = { resource, fields: ["id"], scope: { filter: { id: { inList: ["file-current"] } }, order: {}, page: 1, pageSize: 10 },
    ...(declaration === "exact" || declaration.startsWith("shared-") ? { records: [{ model: "storage.File", id: "file-current" }] }
      : declaration === "related" ? { records: [{ model: "notes.Target", id: "target-current" }], relatedModels: ["storage.File"] } : {}),
  };
  const broadOptions = { ...options, records: undefined, relatedModels: undefined };
  if (declaration === "shared-before") renderHook(() => useResourceListQuery(broadOptions), { wrapper });
  const hook = renderHook(() => useResourceListQuery(options), { wrapper });
  if (declaration === "shared-after") renderHook(() => useResourceListQuery(broadOptions), { wrapper });
  const initialCount = action === "create" ? 0 : 1;
  const response = (count = initialCount) => Response.json({ data: { [resource.roots.list!]: count ? [{ id: "file-current" }] : [],
    [`${resource.roots.list!}_aggregate`]: { aggregate: { count } } } });
  expect(hook.result.current.query.error).toBeNull();
  await waitFor(() => expect(attempts).toHaveLength(1));
  if (retained) {
    await act(async () => { attempts[0]!.resolve(response()); });
    await waitFor(() => expect(hook.result.current.query.isSuccess).toBe(true));
    expect(hook.result.current.result.data).toHaveLength(initialCount);
    act(() => { void hook.result.current.query.refetch(); });
    await waitFor(() => expect(attempts).toHaveLength(2));
  }
  const held = attempts.at(-1)!;
  act(() => { deliver({ data: { fileChanged: { model: "storage.File", id: "file-other", action: "update" } } }); });
  const broad = declaration === "broad" || declaration.startsWith("shared-");
  expect(held.signal?.aborted).toBe(broad);
  if (broad) { liveProvider.setEnabled(false); return; }
  await act(async () => { held.resolve(response()); });
  await waitFor(() => expect(hook.result.current.query.isSuccess).toBe(true));
  expect(hook.result.current.result.data).toHaveLength(initialCount);
  const beforeRefresh = attempts.length;
  act(() => { void hook.result.current.query.refetch(); });
  await waitFor(() => expect(attempts).toHaveLength(beforeRefresh + 1));
  const obsolete = attempts.at(-1)!;
  act(() => { deliver({ data: { fileChanged: { model: "storage.File", id: declaration === "exact" ? "file-current" : "file-child", action,
    ...(declaration === "related" ? { relatedRecords: [{ model: "notes.Target", id: "target-current" }] } : {}),
  } } }); });
  expect(obsolete.signal?.aborted).toBe(true);
  await waitFor(() => expect(attempts).toHaveLength(beforeRefresh + 2));
  await act(async () => { attempts.at(-1)!.resolve(action === "delete" ? Response.json({ data: {
    [resource.roots.list!]: [], [`${resource.roots.list!}_aggregate`]: { aggregate: { count: 0 } },
  } }) : response(1)); });
  await waitFor(() => expect(hook.result.current.result.data).toHaveLength(action === "delete" ? 0 : 1));
  liveProvider.setEnabled(false);
});
