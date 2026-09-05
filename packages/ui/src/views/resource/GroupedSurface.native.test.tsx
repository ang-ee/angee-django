// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider, type GetListParams } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";

import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { useGroupedResourceViewSurface, type GroupedResourceViewSurface } from "./resource-view-surface";
import { ListHeaderCell } from "./resource-view-list-body";

const resource = testDataResource("notes.Note", {
  roots: { groups: "notes_groups" },
  groupByFields: ["status", "title"],
  orderFields: ["title", "status"],
  fields: ["id", "title", "status"].map((name) => ({ name, kind: "scalar", scalar: "String", readable: true,
    filterable: true, sortable: name !== "id", aggregatable: false, groupable: true,
    creatable: false, updatable: false, requiredOnCreate: false })),
  groupDimensions: ["status", "title"].map((field) => ({ field, input: field, key: field,
    kind: "column", scalar: "String", filter: { kind: "equality", field, valueKey: field } })),
});
const metadata = schemaFieldMetadataFromDataResources([resource]);
const model = metadata.labels![resource.modelLabel]!;
const initialState = { pageSize: 20, groupStack: [{ field: "status" }, { field: "title" }] };
const columns = [{ field: "title", header: "Title" }, { field: "status", header: "Status" }];
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); });

function fixture({ failedRoot = false, page = 1 }: { failedRoot?: boolean; page?: number } = {}) {
  let view!: ResourceViewContextValue;
  let surface!: GroupedResourceViewSurface<Row>;
  let show!: React.Dispatch<React.SetStateAction<boolean>>;
  let delayPage: number | null = null;
  let rowTotal = 120;
  let resolvePage!: (value: { data: Row[]; total: number }) => void;
  const lifecycle = { mounts: 0, unmounts: 0 };
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta!.gqlVariables as { group_by: { field: string }[]; limit: number; offset: number };
    const field = variables.group_by[0]!.field;
    if (failedRoot && field === "status") throw new Error("Group query unavailable");
    return { data: { notes_groups: [{ key: { [field]: field === "status" ? "active" : `Title ${variables.offset}` },
      aggregate: { count: rowTotal } }], totalCount: field === "status" ? 1 : 130 } };
  });
  const getList = vi.fn(async ({ pagination }: GetListParams) => {
    if (pagination?.currentPage === delayPage) {
      return new Promise<{ data: Row[]; total: number }>((resolve) => { resolvePage = resolve; });
    }
    return { data: [{ id: `page-${pagination?.currentPage}`, title: "Native row", status: "active" }], total: rowTotal };
  });
  const provider = { getApiUrl: () => "test://groups", custom, getList, getOne: vi.fn(),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  function Surface() {
    const resourceView = useResourceView();
    surface = useGroupedResourceViewSurface({ resource: resource.modelLabel, modelMetadata: model,
      columns, resourceView });
    React.useEffect(() => { lifecycle.mounts++; return () => { lifecycle.unmounts++; }; }, []);
    return <table><thead>{surface.table.getHeaderGroups().map((group) => <tr key={group.id}>
      {group.headers.map((header) => <ListHeaderCell key={header.id} header={header} resourceView={resourceView} />)}
    </tr>)}</thead></table>;
  }
  function Parent() {
    view = useResourceView();
    const [visible, setVisible] = React.useState(true);
    show = setVisible;
    return visible ? <Surface /> : <span>Record form owns this region</span>;
  }
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
      <ModelMetadataProvider metadata={metadata}>
        <OperationDocumentsProvider documents={{ console: { groups: { "notes.Note": "query Groups { notes_groups { key } totalCount }" } } }}>
          <ResourceViewProvider resource={resource.modelLabel} scope="local" initialState={{ ...initialState, page }}><Parent /></ResourceViewProvider>
        </OperationDocumentsProvider>
      </ModelMetadataProvider>
    </Refine>,
  );
  const header = (depth: number) => surface.groupedItems.filter((item) => item.kind === "groupHeader").find((item) => item.depth === depth);
  return { get view() { return view; }, get surface() { return surface; }, header, show: (visible: boolean) => show(visible),
    custom, getList, lifecycle, refreshCount: async (count: number) => { rowTotal = count; await client.invalidateQueries(); }, delay: (page: number) => { delayPage = page; }, release: () => resolvePage({ data: [{ id: "obsolete", title: "Old filter row" }], total: 120 }) };
}

test("native subgroup and leaf page sizes, page 2 and expansion survive an actual server-surface unmount", async () => {
  const f = fixture();
  await waitFor(() => expect(f.header(0)?.kind === "groupHeader" && f.header(0)?.pager).toBeTruthy());
  const root = f.header(0)!;
  if (root.kind !== "groupHeader") throw new Error("missing root header");
  act(() => f.surface.setScopePageSize(root.pager!.pageKey, 50));
  await waitFor(() => expect(f.header(0)?.kind === "groupHeader" && f.header(0)?.pager?.pageSize).toBe(50));
  act(() => f.surface.setScopePage(root.pager!.pageKey, 2));
  await waitFor(() => expect(f.header(1)?.kind === "groupHeader" && f.header(1)?.label).toBe("Title 50"));
  const leaf = f.header(1)!;
  if (leaf.kind !== "groupHeader") throw new Error("missing leaf header");
  act(() => f.surface.toggleGroup(leaf.bucketKey));
  await waitFor(() => expect(f.header(1)?.kind === "groupHeader" && f.header(1)?.pager).toBeTruthy());
  const leafKey = leaf.bucketKey;
  act(() => f.surface.setScopePageSize(leafKey, 50));
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].pagination?.pageSize).toBe(50));
  act(() => f.surface.setScopePage(leafKey, 2));
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].pagination?.currentPage).toBe(2));
  await waitFor(() => expect(f.surface.groupedItems.some((item) => item.kind === "record" && item.nav.page === 2)).toBe(true));

  act(() => f.show(false));
  expect(f.lifecycle.unmounts).toBe(1);
  expect(f.view.paginationByScope[leafKey]).toEqual({ pageIndex: 1, pageSize: 50 });
  act(() => f.show(true));
  await waitFor(() => expect(f.header(1)?.kind === "groupHeader" && f.header(1)?.pager?.page).toBe(2));
  expect(f.lifecycle.mounts).toBe(2);
  const restoredRoot = f.header(0)!;
  const restoredLeaf = f.header(1)!;
  if (restoredRoot.kind !== "groupHeader" || restoredLeaf.kind !== "groupHeader") throw new Error("missing restored headers");
  expect(restoredRoot.pager).toMatchObject({ page: 2, pageSize: 50 });
  expect(restoredLeaf).toMatchObject({ expanded: true, pager: { page: 2, pageSize: 50 } });

  // A corrected leaf page remains corrected when the live count grows again.
  await act(async () => f.refreshCount(40));
  await waitFor(() => expect(f.view.paginationByScope[leafKey]).toEqual({ pageIndex: 0, pageSize: 50 }));
  await act(async () => f.refreshCount(120));
  await waitFor(() => expect(f.header(1)?.pager?.page).toBe(1));
  expect(f.view.paginationByScope[leafKey]).toEqual({ pageIndex: 0, pageSize: 50 });

  // A late old-filter page must not restore its interaction state after reset.
  f.delay(3);
  act(() => f.surface.setScopePage(leafKey, 3));
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].pagination?.currentPage).toBe(3));
  act(() => f.view.setFilter({ title: { iContains: "changed" } }));
  expect(f.view.paginationByScope).toEqual({});
  await act(async () => f.release());
  await waitFor(() => expect(f.header(1)?.kind === "groupHeader" && f.header(1)?.label).toBe("Title 0"));
  expect(f.view.paginationByScope).toEqual({});
  expect(f.surface.groupedItems.some((item) => item.kind === "record" && item.row.original.id === "obsolete")).toBe(false);
});

test("a native grouped header sort updates the leaf query and preserves the parent filter", async () => {
  const f = fixture();
  act(() => f.view.setFilter({ title: { iContains: "kept" } }));
  await waitFor(() => expect(f.header(1)).toBeDefined());
  act(() => f.surface.toggleGroup(f.header(1)!.bucketKey));
  await waitFor(() => expect(f.getList).toHaveBeenCalled());
  const leafKey = f.header(1)!.bucketKey;
  act(() => f.surface.setScopePageSize(leafKey, 50));
  act(() => f.surface.setScopePage(leafKey, 2));
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].pagination).toMatchObject({ currentPage: 2, pageSize: 50 }));
  fireEvent.click(screen.getByRole("button", { name: "Sort Title (not sorted)" }));
  await waitFor(() => expect(f.view.state.sorting).toEqual([{ id: "title", desc: false }]));
  // Sorting resets row windows while keeping the same group tree expanded.
  await waitFor(() => expect(f.header(1)?.expanded).toBe(true));
  expect(f.view.paginationByScope[leafKey]).toEqual({ pageIndex: 0, pageSize: 50 });
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].sorters).toEqual([{ field: "title", order: "asc" }]));
  expect(f.view.state.filter).toEqual({ title: { iContains: "kept" } });
  expect(f.getList.mock.calls.at(-1)?.[0].filters).toEqual(expect.arrayContaining([
    expect.objectContaining({ field: "title", operator: "contains", value: "kept" }),
  ]));
  expect(screen.getByRole("columnheader", { name: "Title" }).getAttribute("aria-sort")).toBe("ascending");
});


test("grouped shift-click replaces the sort using the same single-sort contract as flat views", async () => {
  const f = fixture();
  await waitFor(() => expect(f.header(1)).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "Sort Title (not sorted)" }));
  await waitFor(() => expect(f.view.state.sorting).toEqual([{ id: "title", desc: false }]));
  fireEvent.click(screen.getByRole("button", { name: "Sort Status (not sorted)" }), { shiftKey: true });
  await waitFor(() => expect(f.view.state.sorting).toEqual([{ id: "status", desc: false }]));
});

test("a failed root group query preserves page and unknown total instead of querying page one", async () => {
  const f = fixture({ failedRoot: true, page: 3 });
  await waitFor(() => expect(f.surface.list.error?.message).toBe("Group query unavailable"));
  expect(f.surface.list.total).toBeUndefined();
  expect(f.view.state.pagination.pageIndex).toBe(2);
  expect(f.custom.mock.calls.every(([request]) =>
    (request.meta?.gqlVariables as { offset: number }).offset === 40)).toBe(true);
});
