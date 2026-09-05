// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { flexRender } from "@tanstack/react-table";
import { createAngeeHasuraDataProvider } from "@angee/refine";
import { refineResourcesFromDataResources, schemaFieldMetadataFromDataResources, type ModelMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { ResourceViewProvider, useResourceView } from "@angee/ui/views/resource-view-context";
import { useResourceViewSurface, type ResourceViewSurface } from "@angee/ui/views/resource-view-surface";
import { ToastProvider } from "@angee/ui/feedback/index";
import { afterEach, expect, test, vi } from "vitest";

const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });
async function fixture(initialSort = "thread__title__text") {
  const resource = testDataResource("messaging.Message", { orderFields: ["sent_at", "thread__title__text"] });
  const model: ModelMetadata = schemaFieldMetadataFromDataResources([resource]).labels![resource.modelLabel]!;
  const bodies: { query: string; variables: Record<string, unknown> }[] = [];
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as (typeof bodies)[number];
    bodies.push(body);
    if (body.variables.order_by && "unsupported" in (body.variables.order_by as object)) {
      return new Response(JSON.stringify({ errors: [{ message: "Field 'unsupported' is not defined by type 'messages_order_by'." }] }), { headers: { "content-type": "application/json" } });
    }
    return new Response(JSON.stringify({ data: { messages: [{ id: "message-1", sent_at: "2026-09-05", thread: { title: { text: "Thread" } }, sender: { party: { display_name: "Sender" } } }], messages_aggregate: { aggregate: { count: 1 } } } }), { headers: { "content-type": "application/json" } });
  });
  const provider = createAngeeHasuraDataProvider({ url: "https://fixture.invalid/graphql", fetch, auth: (nativeFetch) => nativeFetch });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  let surface!: ResourceViewSurface;
  function Probe() {
    const view = useResourceView();
    surface = useResourceViewSurface({ resource: resource.modelLabel, modelMetadata: model, resourceView: view, columns: [
      { field: "thread.title.text", header: "Thread" }, { field: "sender.party.display_name", header: "Sender", sortable: true },
      { field: "sent_at", header: "Sent" }, { field: "title", header: "Title" },
    ] });
    return <>{surface.table.getHeaderGroups()[0]!.headers.map((header) => <div key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</div>)}<output>{surface.list.error?.message}</output></>;
  }
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}><ToastProvider><ResourceViewProvider scope="local" initialState={{ sorting: [{ id: initialSort, desc: true }] }}><Probe /></ResourceViewProvider></ToastProvider></Refine>);
  await waitFor(() => expect(bodies.length).toBeGreaterThan(0));
  return { bodies, surface: () => surface };
}

test("native Table keeps dotted display IDs while Refine sends declared flat order fields", async () => {
  const f = await fixture();
  expect(f.bodies[0]?.variables.order_by).toEqual({ thread__title__text: "desc" });
  expect(f.surface().table.getColumn("thread.title.text")?.getIsSorted()).toBe("desc");
  expect(f.surface().table.getColumn("sender.party.display_name")?.getCanSort()).toBe(false);
  expect(f.surface().table.getColumn("title")?.getCanSort()).toBe(false);
  expect(screen.getByText("Sender").closest("button")).toBeNull();
  expect(screen.getByText("Title").closest("button")).toBeNull();
  expect(screen.getByText("Thread").closest("button")).not.toBeNull();
  await act(async () => fireEvent.click(screen.getByText("Sent")));
  await waitFor(() => expect(f.bodies.at(-1)?.variables.order_by).toEqual({ sent_at: "asc" }));
  await act(async () => f.surface().table.getColumn("thread.title.text")!.toggleSorting(false));
  await waitFor(() => expect(f.bodies.at(-1)?.variables.order_by).toEqual({ thread__title__text: "asc" }));
  expect(f.bodies.some(({ variables }) => variables.order_by && "thread" in (variables.order_by as object))).toBe(false);
});

test("unknown active URL sorts reach native query errors rather than disappearing or falling back", async () => {
  const f = await fixture("unsupported");
  expect(f.bodies[0]?.variables.order_by).toEqual({ unsupported: "desc" });
  await waitFor(() => expect(f.surface().list.error?.message).toContain("unsupported"));
  expect(screen.getByText("Sent").closest("button")).not.toBeNull();
  await act(async () => fireEvent.click(screen.getByText("Sent")));
  await waitFor(() => expect(f.surface().list.error).toBeNull());
  expect(f.bodies.at(-1)?.variables.order_by).toEqual({ sent_at: "asc" });
});
