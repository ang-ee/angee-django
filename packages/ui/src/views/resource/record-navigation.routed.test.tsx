// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider, type GetListParams } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { routeSearchString } from "../../runtime/route-href";
import { RoutedRecordController } from "./resource-routing";
import { useListRecordNavigation } from "./use-list-record-navigation";
import { RecordPager } from "./RecordPager";
import type { ResourceRecordController } from "./ResourceList";
import type { ListViewNavigationScope } from "./resource-view-surface";

const resource = testDataResource("notes.Note");
const scope: ListViewNavigationScope = { filter: { title: { iContains: "draft" }, updated_at: { gte: "2026-09-01", lt: "2026-10-01" } }, order: { updated_at: "DESC" }, page: 1, pageSize: 2 };
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });
async function fixture(initialPath = "/notes?group=updated_at%3Amonth&page=3&keep=external") {
  const getList = vi.fn(async (params: GetListParams) => ({ data: params.pagination?.currentPage === 2 ? [{ id: "c" }] : [{ id: "a" }, { id: "b" }], total: 3 }));
  const provider = { getApiUrl: () => "test://notes", getList, getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  function Body({ controller }: { controller: ResourceRecordController }) {
    const navigation = useListRecordNavigation({ resource: "notes.Note", recordId: controller.recordId, navigationScope: controller.navigationScope, onSelect: controller.onSelect });
    return <>
      <output data-testid="record">{controller.recordId ?? "collection"}</output>
      <a href={controller.rowHref?.({ id: "b" }, scope)} onClick={(event) => { event.preventDefault(); controller.onSelect?.("b", scope); }}>Open second</a>
      <button onClick={controller.onClose}>Close</button>
      {navigation.navigation ? <RecordPager navigation={navigation.navigation} /> : null}
    </>;
  }
  const root = createRootRoute({ component: () => <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}><RoutedRecordController resource="notes.Note" newRecordId="new">{(controller) => <Body controller={controller} />}</RoutedRecordController></ModelMetadataProvider></Refine> });
  const collection = createRoute({ getParentRoute: () => root, path: "notes" });
  const record = createRoute({ getParentRoute: () => collection, path: "$id" });
  const history = createMemoryHistory({ initialEntries: [initialPath] });
  const router = createRouter({ routeTree: root.addChildren([collection.addChildren([record])]), history, parseSearch: (value) => Object.fromEntries(new URLSearchParams(value)), stringifySearch: (value) => { const query = routeSearchString(value); return query ? `?${query}` : ""; } });
  render(<RouterProvider router={router} />);
  await screen.findByText("Open second");
  return { router, getList, client };
}

test("copied record links restore native query navigation, page edges preserve the parent, close removes only context", async () => {
  const first = await fixture();
  const copied = screen.getByText("Open second").getAttribute("href")!;
  expect(copied).toContain("recordNav=");
  expect(copied).not.toContain("rows");
  expect(first.getList).not.toHaveBeenCalled();
  cleanup();
  const f = await fixture(copied);
  await waitFor(() => expect(screen.getByRole("navigation").textContent).toContain("2 / 3"));
  expect(screen.getByTestId("record").textContent).toBe("b");
  fireEvent.click(screen.getByRole("button", { name: "Next record" }));
  await waitFor(() => expect(screen.getByTestId("record").textContent).toBe("c"));
  expect(f.router.state.location.search).toMatchObject({ group: "updated_at:month", page: "3", keep: "external" });
  expect(f.getList.mock.calls.at(-1)?.[0]).toMatchObject({ pagination: { currentPage: 2, pageSize: 2 }, sorters: [{ field: "updated_at", order: "desc" }], filters: [{ field: "title", operator: "contains", value: "draft" }, { field: "updated_at", operator: "gte", value: "2026-09-01" }, { field: "updated_at", operator: "lt", value: "2026-10-01" }] });
  await act(async () => { fireEvent.click(screen.getByText("Close")); });
  await waitFor(() => expect(f.router.state.location.pathname).toBe("/notes"));
  expect(f.router.state.location.search).toEqual({ group: "updated_at:month", page: "3", keep: "external" });
});

test.each(["", "?recordNav=broken", "?recordNav=%7B%7D"])("a context-free or malformed routed record has no list fallback: %s", async (query) => {
  const f = await fixture(`/notes/b${query}`);
  expect(screen.getByTestId("record").textContent).toBe("b");
  expect(screen.queryByRole("navigation")).toBeNull();
  expect(f.getList).not.toHaveBeenCalled();
});
