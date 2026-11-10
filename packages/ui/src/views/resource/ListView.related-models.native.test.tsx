// @vitest-environment happy-dom

import { act, cleanup, render } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { createAngeeChangeLiveProvider } from "@angee/refine";
import type { MetaQuery } from "@refinedev/core";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { Column } from "../page";
import { List } from "./List";
import { ListView } from "./ListView";
import { ResourceList } from "./ResourceList";

const resource = testDataResource("notes.Note", {
  roots: { list: "notes", aggregate: "notes_aggregate", changes: "noteChanged" },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  query: testResourceQuery({ fields: { id: testQueryField("id"), title: testQueryField("title") } }),
});
const attachment = testDataResource("notes.Attachment", { roots: { changes: "attachmentChanged" } });
const relatedModels = [attachment.modelLabel] as const;
const { Provider, createClient, clearClients } = createUiTestProviders({ apiUrl: "test://list-related" });
afterEach(() => { cleanup(); clearClients(); vi.clearAllTimers(); vi.useRealTimers(); });

type SubscriptionClient = Parameters<typeof createAngeeChangeLiveProvider>[0];
type Change = { model: string; id?: string; relatedRecords?: readonly { model: string; id: string }[] };
type ListResponse = { data: { id: string; title: string }[]; total: number };
const rows: ListResponse = { data: [{ id: "on-page", title: "Visible note" }], total: 1 };

async function advance(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

function fixture(kind: "ListView" | "List" | "ResourceList" | "nested List" | "client" = "ListView",
  read: (request: { meta?: MetaQuery }) => Promise<ListResponse> = async () => rows,
) {
  vi.useFakeTimers();
  const getList = vi.fn(read);
  const queryClient = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const sinks = new Map<string, { next: (result: { data: Record<string, Change> }) => void }>();
  const disposers: ReturnType<typeof vi.fn>[] = [];
  const subscribe = vi.fn((request: { query: string }, sink: { next: (result: { data: Record<string, Change> }) => void }) => {
    const root = /subscription angee_(\w+)/.exec(request.query)?.[1];
    if (!root) throw new Error(`Unexpected subscription: ${request.query}`);
    sinks.set(root, sink);
    const dispose = vi.fn();
    disposers.push(dispose);
    return dispose;
  });
  const liveProvider = createAngeeChangeLiveProvider(
    { subscribe, on: () => () => undefined } as SubscriptionClient,
    [resource, attachment].map((model) => ({ ...model, identifier: `${model.schemaName}:${model.modelLabel}` })),
    { queryClient },
  );
  const liveSubscribe = vi.spyOn(liveProvider, "subscribe");
  const onLiveEvent = vi.fn();
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const common = { resource: resource.modelLabel, scope: "local" as const, relatedModels };
  const columns = [{ field: "title" }];
  const view = kind === "List" ? <List {...common}><Column field="title" /></List>
    : kind === "ResourceList" ? <ResourceList {...common} columns={columns} hideCreate />
      : kind === "nested List" ? <ResourceList resource={resource.modelLabel} scope="local" hideCreate>
        <List relatedModels={relatedModels}><Column field="title" /></List>
      </ResourceList>
        : <ListView {...common} columns={columns} />;
  const mounted = render(<Provider resources={[kind === "client" ? { ...resource, rowModel: "client" } : resource, attachment]}
    dataProvider={{ getList }} queryClient={queryClient} liveProvider={liveProvider} onLiveEvent={onLiveEvent} options={{ liveMode: "auto" }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      {view}
    </AppRuntimeProvider></ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
  function emit(root: string, change: Change) {
    const sink = sinks.get(root);
    if (!sink) throw new Error(`No subscription for ${root}`);
    sink.next({ data: { [root]: change } });
  }
  const related = (model = resource.modelLabel) => emit("attachmentChanged", {
    model: attachment.modelLabel, id: "attachment-1", relatedRecords: [{ model, id: "off-page" }],
  });
  return { ...mounted, emit, related, getList, subscribe, liveSubscribe, disposers, onLiveEvent };
}

test.each(["ListView", "List", "ResourceList", "nested List", "client"] as const)(
  "%s refreshes for a related off-page record and ignores another model", async (kind) => {
    const f = fixture(kind);
    await advance();
    expect(f.queryByRole("alert")?.textContent ?? null).toBeNull();
    expect(f.getList).toHaveBeenCalledTimes(1);
    expect(f.liveSubscribe).toHaveBeenCalledWith(expect.objectContaining({
      params: expect.objectContaining({ resource: "notes", subscriptionType: "useList", relatedModels }),
    }));
    expect(f.subscribe).toHaveBeenCalledTimes(2);
    f.related("notes.Folder");
    await advance(300);
    expect(f.getList).toHaveBeenCalledTimes(1);
    f.related();
    await advance(299);
    expect(f.getList).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(f.getList).toHaveBeenCalledTimes(2);
  },
);

test("own and related changes share one quiet window and the two-second maximum wait", async () => {
  const f = fixture();
  await advance();
  f.related();
  await advance(100);
  f.emit("noteChanged", { model: resource.modelLabel, id: "on-page" });
  await advance(299);
  expect(f.getList).toHaveBeenCalledTimes(1);
  await advance(1);
  expect(f.getList).toHaveBeenCalledTimes(2);
  expect(f.onLiveEvent).toHaveBeenCalledTimes(1);
  expect(f.onLiveEvent.mock.calls[0]?.[0].payload.model).toBeUndefined();
  for (let index = 0; index < 20; index++) {
    f.related();
    await advance(100);
    if (index < 19) expect(f.getList).toHaveBeenCalledTimes(2);
  }
  expect(f.getList).toHaveBeenCalledTimes(3);
  expect(f.onLiveEvent).toHaveBeenCalledTimes(2);
});

test("related changes let a pending read settle and then refresh without cancellation", async () => {
  let settle!: (value: ListResponse) => void;
  const pending = new Promise<ListResponse>((resolve) => { settle = resolve; });
  let signal: AbortSignal | undefined;
  let first = true;
  const f = fixture("ListView", ({ meta }) => {
    if (!first) return Promise.resolve(rows);
    first = false;
    signal = meta?.signal;
    return pending;
  });
  try {
    await advance();
    f.related();
    await advance(300);
    expect(f.getList).toHaveBeenCalledTimes(1);
    expect(signal?.aborted).toBe(false);
    await act(async () => { settle(rows); });
    await advance();
    expect(f.getList).toHaveBeenCalledTimes(2);
    expect(signal?.aborted).toBe(false);
  } finally {
    settle(rows);
  }
});

test("unmount releases all roots and discards pending and late related deliveries", async () => {
  const f = fixture();
  await advance();
  f.related();
  f.unmount();
  expect(f.disposers).toHaveLength(2);
  for (const dispose of f.disposers) expect(dispose).toHaveBeenCalledTimes(1);
  f.related();
  await advance(2000);
  expect(f.getList).toHaveBeenCalledTimes(1);
  expect(f.onLiveEvent).not.toHaveBeenCalled();
});
