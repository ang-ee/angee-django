// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { ResourceQuery, schemaFieldMetadataFromDataResources, type GroupSpec, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";

import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { useGroupedResourceViewSurface, type GroupedResourceViewSurface } from "./resource-view-surface";
import { ListHeaderCell } from "./resource-view-list-body";
import { ListView } from "./ListView";
import { ToastProvider } from "../../feedback";

const contract = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" },
} }).contract;
for (const field of ["status", "title"]) {
  contract.axes[field]!.server = { input: field, key: field };
  contract.axes[field]!.drill = { kind: "value", field, valueKey: field, nullMode: "isNull", valueMap: [] };
}
const resource = testDataResource("notes.Note", {
  roots: { groups: "notes_groups", aggregate: "notes_aggregate" }, typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" }, query: contract,
  fields: ["id", "title", "status"].map((name) => ({ name, kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
});
const initialState = { pageSize: 20, groupStack: [{ field: "status" }, { field: "title" }] };
const columns = [{ field: "title", header: "Title" }, { field: "status", header: "Status" }];
const { Provider, clients, clearClients } = createUiTestProviders({
  apiUrl: "test://groups",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

function fixture({ failedRoot = false, page = 1, summaryOnly = false }: { failedRoot?: boolean; page?: number; summaryOnly?: boolean } = {}) {
  const activeResource = summaryOnly ? { ...resource, query: { ...contract, axes: { status: {
    ...contract.axes.status!, identityPath: null, paths: [], drill: null,
  } } } } : resource;
  const activeMetadata = schemaFieldMetadataFromDataResources([activeResource]);
  const activeModel = activeMetadata.labels[activeResource.modelLabel]!;
  let view!: ResourceViewContextValue;
  let surface!: GroupedResourceViewSurface<Row>;
  let show!: React.Dispatch<React.SetStateAction<boolean>>;
  let delayPage: number | null = null;
  let rowTotal = 120;
  let resolvePage!: (value: { data: Row[]; total: number }) => void;
  const lifecycle = { mounts: 0, unmounts: 0 };
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta!.gqlVariables as {
      group_by: { field: string }[]; limit: number; offset: number; with_rows?: boolean; rows_limit?: number;
    };
    const field = variables.group_by[0]!.field;
    if (failedRoot && field === "status") throw new Error("Group query unavailable");
    // The leaf groups read carries each group's first page.
    const carried = variables.with_rows
      ? { rows: Array.from({ length: Math.min(rowTotal, variables.rows_limit ?? 0) }, (_, index) => ({
          id: `carried-${index}`, title: "Native row", status: "active" })) }
      : {};
    return { data: { notes_groups: [{ key: { [field]: field === "status" ? "active" : `Title ${variables.offset}` },
      aggregate: { count: rowTotal }, ...carried }], totalCount: field === "status" ? 1 : 130 } };
  });
  const getList = vi.fn(async ({ pagination }: GetListParams) => {
    if (pagination?.currentPage === delayPage) {
      return new Promise<{ data: Row[]; total: number }>((resolve) => { resolvePage = resolve; });
    }
    return { data: [{ id: `page-${pagination?.currentPage}`, title: "Native row", status: "active" }], total: rowTotal };
  });
  function Surface() {
    const resourceView = useResourceView();
    surface = useGroupedResourceViewSurface({ resource: resource.modelLabel, modelMetadata: activeModel,
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
    <Provider metadata={activeMetadata} dataProvider={{ custom, getList }}>
      <OperationDocumentsProvider documents={{ console: { groups: { "notes.Note": "query Groups { notes_groups { key } totalCount }" } } }}>
        <ResourceViewProvider resource={resource.modelLabel} scope="local" initialState={{ ...initialState, page, ...(summaryOnly ? { groupStack: [{ field: "status" }] } : {}) }}><Parent /></ResourceViewProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  const client = clients.at(-1)!;
  const header = (depth: number) => surface.groupedItems.filter((item) => item.kind === "groupHeader").find((item) => item.depth === depth);
  return { get view() { return view; }, get surface() { return surface; }, header, show: (visible: boolean) => show(visible),
    custom, getList, lifecycle, refreshCount: async (count: number) => { rowTotal = count; await client.invalidateQueries(); }, delay: (page: number) => { delayPage = page; }, release: () => resolvePage({ data: [{ id: "obsolete", title: "Old filter row" }], total: 120 }) };
}

test("server summary axes without a row identity render buckets without leaf requests", async () => {
  const f = fixture({ summaryOnly: true });
  await waitFor(() => expect(f.header(0)?.label).toBe("active"));
  expect(f.surface.list.error).toBeNull();
  expect(f.getList).not.toHaveBeenCalled();
  expect(f.surface.requestedFields).toEqual(["id", "title", "status"]);
});

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

test("native grouped root and leaf pagination clamp oversized page sizes", async () => {
  const f = fixture();
  await waitFor(() => expect(f.header(0)?.kind === "groupHeader" && f.header(0)?.pager).toBeTruthy());

  act(() => f.view.setPageSize(500));
  await waitFor(() => expect(f.view.state.pagination.pageSize).toBe(100));
  expect((f.custom.mock.calls.at(-1)?.[0].meta?.gqlVariables as { limit: number }).limit).toBe(100);

  const root = f.header(0)!;
  if (root.kind !== "groupHeader") throw new Error("missing root header");
  act(() => f.surface.setScopePageSize(root.pager!.pageKey, 500));
  await waitFor(() => expect(f.header(0)?.pager?.pageSize).toBe(100));

  const leaf = f.header(1)!;
  if (leaf.kind !== "groupHeader") throw new Error("missing leaf header");
  act(() => f.surface.toggleGroup(leaf.bucketKey));
  await waitFor(() => expect(f.header(1)?.pager).toBeTruthy());
  act(() => f.surface.setScopePageSize(leaf.bucketKey, 500));
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].pagination?.pageSize).toBe(100));
  expect(f.view.paginationByScope[leaf.bucketKey]).toEqual({ pageIndex: 0, pageSize: 100 });
  expect(f.surface.groupedItems.find((item) => item.kind === "record")?.nav.pageSize).toBe(100);
});

test("a native grouped header sort updates the leaf query and preserves the parent filter", async () => {
  const f = fixture();
  act(() => f.view.setFilter({ title: { iContains: "kept" } }));
  await waitFor(() => expect(f.header(1)).toBeDefined());
  act(() => f.surface.toggleGroup(f.header(1)!.bucketKey));
  // Page 1 rides on the groups read; only a resized or later page reads rows on its own.
  await waitFor(() => expect(f.surface.groupedItems.some((item) => item.kind === "record")).toBe(true));
  expect(f.getList).not.toHaveBeenCalled();
  const leafKey = f.header(1)!.bucketKey;
  act(() => f.surface.setScopePageSize(leafKey, 50));
  act(() => f.surface.setScopePage(leafKey, 2));
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].pagination).toMatchObject({ currentPage: 2, pageSize: 50 }));
  fireEvent.click(screen.getByRole("button", { name: "Sort Title (not sorted)" }));
  await waitFor(() => expect(f.view.state.sorting).toEqual([{ id: "title", desc: false }]));
  // Sorting resets row windows while keeping the same group tree expanded.
  await waitFor(() => expect(f.header(1)?.expanded).toBe(true));
  expect(f.view.paginationByScope[leafKey]).toEqual({ pageIndex: 0, pageSize: 50 });
  await waitFor(() => expect(f.getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.order_by).toEqual({ title: "asc" }));
  expect(f.view.state.filter).toEqual({ title: { iContains: "kept" } });
  expect(JSON.stringify(f.getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where)).toContain('"_ilike":"%kept%"');
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


test("the rendered ListView displays a failed root group query instead of empty records", async () => {
  const custom = vi.fn(async () => { throw new Error("Group query unavailable"); });
  const getList = vi.fn(async () => ({ data: [], total: 0 }));
  render(
    <Provider resources={[resource]} apiUrl="test://root-error" dataProvider={{ custom, getList }}
      queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <OperationDocumentsProvider documents={{ console: { groups: { "notes.Note": "query Groups { notes_groups { key } totalCount }" } } }}>
        <ToastProvider><ListView resource={resource.modelLabel} columns={columns} defaultGroup={{ field: "title" }}
          scope="local" emptyContent="There are no notes" /></ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  expect(await screen.findByText("Group query unavailable")).toBeTruthy();
  expect(screen.queryByText("There are no notes")).toBeNull();
  expect(getList).not.toHaveBeenCalled();
});


const taskContract = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" },
  owner: { scalar: "String" }, kind: { scalar: "String" },
} }).contract;
for (const field of ["status", "owner", "kind"]) {
  taskContract.axes[field]!.server = { input: field, key: field };
  taskContract.axes[field]!.drill = { kind: "value", field, valueKey: field, nullMode: "isNull", valueMap: [] };
}
const taskResource = testDataResource("notes.Task", {
  roots: { groups: "tasks_groups", aggregate: "tasks_aggregate" }, typeNames: { filter: "TaskBoolExp", order: "TaskOrderBy" },
  query: taskContract, recordRepresentation: "title",
  fields: ["id", "title", "status", "owner", "kind"].map((name) => ({ name, kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
});
const tasks: readonly Row[] = [
  { id: "t1", title: "Task 1", status: "active", owner: "Ada", kind: "Same" },
  { id: "t2", title: "Task 2", status: "active", owner: "Bo", kind: "Same" },
  { id: "t3", title: "Task 3", status: "closed", owner: "Ada", kind: "Other" },
  { id: "t4", title: "Task 4", status: null, owner: "Ada", kind: "Other" },
  { id: "t5", title: "Task 5", status: "", owner: "Ada", kind: "Other" },
];
/** The few Hasura predicates the grouped list sends: equality, inequality, null tests, conjunction. */
function matchesWhere(row: Row, where: Record<string, unknown> | undefined): boolean {
  return Object.entries(where ?? {}).every(([key, condition]) => key === "_and"
    ? (condition as Record<string, unknown>[]).every((branch) => matchesWhere(row, branch))
    : Object.entries(condition as Record<string, unknown>).every(([operator, operand]) =>
      operator === "_eq" ? row[key] === operand
        : operator === "_neq" ? row[key] !== operand
          : operator === "_is_null" ? (row[key] == null) === operand : false));
}

/** A rendered grouped Task list whose page's bulk actions record the selectedGroups they receive. */
function selectedGroupsFixture(initialState: Record<string, unknown>, {
  stale, columns = [{ field: "title", header: "Title" }],
}: {
  /** A stale page: the server also lists this row in another group. */
  stale?: { id: string; group: string };
  columns?: readonly { field: string; header: string; aggregate?: "count" }[];
} = {}) {
  const records = tasks.map((row) => ({ ...row }));
  const seen: unknown[] = [];
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = meta!.gqlVariables as {
      group_by?: { field: string }[]; where?: Record<string, unknown>; having?: { count_gt: number }; rows_limit?: number;
    };
    const rows = records.filter((row) => matchesWhere(row, variables.where));
    if (!variables.group_by) return { data: { tasks_aggregate: { aggregate: { count: rows.length } } } };
    const field = variables.group_by[0]!.field;
    // Server order: values ascending, the null group last.
    const keys = [...new Set(rows.map((row) => row[field] as string | null))]
      .sort((left, right) => left === null ? 1 : right === null ? -1 : left.localeCompare(right));
    const buckets = keys.map((key) => {
      const members = rows.filter((row) => row[field] === key);
      const extra = stale?.group === key ? records.filter((row) => row.id === stale.id) : [];
      return { key: { [field]: key }, aggregate: { count: members.length + extra.length },
        rows: [...members, ...extra].slice(0, variables.rows_limit ?? 0) };
    }).filter((bucket) => bucket.aggregate.count > (variables.having?.count_gt ?? 0));
    return { data: { tasks_groups: buckets, totalCount: buckets.length } };
  });
  const getList = vi.fn(async () => ({ data: records, total: records.length }));
  let view!: ResourceViewContextValue;
  let rerender!: () => void;
  function Probe() { view = useResourceView(); return null; }
  function Page() {
    const [, setTick] = React.useState(0);
    rerender = () => setTick((tick) => tick + 1);
    return (
      <ResourceViewProvider resource={taskResource.modelLabel} scope="local" initialState={initialState}>
        <Probe />
        <ListView resource={taskResource.modelLabel} columns={columns} scope="inherit"
          bulkActions={(_ids, _clear, collection) => { seen.push(collection.selectedGroups); return null; }} />
      </ResourceViewProvider>
    );
  }
  render(
    <Provider resources={[taskResource]} apiUrl="test://selected-groups" dataProvider={{ custom, getList }}
      queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <OperationDocumentsProvider documents={{ console: {
        groups: { "notes.Task": "query Groups { tasks_groups { key } totalCount }" },
        aggregates: { "notes.Task": "query Aggregate { tasks_aggregate { aggregate { count } } }" },
      } }}>
        <ToastProvider><Page /></ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  const client = clients.at(-1)!;
  const select = async (title: string) => {
    const row = (await screen.findByText(title)).closest("tr")!;
    fireEvent.click(within(row).getByRole("checkbox", { name: "Select row" }));
  };
  return {
    select, custom, seen,
    get view() { return view; },
    get selectedGroups() { return seen.at(-1); },
    rerender: () => act(() => rerender()),
    /** Change a record on the server and let the live list refetch. */
    update: async (id: string, patch: Partial<Row>) => {
      Object.assign(records.find((row) => row.id === id)!, patch);
      await act(async () => { await client.invalidateQueries(); });
    },
  };
}

const status = (key: string | null, label: string, ids: string[]) => ({ field: "status", key, label, parents: [], ids });

test("a flat list offers no group split to its bulk actions", async () => {
  const f = selectedGroupsFixture({});
  await f.select("Task 1");
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({ t1: true }));
  expect(f.selectedGroups).toBeUndefined();
});

test("a grouped selection splits by group key in display order and keeps a row's group after its page unloads", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  for (const title of ["Task 3", "Task 4", "Task 5", "Task 1"]) await f.select(title);
  // The key is the stored value and the label what the header shows: null and "" stay two groups.
  await waitFor(() => expect(f.selectedGroups).toEqual([
    status("", "Blank", ["t5"]), status("active", "active", ["t1"]),
    status("closed", "closed", ["t3"]), status(null, "No value", ["t4"]),
  ]));
  // Collapsing "active" unloads its rows; the selected row keeps the group it was selected in.
  fireEvent.click(screen.getByRole("button", { name: "active" }));
  await waitFor(() => expect(screen.queryByText("Task 1")).toBeNull());
  await f.select("Task 3");
  await waitFor(() => expect(f.selectedGroups).toEqual([
    status("", "Blank", ["t5"]), status("active", "active", ["t1"]), status(null, "No value", ["t4"]),
  ]));
});

test("a nested grouping keeps same-label inner groups under different parents apart", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "owner" }, { field: "kind" }] });
  // Nested groups open on demand: one "Same" under Ada, one under Bo.
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Same" })).toHaveLength(2));
  for (const inner of screen.getAllByRole("button", { name: "Same" })) fireEvent.click(inner);
  await f.select("Task 2");
  await f.select("Task 1");
  await waitFor(() => expect(f.selectedGroups).toEqual([
    { field: "kind", key: "Same", label: "Same", parents: [{ field: "owner", key: "Ada", label: "Ada" }], ids: ["t1"] },
    { field: "kind", key: "Same", label: "Same", parents: [{ field: "owner", key: "Bo", label: "Bo" }], ids: ["t2"] },
  ]));
});

test("a grouped selection holding an id no group placed offers no split rather than a partial one", async () => {
  // A programmatic selection holds the id without its row or group.
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }], selectedIds: ["t3"] });
  await f.select("Task 1");
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({ t3: true, t1: true }));
  expect(f.selectedGroups).toBeNull();
});

test("a selected row the live list now shows in another group offers no split", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  await f.select("Task 1");
  await f.select("Task 2");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  // Task 2 is renamed elsewhere; the refetch shows it under "closed", still selected.
  await f.update("t2", { status: "closed" });
  await waitFor(() => expect(f.selectedGroups).toBeNull());
  expect(f.view.state.rowSelection).toEqual({ t1: true, t2: true });
});

test("a row shown under two groups at once cannot be placed", async () => {
  // A stale page of "closed" still lists Task 1 beside the fresh "active" page.
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] }, { stale: { id: "t1", group: "closed" } });
  await waitFor(() => expect(screen.getAllByText("Task 1")).toHaveLength(2));
  fireEvent.click(within(screen.getAllByText("Task 1")[0]!.closest("tr")!).getByRole("checkbox", { name: "Select row" }));
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({ t1: true }));
  expect(f.selectedGroups).toBeNull();
});

test("a group header's checkbox and select-all capture each row's own group", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select the loaded rows in active" }));
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all loaded rows" }));
  await waitFor(() => expect(f.selectedGroups).toEqual([
    status("", "Blank", ["t5"]), status("active", "active", ["t1", "t2"]),
    status("closed", "closed", ["t3"]), status(null, "No value", ["t4"]),
  ]));
});

test("a grouped board card captures its record's group", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }], view: "board" });
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select Task 3" }));
  await waitFor(() => expect(f.selectedGroups).toEqual([status("closed", "closed", ["t3"])]));
});

test("the split checks the captured levels against the rendered ones and the surface's placement", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  await f.select("Task 1");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1"])]));
  const none = new Map();
  expect(f.view.selectedGroups([{ field: "status" }], none)).toEqual([status("active", "active", ["t1"])]);
  // A level's `repeated` is part of its spec, as are its granularity and the level order.
  expect(f.view.selectedGroups([{ field: "status", repeated: true }], none)).toBeNull();
  expect(f.view.selectedGroups([{ field: "status", granularity: "month" }], none)).toBeNull();
  // A surface that places no rows (a lane board under the same field, browser grouping) offers no split.
  expect(f.view.selectedGroups([{ field: "status" }], null)).toBeNull();
});

test("selectedGroups keeps its identity across re-renders while the selection is unchanged", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  await f.select("Task 3");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("closed", "closed", ["t3"])]));
  const split = f.selectedGroups;
  const calls = f.seen.length;
  f.rerender();
  await waitFor(() => expect(f.seen.length).toBeGreaterThan(calls));
  expect(f.selectedGroups).toBe(split);
});

test("a repeated grouping lists no footer total, which a flat aggregate cannot narrow to its groups", async () => {
  const columns = [{ field: "title", header: "Title" }, { field: "id", header: "Tasks", aggregate: "count" as const }];
  const totals = (f: ReturnType<typeof selectedGroupsFixture>) =>
    f.custom.mock.calls.filter(([request]) => !(request.meta?.gqlVariables as { group_by?: unknown }).group_by);
  const plain = selectedGroupsFixture({ groupStack: [{ field: "status" }] }, { columns });
  await waitFor(() => expect(totals(plain).length).toBeGreaterThan(0));
  cleanup();
  const repeated = selectedGroupsFixture({ groupStack: [{ field: "status", repeated: true }] }, { columns });
  // Only "active" holds more than one record.
  await waitFor(() => expect(screen.getByRole("button", { name: "active" })).toBeTruthy());
  expect(screen.queryByRole("button", { name: "closed" })).toBeNull();
  expect(totals(repeated)).toEqual([]);
});

test("the selection clears when a level's axis, granularity, repeated values or order changes", () => {
  let view!: ResourceViewContextValue;
  function Probe() { view = useResourceView(); return null; }
  render(<ResourceViewProvider scope="local" initialState={{ groupStack: [{ field: "owner" }, { field: "kind" }] }}><Probe /></ResourceViewProvider>);
  const stacks: GroupSpec[][] = [
    [{ field: "kind" }, { field: "owner" }],
    [{ field: "kind", repeated: true }, { field: "owner" }],
    [{ field: "created", granularity: "month" }],
    [{ field: "created", granularity: "year" }],
    [{ field: "status" }],
  ];
  for (const groupStack of stacks) {
    act(() => view.toggleSelectedId("t1", true));
    expect(view.state.rowSelection).toEqual({ t1: true });
    act(() => view.setGroupStack(groupStack));
    expect(view.state.rowSelection).toEqual({});
  }
});

test("a client-row-model list refuses a repeated level it would group in the browser", async () => {
  const clientResource = { ...taskResource, rowModel: "client" as const };
  render(
    <Provider resources={[clientResource]} apiUrl="test://client-repeated" dataProvider={{ getList: vi.fn(async () => ({ data: [...tasks], total: tasks.length })) }}
      queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <ToastProvider>
        <ListView resource={clientResource.modelLabel} columns={[{ field: "title", header: "Title" }]} scope="local"
          defaultGroup={{ field: "status", repeated: true }} />
      </ToastProvider>
    </Provider>,
  );
  expect(await screen.findByText(/cannot list only repeated values/)).toBeTruthy();
});

test("an unbound ambient view reports invalid sorting before the list can request rows", async () => {
  const getList = vi.fn(async () => ({ data: [], total: 0 }));
  render(<Provider resources={[resource]} apiUrl="test://sort-boundary" dataProvider={{ getList }}
    queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
    <ToastProvider>
      <ResourceViewProvider scope="local" initialState={{ sorting: [{ id: "unknown", desc: false }] }}>
        <ListView resource={resource.modelLabel} columns={columns} />
      </ResourceViewProvider>
    </ToastProvider>
  </Provider>);
  expect(await screen.findByText('sort[0].field: field "unknown" cannot be sorted')).toBeTruthy();
  expect(screen.getByRole("button", { name: "Reset filters, sorting and grouping" })).toBeTruthy();
  expect(getList).not.toHaveBeenCalled();
});
