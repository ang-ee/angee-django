// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider, type GetListParams } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { ModelMetadataProvider, ResourceQuery, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { ToastProvider } from "../../feedback";
import { ListView } from "./ListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { resourceViewFavoritesFromUnknown } from "./model/favorites";

const resource = testDataResource("notes.Note", {
  roots: { aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, title: { scalar: "String" } } }).contract,
  fields: ["id", "title"].map((name) => ({ name, kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
});
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); });

function fixture({ rowModel = "server" }: { rowModel?: "server" | "client" } = {}) {
  const activeResource = { ...resource, rowModel };
  const getList = vi.fn(async (_params: GetListParams) => ({ data: [{ id: "1", title: "Kept note" }], total: 1 }));
  const provider = { getApiUrl: () => "test://query", getList, getOne: vi.fn(), create: vi.fn(),
    update: vi.fn(), deleteOne: vi.fn() } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  let view!: ResourceViewContextValue;
  function Records() {
    view = useResourceView();
    return <ListView resource={resource.modelLabel} columns={[{ field: "title", header: "Title" }]} />;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", component: () =>
    <ResourceViewProvider resource={resource.modelLabel}><Records /></ResourceViewProvider> });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([activeResource])]} dataProvider={{ default: provider, console: provider }}
    options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
    <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([activeResource])}><ToastProvider>
      <RouterProvider router={router} />
    </ToastProvider></ModelMetadataProvider>
  </Refine>);
  return { get view() { return view; }, getList, router };
}

test("a routed invalid saved operator blocks reads until the user resets the query", async () => {
  const f = fixture();
  expect(await screen.findByText("Kept note")).toBeTruthy();
  const requests = f.getList.mock.calls.length;
  const favorite = resourceViewFavoritesFromUnknown([{ id: "old", label: "Old search", filter: { title: { sqid: "legacy" } } }])[0]!;
  act(() => f.view.applyFavorite(favorite));
  expect(await screen.findByText(/sqid/)).toBeTruthy();
  expect(screen.queryByText("Kept note")).toBeNull();
  expect(f.getList).toHaveBeenCalledTimes(requests);
  expect(f.router.state.location.search).toEqual({});

  fireEvent.click(screen.getByRole("button", { name: "Reset filters, sorting and grouping" }));
  expect(await screen.findByText("Kept note")).toBeTruthy();
  expect(f.view.state.queryError).toBeFalsy();
  expect(f.view.state.filter).toEqual({});
});

test("two immediate route updates retain both the first filter and the following sort", async () => {
  const f = fixture();
  expect(await screen.findByText("Kept note")).toBeTruthy();
  act(() => {
    f.view.setFilter({ title: { iContains: "Kept" } });
    f.view.setSorting([{ id: "title", desc: true }]);
  });
  await waitFor(() => expect(f.view.state.sorting).toEqual([{ id: "title", desc: true }]));
  expect(f.view.state.filter).toEqual({ title: { iContains: "Kept" } });
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].meta?.gqlVariables).toMatchObject({
    where: { title: { _ilike: "%Kept%" } }, order_by: { title: "desc" },
  }));
});

test.each(["list", "board"] as const)("a client-only axis in a server %s gives a repairable query error before group reads", async (view) => {
  const f = fixture();
  expect(await screen.findByText("Kept note")).toBeTruthy();
  act(() => f.view.setView(view));
  await waitFor(() => expect(f.view.state.view).toBe(view));
  const requests = f.getList.mock.calls.length;
  act(() => f.view.setGroup({ field: "title" }));
  expect(await screen.findByText(/does not support server grouping/)).toBeTruthy();
  expect(screen.queryByText("Kept note")).toBeNull();
  expect(f.getList).toHaveBeenCalledTimes(requests);
  fireEvent.click(screen.getByRole("button", { name: "Reset filters, sorting and grouping" }));
  expect(await screen.findByText("Kept note")).toBeTruthy();
});

test.each(["server", "client"] as const)("list and board group choices follow the resource's %s grouping capability", async (rowModel) => {
  const f = fixture({ rowModel });
  expect(await screen.findByText("Kept note")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Filter and group" }));
  expect(Boolean(screen.queryByRole("button", { name: "Title" }))).toBe(rowModel === "client");

  act(() => f.view.setView("board"));
  await waitFor(() => expect(f.view.state.view).toBe("board"));
  const trigger = await screen.findByRole("button", { name: "Filter and group" });
  if (trigger.getAttribute("aria-expanded") !== "true") fireEvent.click(trigger);
  expect(Boolean(screen.queryByRole("button", { name: "Title" }))).toBe(rowModel === "client");
});
