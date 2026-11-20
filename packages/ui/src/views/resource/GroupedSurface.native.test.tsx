// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { ResourceQuery, schemaFieldMetadataFromDataResources, type DataResourceMetadata, type GroupSpec, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider, groupByVariables, stableSerialize } from "@angee/refine";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { AppRuntimeProvider, containersFromChildren, type ResourceViewKindContent } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { RESOURCE_CONTAINERS } from "./resource-view-kinds";

import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { useGroupedResourceViewSurface, type GroupedResourceViewSurface } from "./resource-view-surface";
import { ListHeaderCell } from "./resource-view-list-body";
import { ListView, type ListViewProps } from "./ListView";
import { useResourceViewUtilityContext } from "./resource-view-utilities";
import { idsFromRowSelectionState } from "./resource-view-codecs";
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
  const shown: GroupedResourceViewSurface<Row>["shown"][] = [];
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
    shown.push(surface.shown);
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
    /** What every render reported its pages listing. */
    shown,
    custom, getList, lifecycle, refreshCount: async (count: number) => { rowTotal = count; await client.invalidateQueries(); }, delay: (page: number) => { delayPage = page; }, release: () => resolvePage({ data: [{ id: "obsolete", title: "Old filter row" }], total: 120 }) };
}

test("server summary axes without a row identity render buckets without leaf requests", async () => {
  const f = fixture({ summaryOnly: true });
  await waitFor(() => expect(f.header(0)?.label).toBe("active"));
  expect(f.surface.list.error).toBeNull();
  expect(f.getList).not.toHaveBeenCalled();
  expect(f.surface.requestedFields).toEqual(["id", "title", "status"]);
});

test("no render reports a new page of groups settled with the previous page's groups", async () => {
  const f = fixture();
  await waitFor(() => expect(f.header(1)?.label).toBe("Title 0"));
  const root = f.header(0)!;
  if (root.kind !== "groupHeader" || !root.pager) throw new Error("missing root header");
  const previous = f.header(1)!.bucketKey;
  // The key of the titles' second page, as the render's pages name it.
  const second = stableSerialize([root.pager.pageKey, 2, root.pager.pageSize]);
  const from = f.shown.length;
  act(() => f.surface.setScopePage(root.pager!.pageKey, 2));
  await waitFor(() => expect(f.header(1)?.label).toBe("Title 20"));
  const reported = f.shown.slice(from).map((shown) => shown.pages.get(second));
  expect(reported.filter((listed) => listed?.has(previous))).toEqual([]);
  expect(reported.at(-1)).toEqual(new Set([f.header(1)!.bucketKey]));
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

/** The Task server: groups (with HAVING and paging), aggregates and a group's later pages, over editable records. */
function taskServer(rows: readonly Row[], stale?: { id: string; group: string }) {
  const records = rows.map((row) => ({ ...row }));
  let held: Promise<void> | null = null;
  let groupsFail = false;
  let rowsFail = false;
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    if (held) await held;
    const variables = meta!.gqlVariables as {
      group_by?: { field: string }[]; where?: Record<string, unknown>; having?: { count_gt: number }; rows_limit?: number;
      limit?: number; offset?: number;
    };
    if (groupsFail && variables.group_by) throw new Error("Groups unavailable");
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
    const offset = variables.offset ?? 0;
    return { data: { tasks_groups: buckets.slice(offset, offset + (variables.limit ?? buckets.length)), totalCount: buckets.length } };
  });
  // A group's later page: its rows in server order, one page of them.
  const getList = vi.fn(async ({ resource, pagination, meta }: GetListParams) => {
    if (held) await held;
    // A board's project lanes: none, so every card shows in the lane without one.
    if (resource === "projects") return { data: [], total: 0 };
    if (rowsFail) throw new Error("Rows unavailable");
    const matching = records.filter((row) => matchesWhere(row, (meta?.gqlVariables as { where?: Record<string, unknown> } | undefined)?.where));
    const size = pagination?.pageSize ?? matching.length;
    const page = pagination?.currentPage ?? 1;
    return { data: matching.slice((page - 1) * size, page * size), total: matching.length };
  });
  const documents = { console: {
    groups: { "notes.Task": "query Groups { tasks_groups { key } totalCount }" },
    aggregates: { "notes.Task": "query Aggregate { tasks_aggregate { aggregate { count } } }" },
  } };
  return {
    records, custom, getList, documents,
    /** Make every later groups read fail, or answer again. */
    failGroups: (fail: boolean) => { groupsFail = fail; },
    /** Make every later rows read fail, or answer again. */
    failRows: (fail: boolean) => { rowsFail = fail; },
    /** Hold the server's answers until the returned release runs. */
    hold: () => {
      let release!: () => void;
      held = new Promise((resolve) => { release = resolve; });
      return () => act(async () => { held = null; release(); });
    },
  };
}

type BulkCollection = Parameters<NonNullable<ListViewProps["bulkActions"]>>[2];

/** A rendered grouped Task list whose page's bulk actions record the selectedGroups they receive. */
function selectedGroupsFixture(initialState: Record<string, unknown>, {
  stale, columns = [{ field: "title", header: "Title" }], rows = tasks, selectAllMatching, resource = taskResource, utility,
  second, laneSource, related = [],
}: {
  /** A stale page: the server also lists this row in another group. */
  stale?: { id: string; group: string };
  columns?: readonly { field: string; header: string; aggregate?: "count" }[];
  rows?: readonly Row[];
  selectAllMatching?: boolean;
  resource?: DataResourceMetadata;
  /** A list utility contributed to the toolbar, which reads the utility context. */
  utility?: React.ReactElement;
  /** A second list of the same view, sharing its selection, whose page verbs say they rendered. */
  second?: { selectAllMatching?: boolean };
  laneSource?: ListViewProps["laneSource"];
  /** Resources the list's relations name. */
  related?: readonly DataResourceMetadata[];
} = {}) {
  const { records, custom, getList, documents, hold, failGroups, failRows } = taskServer(rows, stale);
  const seen: unknown[] = [];
  const collections: BulkCollection[] = [];
  const secondCollections: BulkCollection[] = [];
  let view!: ResourceViewContextValue;
  let rerender!: () => void;
  let showLists!: React.Dispatch<React.SetStateAction<{ first: boolean; second: boolean }>>;
  function Probe() { view = useResourceView(); return null; }
  function Page() {
    const [, setTick] = React.useState(0);
    const [shownLists, setShownLists] = React.useState({ first: true, second: true });
    rerender = () => setTick((tick) => tick + 1);
    showLists = setShownLists;
    return (
      <ResourceViewProvider resource={resource.modelLabel} scope="local" initialState={initialState}>
        <Probe />
        <section aria-label="First list">{shownLists.first ? (
          <ListView resource={resource.modelLabel} columns={columns} scope="inherit" selectAllMatching={selectAllMatching}
            laneSource={laneSource}
            bulkActions={(_ids, _clear, collection) => { seen.push(collection.selectedGroups); collections.push(collection); return null; }} />
        ) : null}</section>
        {second && shownLists.second ? <section aria-label="Second list">
          <ListView resource={resource.modelLabel} columns={columns} scope="inherit" selectAllMatching={second.selectAllMatching}
            bulkActions={(_ids, _clear, collection) => { secondCollections.push(collection); return <span>Second page verb</span>; }} />
        </section> : null}
      </ResourceViewProvider>
    );
  }
  const page = <ToastProvider><Page /></ToastProvider>;
  render(
    <Provider resources={[resource, ...related]} apiUrl="test://selected-groups" dataProvider={{ custom, getList }}
      queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <OperationDocumentsProvider documents={documents}>
        {utility ? <AppRuntimeProvider runtime={{ containers: containersFromChildren(RESOURCE_CONTAINERS, {
          [`${resource.modelLabel}#utilities`]: { "test.utility": { content: utility } },
        }) }}>{page}</AppRuntimeProvider> : page}
      </OperationDocumentsProvider>
    </Provider>,
  );
  const client = clients.at(-1)!;
  /** The first list, where every helper acts. */
  const first = () => within(screen.getByRole("region", { name: "First list" }));
  const select = async (title: string) => {
    const row = (await first().findByText(title)).closest("tr")!;
    fireEvent.click(within(row).getByRole("checkbox", { name: "Select row" }));
  };
  /** Change a record on the server without telling the list. */
  const edit = (id: string, patch: Partial<Row>) => { Object.assign(records.find((row) => row.id === id)!, patch); };
  return {
    select, custom, getList, seen, edit, failGroups, failRows, first, secondCollections,
    /** Unmount or mount a list, the view and its selection staying. */
    show: (list: "first" | "second", shown: boolean) => act(() => showLists((current) => ({ ...current, [list]: shown }))),
    get view() { return view; },
    get selectedGroups() { return seen.at(-1); },
    /** The collection the page's bulk actions last received. */
    get collection() { return collections.at(-1); },
    /** Every collection the page's bulk actions received, in render order. */
    collections,
    /** The list's read of its root groups is in flight (the frame's `aria-busy`). */
    get fetching() { return document.querySelector("[data-resource-presentation]")?.getAttribute("aria-busy") === "true"; },
    rerender: () => act(() => rerender()),
    /** Change a record on the server and let the live list refetch. */
    update: async (id: string, patch: Partial<Row>) => {
      edit(id, patch);
      await act(async () => { await client.invalidateQueries(); });
    },
    /** Start the live list's refetch without waiting for it. */
    refetch: () => act(() => { void client.invalidateQueries(); }),
    /** Drop what the list's reads hold, so each page loads as for the first time. */
    reset: () => act(() => { void client.resetQueries(); }),
    hold,
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

test("a selected row that moved into a collapsed group offers no split once its old group's page settles without it", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  fireEvent.click(await screen.findByRole("button", { name: "closed" }));
  await waitFor(() => expect(screen.queryByText("Task 3")).toBeNull());
  await f.select("Task 1");
  await f.select("Task 2");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  await f.update("t2", { status: "closed" });
  await waitFor(() => expect(f.selectedGroups).toBeNull());
  expect(f.view.state.rowSelection).toEqual({ t1: true, t2: true });
});

test("a selected row that moved into a group on a page of groups not loaded offers no split", async () => {
  // Two groups a page: "" and "active" here, "closed" on the next page.
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }], pageSize: 2 });
  await f.select("Task 1");
  await f.select("Task 2");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  await f.update("t2", { status: "closed" });
  await waitFor(() => expect(f.selectedGroups).toBeNull());
});

test("a repeated group that shrinks away from its settled page of groups offers no split", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status", repeated: true }] });
  await f.select("Task 1");
  await f.select("Task 2");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  // Renamed, Task 1 leaves "active" with one record: both groups drop below two.
  await f.update("t1", { status: "solo" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "active" })).toBeNull());
  expect(f.selectedGroups).toBeNull();
});

test("a nested selection whose parent group left its settled page offers no split", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "owner" }, { field: "kind" }] });
  // Nested groups open on demand: Ada's "Same", then Bo's.
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Same" })).toHaveLength(2));
  fireEvent.click(screen.getAllByRole("button", { name: "Same" })[1]!);
  await f.select("Task 2");
  await waitFor(() => expect(f.selectedGroups).toEqual([
    { field: "kind", key: "Same", label: "Same", parents: [{ field: "owner", key: "Bo", label: "Bo" }], ids: ["t2"] },
  ]));
  // Only the parent changes: Task 2 is now Cy's, whose "Same" opens on demand.
  await f.update("t2", { owner: "Cy" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Cy" })).toBeTruthy());
  expect(screen.queryByText("Task 2")).toBeNull();
  expect(f.selectedGroups).toBeNull();
});

test("a refetch that changes nothing keeps the split, the same array, throughout", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  await f.select("Task 1");
  await f.select("Task 2");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  const split = f.selectedGroups;
  const from = f.seen.length;
  const release = f.hold();
  f.refetch();
  await waitFor(() => expect(f.fetching).toBe(true));
  const during = f.seen.length;
  await release();
  await waitFor(() => expect(f.fetching).toBe(false));
  expect(during).toBeGreaterThan(from);
  expect(f.seen.length).toBeGreaterThan(during);
  for (const seen of f.seen.slice(from)) expect(seen).toBe(split);
});

test("a refetch that moves a selected row out keeps the last split in flight and offers none once it settles", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  await f.select("Task 1");
  await f.select("Task 2");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  const split = f.selectedGroups;
  const from = f.seen.length;
  f.edit("t2", { status: "closed" });
  const release = f.hold();
  f.refetch();
  await waitFor(() => expect(f.fetching).toBe(true));
  expect(f.seen.length).toBeGreaterThan(from);
  for (const seen of f.seen.slice(from)) expect(seen).toBe(split);
  await release();
  await waitFor(() => expect(f.selectedGroups).toBeNull());
  expect(f.view.state.rowSelection).toEqual({ t1: true, t2: true });
});

test("a page of groups that lists a selected row's group and loads for the first time offers no split until it settles", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  await f.select("Task 1");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1"])]));
  // The reads start over: the page of groups that lists "active" has never settled.
  const release = f.hold();
  f.reset();
  await waitFor(() => expect(f.selectedGroups).toBeNull());
  expect(f.fetching).toBe(true);
  await release();
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1"])]));
});

test("collapsing a group or paging within it keeps the group a row was selected in", async () => {
  const more = Array.from({ length: 20 }, (_, index) => ({ id: `a${index}`, title: `Active ${index}`, status: "active", owner: "Ada", kind: "Same" }));
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] }, { rows: [...tasks, ...more] });
  await f.select("Task 1");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1"])]));
  // The group's second page of records does not hold Task 1, and cannot tell where it is.
  fireEvent.click(await screen.findByRole("button", { name: "Next active records" }));
  expect(await screen.findByText("Active 19")).toBeTruthy();
  expect(screen.queryByText("Task 1")).toBeNull();
  expect(f.selectedGroups).toEqual([status("active", "active", ["t1"])]);
  fireEvent.click(screen.getByRole("button", { name: "active" }));
  await waitFor(() => expect(screen.queryByText("Active 19")).toBeNull());
  expect(f.selectedGroups).toEqual([status("active", "active", ["t1"])]);
});

test("a nested split ranks each group once, so a parent that moved keeps its children together", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "owner" }, { field: "kind" }] }, { rows: [
    { id: "t1", title: "Task 1", owner: "Ada", kind: "Same" }, { id: "t2", title: "Task 2", owner: "Bo", kind: "Same" },
    { id: "t3", title: "Task 3", owner: "Ada", kind: "Other" }, { id: "t4", title: "Task 4", owner: "Aa", kind: "Other" },
    { id: "t5", title: "Task 5", owner: "Ab", kind: "Other" },
  ] });
  const inner = (name: string, index: number) => fireEvent.click(screen.getAllByRole("button", { name })[index]!);
  // Ada is third of the owners: Task 1 is selected under Ada > Same, which then folds away.
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Same" })).toHaveLength(2));
  inner("Same", 0);
  await f.select("Task 1");
  inner("Same", 0);
  await waitFor(() => expect(screen.queryByText("Task 1")).toBeNull());
  // The owners before Ada leave: Ada is now first, Bo second.
  await f.update("t4", { owner: "Zed" });
  await f.update("t5", { owner: "Zed" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Zed" })).toBeTruthy());
  inner("Same", 1);
  await f.select("Task 2");
  inner("Other", 0);
  await f.select("Task 3");
  const kind = (key: string, owner: string, ids: string[]) =>
    ({ field: "kind", key, label: key, parents: [{ field: "owner", key: owner, label: owner }], ids });
  await waitFor(() => expect(f.selectedGroups).toEqual([kind("Other", "Ada", ["t3"]), kind("Same", "Ada", ["t1"]), kind("Same", "Bo", ["t2"])]));
});

test("groups that tie on their captured positions keep selection order", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }], pageSize: 1 }, { rows: [
    { id: "rb", title: "Task b", status: "b" }, { id: "ra", title: "Task a", status: "zz" }, { id: "rc", title: "Task c", status: "c" },
  ] });
  // One group a page: "b" first, then "a" takes the first place and is selected there too.
  await f.select("Task b");
  await f.update("ra", { status: "a" });
  await f.select("Task a");
  // On the third page neither group shows: both rest on position 0, captured on the first.
  act(() => f.view.setPage(3));
  await waitFor(() => expect(screen.getByText("Task c")).toBeTruthy());
  await waitFor(() => expect(f.selectedGroups).toEqual([status("b", "b", ["rb"]), status("a", "a", ["ra"])]));
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

test("ticking a group header recaptures the group of the rows it covers that were already selected", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }] });
  await f.select("Task 1");
  await f.select("Task 2");
  await waitFor(() => expect(f.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  // Task 2 moves to "closed", still selected: it has no place until it is ticked again.
  await f.update("t2", { status: "closed" });
  await waitFor(() => expect(f.selectedGroups).toBeNull());
  fireEvent.click(screen.getByRole("checkbox", { name: "Select the loaded rows in closed" }));
  await waitFor(() => expect(f.selectedGroups).toEqual([
    status("active", "active", ["t1"]), status("closed", "closed", ["t2", "t3"]),
  ]));
});

test("a grouped board card captures its record's group", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status" }], view: "board" });
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select Task 3" }));
  await waitFor(() => expect(f.selectedGroups).toEqual([status("closed", "closed", ["t3"])]));
});

test("a list grouped in the browser places no rows, so its bulk actions get no split", async () => {
  const clientResource = { ...taskResource, rowModel: "client" as const };
  const seen: unknown[] = [];
  render(
    <Provider resources={[clientResource]} apiUrl="test://client-groups" dataProvider={{ getList: vi.fn(async () => ({ data: [...tasks], total: tasks.length })) }}
      queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <ToastProvider>
        <ListView resource={clientResource.modelLabel} columns={[{ field: "title", header: "Title" }]} scope="local"
          defaultGroup={{ field: "status" }} bulkActions={(_ids, _clear, collection) => { seen.push(collection.selectedGroups); return null; }} />
      </ToastProvider>
    </Provider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: /^active/, expanded: false }));
  const row = (await screen.findByText("Task 1")).closest("tr")!;
  fireEvent.click(within(row).getByRole("checkbox", { name: "Select row" }));
  await waitFor(() => expect(seen.length).toBeGreaterThan(0));
  expect(seen.at(-1)).toBeNull();
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

/** The variables of the list's own root groups read, as the server last received them. */
function rootGroupsRead(custom: ReturnType<typeof taskServer>["custom"], field: string) {
  return custom.mock.calls.map(([request]) => request.meta?.gqlVariables as {
    group_by?: { field: string }[]; where?: Record<string, unknown>; having?: Record<string, unknown>;
  }).filter((variables) => variables.group_by?.[0]?.field === field).at(-1)!;
}

/** The `where` of the list's last rows read, as the server received it. */
const rowsWhere = (getList: ReturnType<typeof taskServer>["getList"]) =>
  (getList.mock.calls.at(-1)![0].meta?.gqlVariables as { where: Record<string, unknown> }).where;

/** A verb's accessible description: the text of the nodes its aria-describedby names. */
function description(element: HTMLElement): string {
  return (element.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? "").join(" ");
}

const checked = (name: string, index = 0) => screen.getAllByRole("checkbox", { name })[index]!.getAttribute("aria-checked");
const sixTasks = [...tasks, { id: "t6", title: "Task 6", status: "closed", owner: "Bo", kind: "Other" }];
const deletableTasks = { ...taskResource, roots: { ...taskResource.roots, deletePreview: "tasks_delete" } };
// Tasks whose board lanes are their project, a relation.
const laneContract = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" }, owner: { scalar: "String" }, kind: { scalar: "String" },
  project: { kind: "relation", identityPath: "project.id", labelPath: "project.name" },
} }).contract;
laneContract.fields.project!.relation = { model: "notes.Project", identityPath: "project.id", labelPath: "project.name" };
for (const field of ["status", "owner", "kind"]) laneContract.axes[field] = taskContract.axes[field]!;
laneContract.axes.project!.server = { input: "project", key: "project" };
const laneTasks: DataResourceMetadata = { ...taskResource, query: laneContract, fields: [...taskResource.fields ?? [], {
  name: "project", kind: "relation", relationModelLabel: "notes.Project", relationObject: true, readable: true,
  aggregatable: false, nullable: true, creatable: false, updatable: false, requiredOnCreate: false,
}] };
const projects = testDataResource("notes.Project", {
  capabilities: ["list"], roots: { list: "projects" }, recordRepresentation: "name",
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, name: { scalar: "String" } } }).contract,
  fields: ["id", "name"].map((name) => ({ name, kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
});
const SELECT_ALL = /^Select (all|the) /;
const ADA = { pageSize: 2, filter: { owner: { exact: "Ada" } } };

/** Ada's four tasks, two a page, with every one of them selected. */
async function allOfAda(options: Parameters<typeof selectedGroupsFixture>[1] = {}) {
  const f = selectedGroupsFixture(ADA, { selectAllMatching: true, ...options });
  await f.select("Task 1");
  await f.select("Task 3");
  fireEvent.click(await f.first().findByRole("button", { name: "Select all 4 records" }));
  await waitFor(() => expect(f.collection?.allMatching?.total).toBe(4));
  return f;
}

/** The repeated status groups, `pageSize` a page, with every one of them selected. */
async function allRepeated(options: Parameters<typeof selectedGroupsFixture>[1] = {}, { pageSize = 1, groups = 2 } = {}) {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status", repeated: true }], pageSize },
    { rows: sixTasks, selectAllMatching: true, ...options });
  await screen.findByText("Task 2");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all loaded rows" }));
  fireEvent.click(await screen.findByRole("button", { name: `Select all ${groups} groups` }));
  await waitFor(() => expect(f.collection?.allMatching?.total).toBe(groups));
  return f;
}

test("an opted-in flat list offers its every record once each loaded row is selected, handing on a copy of its own read", async () => {
  const f = selectedGroupsFixture(ADA, { selectAllMatching: true });
  await f.select("Task 1");
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({ t1: true }));
  expect(screen.queryByRole("button", { name: SELECT_ALL })).toBeNull();
  expect(f.collection).not.toHaveProperty("allMatching");
  await f.select("Task 3");
  fireEvent.click(await screen.findByRole("button", { name: "Select all 4 records" }));
  expect(await screen.findByText("All 4 records selected")).toBeTruthy();
  // Focus stays in the bar: the control that was pressed is gone.
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Clear" }));
  expect(JSON.stringify(rowsWhere(f.getList))).toContain("Ada");
  expect(f.collection?.allMatching).toEqual({ unit: "records", total: 4, read: { where: rowsWhere(f.getList) } });
  // No id of an unloaded row is invented.
  expect(f.view.state.rowSelection).toEqual({ t1: true, t3: true });
  expect(f.collection?.selectedRows.map((row) => row.id)).toEqual(["t1", "t3"]);
  // A page that changes what it received changes nothing in the list's next read.
  Object.assign(f.collection!.allMatching!.read.where as Record<string, unknown>, { title: { _eq: "Task 9" } });
  const reads = f.getList.mock.calls.length;
  await f.update("t1", {});
  await waitFor(() => expect(f.getList.mock.calls.length).toBeGreaterThan(reads));
  expect(JSON.stringify(rowsWhere(f.getList))).not.toContain("Task 9");
});

test("an opted-in repeated grouping offers its every group, handing on its own groups read", async () => {
  const f = await allRepeated();
  expect(await screen.findByText("All 2 groups selected")).toBeTruthy();
  const sent = rootGroupsRead(f.custom, "status");
  expect(sent.having).toEqual({ count_gt: 1 });
  const allMatching = f.collection?.allMatching;
  if (allMatching?.unit !== "groups") throw new Error("expected every group selected");
  expect(allMatching.total).toBe(2);
  expect(allMatching.read.dimensions).toHaveLength(1);
  // A page turns it into its own read's variables: those the list's request sent.
  const variables = groupByVariables(allMatching.read);
  expect([variables.group_by, variables.where, variables.having]).toEqual([sent.group_by, sent.where, sent.having]);
  expect(f.collection?.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]);
});

test("a grouping that has not opted in, or whose page holds every record, offers no select-all", async () => {
  const plain = selectedGroupsFixture({ groupStack: [{ field: "status", repeated: true }], pageSize: 1 }, { rows: sixTasks });
  await screen.findByText("Task 2");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all loaded rows" }));
  await waitFor(() => expect(plain.collection?.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]));
  expect(screen.queryByRole("button", { name: SELECT_ALL })).toBeNull();
  expect(plain.collection).not.toHaveProperty("allMatching");
  cleanup();
  // Only "active" repeats, and both its records are loaded.
  const whole = selectedGroupsFixture({ groupStack: [{ field: "status", repeated: true }] }, { selectAllMatching: true });
  await screen.findByText("Task 2");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all loaded rows" }));
  await waitFor(() => expect(whole.view.state.rowSelection).toEqual({ t1: true, t2: true }));
  expect(screen.getByText("2 selected")).toBeTruthy();
  expect(screen.queryByRole("button", { name: SELECT_ALL })).toBeNull();
});

test("under nested grouping a root group holds records nobody saw: no select-all, no member", async () => {
  // Kinds one a page ("Other" first), each split into its repeated statuses: only "closed" repeats there.
  const f = selectedGroupsFixture({ groupStack: [{ field: "kind" }, { field: "status", repeated: true }], pageSize: 1 },
    { rows: sixTasks, selectAllMatching: true });
  fireEvent.click(await screen.findByRole("button", { name: "closed" }));
  await screen.findByText("Task 6");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all loaded rows" }));
  await waitFor(() => expect(f.view.state.rowSelection).toEqual({ t3: true, t6: true }));
  expect(screen.getByText("2 selected")).toBeTruthy();
  expect(screen.queryByRole("button", { name: SELECT_ALL })).toBeNull();
  expect(f.collections.some((collection) => "allMatching" in collection)).toBe(false);
});

test("paging in the mode hands every render the whole selection, never the selected ids alone", async () => {
  const f = await allOfAda();
  const from = f.collections.length;
  const release = f.hold();
  act(() => f.view.setPage(2));
  await waitFor(() => expect(f.fetching).toBe(true));
  expect(screen.getByText("All 4 records selected")).toBeTruthy();
  await release();
  expect(await screen.findByText("Task 5")).toBeTruthy();
  expect(f.collections.length).toBeGreaterThan(from + 1);
  expect(f.collections.slice(from).map((collection) => collection.allMatching?.total)).toEqual(
    f.collections.slice(from).map(() => 4));
});

test("a failed refetch of the root groups keeps the last settled member and the generic verbs disabled", async () => {
  const f = await allRepeated({ resource: deletableTasks });
  const from = f.collections.length;
  f.failGroups(true);
  await f.update("t1", {});
  expect(await screen.findByText("Groups unavailable")).toBeTruthy();
  expect(f.collections.slice(from).map((collection) => collection.allMatching?.total)).toEqual(
    f.collections.slice(from).map(() => 2));
  expect(screen.getByText("All 2 groups selected")).toBeTruthy();
  const remove = screen.getByRole("button", { name: "Delete" });
  expect(remove.getAttribute("aria-disabled")).toBe("true");
  expect(description(remove)).toBe("This acts on the rows you select, not on all 2 groups");
});

test("a refetch that settles with another total moves the selection's N, which paging then keeps", async () => {
  const f = await allOfAda();
  await f.update("t2", { owner: "Ada" });
  expect(await screen.findByText("All 5 records selected")).toBeTruthy();
  expect(f.collection?.allMatching?.total).toBe(5);
  const from = f.collections.length;
  const release = f.hold();
  act(() => f.view.setPage(2));
  await waitFor(() => expect(f.fetching).toBe(true));
  expect(screen.getByText("All 5 records selected")).toBeTruthy();
  await release();
  await waitFor(() => expect(f.fetching).toBe(false));
  expect(new Set(f.collections.slice(from).map((collection) => collection.allMatching?.total))).toEqual(new Set([5]));
});

const selected = (f: ReturnType<typeof selectedGroupsFixture>) => [...idsFromRowSelectionState(f.view.state.rowSelection)];

test("every loaded row shows selected in the mode; unselecting one leaves what shows selected, the header clears what it covers", async () => {
  const f = await allOfAda();
  act(() => f.view.setPage(2));
  await screen.findByText("Task 5");
  expect(checked("Select all rows on this page")).toBe("true");
  expect(checked("Select row", 0)).toBe("true");
  expect(checked("Select row", 1)).toBe("true");
  // The selection's ids stay the ones selected.
  expect(f.view.state.rowSelection).toEqual({ t1: true, t3: true });
  // Task 5 stays selected as it shows; Tasks 1 and 3, selected on a page not shown now, are dropped.
  await f.select("Task 4");
  await waitFor(() => expect(screen.getByText("1 selected")).toBeTruthy());
  expect(f.collection).not.toHaveProperty("allMatching");
  expect([checked("Select row", 0), checked("Select row", 1)]).toEqual(["false", "true"]);
  expect(selected(f)).toEqual(["t5"]);
  expect(f.collection?.selectedRows.map((row) => row.id)).toEqual(["t5"]);
  cleanup();
  // The page's header covers every row shown: unselecting it leaves nothing selected.
  const again = await allOfAda();
  act(() => again.view.setPage(2));
  await screen.findByText("Task 5");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all rows on this page" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Clear" })).toBeNull());
  expect(selected(again)).toEqual([]);
  expect(checked("Select row", 0)).toBe("false");
});

test("every loaded group shows selected in the mode; unselecting a group or a row leaves the loaded rows that show selected, in their groups", async () => {
  // Three repeated groups, two a page: "active" and "closed", then "open".
  const rows = [...sixTasks, { id: "t7", title: "Task 7", status: "open", owner: "Bo", kind: "Other" },
    { id: "t8", title: "Task 8", status: "open", owner: "Bo", kind: "Other" }];
  const f = await allRepeated({ rows }, { pageSize: 2, groups: 3 });
  expect(checked("Select all loaded rows")).toBe("true");
  expect(checked("Select the loaded rows in closed")).toBe("true");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select the loaded rows in closed" }));
  await waitFor(() => expect(screen.getByText("2 selected")).toBeTruthy());
  expect(f.collection).not.toHaveProperty("allMatching");
  expect(f.collection?.selectedGroups).toEqual([status("active", "active", ["t1", "t2"])]);
  expect([checked("Select the loaded rows in active"), checked("Select the loaded rows in closed")]).toEqual(["true", "false"]);
  cleanup();
  // On the next page of groups only "open" shows: unselecting Task 7 leaves Task 8, in its group.
  const again = await allRepeated({ rows }, { pageSize: 2, groups: 3 });
  act(() => again.view.setPage(2));
  await screen.findByText("Task 8");
  expect(checked("Select the loaded rows in open")).toBe("true");
  await again.select("Task 7");
  await waitFor(() => expect(screen.getByText("1 selected")).toBeTruthy());
  expect(again.collection).not.toHaveProperty("allMatching");
  expect(selected(again)).toEqual(["t8"]);
  expect(again.collection?.selectedRows.map((row) => row.id)).toEqual(["t8"]);
  expect(again.collection?.selectedGroups).toEqual([status("open", "open", ["t8"])]);
});

test("with every group collapsed in the mode the header and the groups show selected, and unselecting the header leaves it", async () => {
  const f = await allRepeated();
  fireEvent.click(screen.getByRole("button", { name: "Collapse all groups" }));
  await waitFor(() => expect(screen.queryByText("Task 1")).toBeNull());
  const header = screen.getByRole("checkbox", { name: "Select all loaded rows" });
  expect(header.getAttribute("aria-checked")).toBe("true");
  expect(header.hasAttribute("data-disabled")).toBe(false);
  expect(checked("Select the loaded rows in active")).toBe("true");
  fireEvent.click(header);
  // Nothing shows selected any more, so nothing is.
  await waitFor(() => expect(screen.queryByRole("button", { name: "Clear" })).toBeNull());
  expect(selected(f)).toEqual([]);
  expect(header.getAttribute("aria-checked")).toBe("false");
});

test("a page of groups loading for the first time leaves the pager's total unknown, never 0", async () => {
  const f = await allRepeated();
  expect(screen.getByRole("button", { name: "Groups 1-1 / 2 groups" })).toBeTruthy();
  const release = f.hold();
  act(() => f.view.setPage(2));
  await waitFor(() => expect(f.fetching).toBe(true));
  expect(screen.getByRole("button", { name: "Groups 2-2" })).toBeTruthy();
  expect(screen.getByText("All 2 groups selected")).toBeTruthy();
  await release();
  expect(await screen.findByRole("button", { name: "Groups 2-2 / 2 groups" })).toBeTruthy();
});

test("a flat list's failed refetch or failed page change in the mode keeps the last settled member and the generic verbs disabled", async () => {
  const f = await allOfAda({ resource: deletableTasks });
  const keeps = (from: number) => {
    expect(f.collections.length).toBeGreaterThan(from);
    expect(f.collections.slice(from).map((collection) => collection.allMatching?.total)).toEqual(
      f.collections.slice(from).map(() => 4));
    expect(screen.getByText("All 4 records selected")).toBeTruthy();
    const remove = screen.getByRole("button", { name: "Delete" });
    expect(remove.getAttribute("aria-disabled")).toBe("true");
    expect(description(remove)).toBe("This acts on the rows you select, not on all 4 records");
  };
  let from = f.collections.length;
  f.failRows(true);
  await f.update("t1", {});
  expect(await screen.findByText("Rows unavailable")).toBeTruthy();
  keeps(from);
  from = f.collections.length;
  act(() => f.view.setPage(2));
  await waitFor(() => expect(f.getList.mock.calls.some(([params]) => params.pagination?.currentPage === 2)).toBe(true));
  await waitFor(() => expect(f.fetching).toBe(false));
  expect(screen.getByText("Rows unavailable")).toBeTruthy();
  keeps(from);
});

test("a list reading otherwise without remounting, a lane board and back, leaves the mode for the selected ids", async () => {
  const f = await allOfAda({ resource: laneTasks, related: [projects], laneSource: { field: "project" } });
  act(() => f.view.setView("board"));
  await waitFor(() => expect(screen.queryByText("All 4 records selected")).toBeNull());
  expect(screen.getByText("2 selected")).toBeTruthy();
  expect(f.view.state.rowSelection).toEqual({ t1: true, t3: true });
  act(() => f.view.setView("list"));
  // The person may choose it again; nothing chose it for them.
  expect(await f.first().findByRole("button", { name: "Select all 4 records" })).toBeTruthy();
  expect(screen.queryByText("All 4 records selected")).toBeNull();
  expect(f.collection).not.toHaveProperty("allMatching");
});

test("a list that unmounts, as on leaving its route, and mounts again is back to the selected ids", async () => {
  const f = await allOfAda();
  f.show("first", false);
  expect(f.view.state.rowSelection).toEqual({ t1: true, t3: true });
  f.show("first", true);
  expect(await f.first().findByRole("button", { name: "Select all 4 records" })).toBeTruthy();
  expect(screen.getByText("2 selected")).toBeTruthy();
  expect(f.collection).not.toHaveProperty("allMatching");
});

test("a second list sharing the selection refuses its generic verbs and Share and renders no page verbs, and only the list that chose the mode ends it", async () => {
  function Reason() {
    const { disabledReason } = useResourceViewUtilityContext();
    return <output aria-label="Utility reason">{disabledReason == null ? "none" : disabledReason}</output>;
  }
  const reason = "This acts on the rows you select, not on all 4 records";
  // The second list has not opted in, so it reads its rows otherwise, over the same selected ids.
  const f = await allOfAda({ resource: deletableTasks, utility: <Reason />, second: {} });
  const second = within(screen.getByRole("region", { name: "Second list" }));
  expect(second.getByText("2 selected")).toBeTruthy();
  const remove = second.getByRole("button", { name: "Delete" });
  expect(remove.getAttribute("aria-disabled")).toBe("true");
  expect(description(remove)).toBe(reason);
  expect(second.getByRole("status", { name: "Utility reason" }).textContent).toBe(reason);
  expect(second.queryByText("Second page verb")).toBeNull();
  const handed = f.secondCollections.length;
  f.rerender();
  expect(f.secondCollections).toHaveLength(handed);
  cleanup();
  // A second list reading the same shows the mode and hands it on; its leaving never ends it.
  const same = await allOfAda({ second: { selectAllMatching: true } });
  const other = within(screen.getByRole("region", { name: "Second list" }));
  expect(other.getByText("All 4 records selected")).toBeTruthy();
  expect(same.secondCollections.at(-1)?.allMatching?.total).toBe(4);
  same.show("second", false);
  expect(same.first().getByText("All 4 records selected")).toBeTruthy();
  same.rerender();
  expect(same.collection?.allMatching?.total).toBe(4);
});

test("paging keeps every group selected; selecting a row, Clear, the filter, the sort or a level's repeated values leave it", async () => {
  const f = selectedGroupsFixture({ groupStack: [{ field: "status", repeated: true }], pageSize: 1 },
    { rows: sixTasks, selectAllMatching: true });
  const selectAll = async () => {
    await screen.findByText("Task 2");
    if (!f.view.state.rowSelection.t1 || !f.view.state.rowSelection.t2) {
      fireEvent.click(screen.getByRole("checkbox", { name: "Select all loaded rows" }));
    }
    fireEvent.click(await screen.findByRole("button", { name: "Select all 2 groups" }));
    await waitFor(() => expect(f.collection?.allMatching?.total).toBe(2));
  };
  await selectAll();
  const from = f.collections.length;
  act(() => f.view.setPage(2));
  expect(await screen.findByText("Task 6")).toBeTruthy();
  await waitFor(() => expect(f.fetching).toBe(false));
  expect(screen.getByText("All 2 groups selected")).toBeTruthy();
  expect(f.collections.slice(from).every((collection) => collection.allMatching?.total === 2)).toBe(true);
  act(() => f.view.setPage(1));
  await f.select("Task 1");
  await waitFor(() => expect(f.collection?.selectedRows.map((row) => row.id)).toEqual(["t2"]));
  expect(f.collection).not.toHaveProperty("allMatching");
  expect(screen.getByText("1 selected")).toBeTruthy();
  await f.select("Task 1");
  await selectAll();
  // Clear empties the selection.
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(f.view.state.rowSelection).toEqual({});
  expect(screen.queryByText("All 2 groups selected")).toBeNull();
  // A change of filter, sort or grouping clears the selection, the mode with it.
  for (const change of [
    () => f.view.setFilter({ title: { ne: "Task 5" } }),
    () => f.view.setSorting([{ id: "title", desc: true }]),
    () => f.view.setGroupStack([{ field: "status" }]),
  ]) {
    await selectAll();
    act(change);
    expect(f.view.state.rowSelection).toEqual({});
    expect(screen.queryByText("All 2 groups selected")).toBeNull();
  }
});

test("the mode never comes back by itself: a view reading otherwise leaves it, and the list is back to the selected ids", async () => {
  const f = await allRepeated();
  // The gallery pages every record of the filter, not the repeated groups the person chose.
  act(() => f.view.setView("gallery"));
  // Its own read has settled: six records, one a page.
  expect(await screen.findByRole("button", { name: "Records 1-1 / 6" })).toBeTruthy();
  expect(f.collection).not.toHaveProperty("allMatching");
  expect(screen.queryByText("All 2 groups selected")).toBeNull();
  expect(f.view.state.rowSelection).toEqual({ t1: true, t2: true });
  act(() => f.view.setView("list"));
  // The person may choose it again; nothing chose it for them.
  expect(await screen.findByRole("button", { name: "Select all 2 groups" })).toBeTruthy();
  expect(screen.queryByText("All 2 groups selected")).toBeNull();
  expect(f.collection).not.toHaveProperty("allMatching");
});

test("a list utility receives the mode's reason, so a Share of the selected rows can refuse it", async () => {
  function Reason() {
    const { disabledReason } = useResourceViewUtilityContext();
    return <output aria-label="Utility reason">{disabledReason === null ? "none" : disabledReason}</output>;
  }
  await allOfAda({ utility: <Reason /> });
  expect(screen.getByRole("status", { name: "Utility reason" }).textContent)
    .toBe("This acts on the rows you select, not on all 4 records");
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  await waitFor(() => expect(screen.getByRole("status", { name: "Utility reason" }).textContent).toBe("none"));
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

test("a view switch drops repeated values the new view cannot apply, keeps the grouping, and the URL follows", async () => {
  const server = taskServer(tasks);
  // A contributed kind that renders the grouping itself, never through the server group read.
  const timeline: ResourceViewKindContent = { label: "Timeline", icon: "network", render: () => <p>Timeline body</p>,
    capabilities: { grouping: true, pagination: true, columns: false, filter: true } };
  const containers = { ...containersFromChildren(RESOURCE_CONTAINERS, { "notes.Task#views": { "tasks.timeline": { content: timeline } } }), rules: {} };
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", component: () => (
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets, containers }}>
      <ListView resource={taskResource.modelLabel} columns={[{ field: "title", header: "Title" }]} />
    </AppRuntimeProvider>
  ) });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: ["/?group=status%3Arepeated"] }) });
  render(
    <Provider resources={[taskResource]} apiUrl="test://view-switch-repeated" dataProvider={{ custom: server.custom, getList: server.getList }}
      queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <OperationDocumentsProvider documents={server.documents}>
        <ToastProvider><RouterProvider router={router} /></ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  // Only "active" holds more than one record.
  expect(await screen.findByRole("button", { name: "active" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "closed" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Timeline" }));
  expect(await screen.findByText("Timeline body")).toBeTruthy();
  await waitFor(() => expect(router.state.location.search).toEqual({ view: "tasks.timeline", group: "status" }));
  fireEvent.click(screen.getByRole("button", { name: "List view" }));
  // Back on the list, the grouping lists every group again.
  expect(await screen.findByRole("button", { name: "closed" })).toBeTruthy();
  expect(router.state.location.search).toEqual({ group: "status" });
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
