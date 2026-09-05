// @vitest-environment happy-dom
import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine, type DataProvider, type GetListParams } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider, tanStackRouterProvider } from "@angee/refine";
import { Breadcrumb, BreadcrumbLabelProvider } from "@angee/ui/chrome/index";
import { ModalsHost, ToastProvider } from "@angee/ui/feedback/index";
import { ResourceList } from "@angee/ui/views/ResourceList";
import { afterEach, expect, test, vi } from "vitest";
import { createAddonRouteNodes } from "../route-tree";
import { parseFlatSearch, stringifyFlatSearch } from "../create-app";
import type { BaseAddonRoute } from "../define-base-addon";

const resource = testDataResource("notes.Note", {
  roots: { groups: "notes_groups" }, recordRepresentation: "title",
  groupByFields: ["updated_at"], orderFields: ["title", "updated_at"],
  fields: ["id", "title", "updated_at"].map((name) => ({ name, kind: "scalar", scalar: name === "updated_at" ? "DateTime" : "String",
    readable: true, filterable: true, sortable: name !== "id", aggregatable: false, groupable: name === "updated_at",
    creatable: false, updatable: name === "title", requiredOnCreate: false })),
  groupDimensions: [{ field: "updated_at", input: "UPDATED_AT", key: "updated_at", kind: "column", scalar: "DateTime",
    filter: { kind: "equality", field: "updated_at", valueKey: "updated_at" },
    extractions: [{ name: "month", input: "MONTH", key: "updated_at_month", rangeKey: "updated_at_month_range",
      filter: { kind: "range", field: "updated_at", valueKey: "updated_at_month", rangeKey: "updated_at_month_range" } }] }],
});
const rows = Array.from({ length: 292 }, (_, index) => ({ id: `note-${index + 1}`, title: `Note ${index + 1}`, updated_at: "2021-01-12T00:00:00Z" }));
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); });

async function fixture() {
  const lifecycle = { mounts: 0, unmounts: 0 };
  const getList = vi.fn(async ({ pagination }: GetListParams) => {
    const offset = ((pagination?.currentPage ?? 1) - 1) * (pagination?.pageSize ?? 20);
    return { data: rows.slice(offset, offset + (pagination?.pageSize ?? 20)), total: rows.length };
  });
  const provider = { getApiUrl: () => "test://notes", getList,
    getOne: vi.fn(async ({ id }: { id: string }) => ({ data: rows.find((row) => row.id === id)! })),
    custom: vi.fn(async () => ({ data: { notes_groups: [{ key: { updated_at_month: "2021-01-01T00:00:00Z",
      updated_at_month_range: { from: "2021-01-01T00:00:00Z", to: "2021-02-01T00:00:00Z" } }, aggregate: { count: rows.length } }], totalCount: 1 } })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  function NotesPage() {
    React.useEffect(() => { lifecycle.mounts++; return () => { lifecycle.unmounts++; }; }, []);
    return <ResourceList resource="notes.Note" routed placement="inline" hideCreate
      defaultGroups={{ list: { field: "updated_at", granularity: "month" } }}
      columns={[{ field: "title" }, { field: "updated_at" }]} formFields={[{ name: "title", widget: "text", title: true }]} />;
  }
  const root = createRootRoute({ component: () => <Refine routerProvider={tanStackRouterProvider}
    resources={refineResourcesFromDataResources([resource]).map((entry) => ({ ...entry, list: "/notes", show: "/notes/:id", meta: { ...entry.meta, label: "Notes" } }))}
    dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
    <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
      <OperationDocumentsProvider documents={{ console: { groups: { "notes.Note": "query Groups { notes_groups { key } totalCount }" } } }}>
        <ModalsHost><ToastProvider><BreadcrumbLabelProvider><Breadcrumb /><Outlet /></BreadcrumbLabelProvider></ToastProvider></ModalsHost>
      </OperationDocumentsProvider>
    </ModelMetadataProvider>
  </Refine> });
  const routes: BaseAddonRoute[] = [{ name: "notes", path: "/notes", component: NotesPage }, { name: "notes.record", parent: "notes", path: "/notes/$id" }];
  createAddonRouteNodes({ routes, routesByName: new Map(routes.map((route) => [route.name, route])), layoutRoutes: new Map([["console", root]]) });
  const history = createMemoryHistory({ initialEntries: [`/notes?pageSize=20&sort=updated_at%3Adesc&keep=external&filter=${encodeURIComponent(JSON.stringify({ title: { iContains: "Note" } }))}`] });
  const router = createRouter({ routeTree: root, history, parseSearch: parseFlatSearch, stringifySearch: stringifyFlatSearch });
  render(<RouterProvider router={router} />);
  await screen.findByText("January 2021");
  return { router, lifecycle, getList };
}

test("generated native record routes return through Breadcrumb to the same group page and parent search", async () => {
  const f = await fixture();
  fireEvent.click(screen.getByRole("button", { name: "January 2021 records 1-20 / 292" }));
  fireEvent.click(screen.getByRole("button", { name: "50" }));
  fireEvent.keyDown(document.body, { key: "Escape" });
  await waitFor(() => expect(screen.getByText("1-50 / 292")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Next January 2021 records" }));
  await screen.findByText("51-100 / 292");
  const parentSearch = f.router.state.location.search;
  fireEvent.click(screen.getByRole("link", { name: "Open Note 51" }));
  await waitFor(() => expect(f.router.state.location.pathname).toBe("/notes/note-51"));
  await screen.findByRole("button", { name: "Next record" });
  fireEvent.click(screen.getByRole("button", { name: "Next record" }));
  await waitFor(() => expect(f.router.state.location.pathname).toBe("/notes/note-52"));
  const breadcrumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
  await act(async () => fireEvent.click(within(breadcrumbs).getByRole("link", { name: "Notes" })));
  await waitFor(() => expect(f.router.state.location.pathname).toBe("/notes"));
  expect(f.router.state.location.search).toEqual(parentSearch);
  expect(f.lifecycle).toEqual({ mounts: 1, unmounts: 0 });
  await screen.findByText("51-100 / 292");
  expect(f.getList.mock.calls.at(-1)?.[0].pagination).toMatchObject({ currentPage: 2, pageSize: 50 });
  expect(f.getList.mock.calls.at(-1)?.[0].filters).toContainEqual({ field: "title", operator: "contains", value: "Note" });
});
