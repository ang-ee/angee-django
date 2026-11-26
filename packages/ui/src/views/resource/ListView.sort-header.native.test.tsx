// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { getCoreRowModel, useReactTable, type SortingState } from "@tanstack/react-table";
import { ResourceQuery, schemaFieldMetadataFromDataResources, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ToastProvider } from "../../feedback";
import { createUiTestProviders } from "../../testing";
import { ListView } from "./ListView";
import { RowsListView } from "./RowsListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { buildColumns, ListHeaderCell } from "./resource-view-list-body";
import { favoriteFromResourceView, resourceViewFavoriteIsActive, resourceViewFavoritesFromUnknown } from "./model/favorites";
import type { ResourceViewGroup, ResourceViewInitialState } from "./resource-view-model";
import { useResourceViewSurface } from "./resource-view-surface";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://sort-header" });
beforeEach(() => {
  // The document has no layout: the list's scroll element is a 600px viewport for its virtualizer.
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("resource-table-scroll") ? 600 : 0;
  });
});
afterEach(() => { cleanup(); clearClients(); vi.restoreAllMocks(); });

type Order = readonly { field: string; direction: "ASC" | "DESC" }[];
const records: readonly Row[] = [
  { id: "a", title: "Alpha", status: "open", rank: 2 },
  { id: "b", title: "Bravo", status: "open", rank: 1 },
  { id: "c", title: "Charlie", status: "closed", rank: 3 },
  { id: "d", title: "Delta", status: "open", rank: 0 },
];
const fields = { id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" }, rank: { scalar: "Int" } } as const;
const titleAndRank = [{ field: "title", header: "Title" }, { field: "rank", header: "Rank" }];
const withStatus = [{ field: "status", header: "Status" }, ...titleAndRank];

/** A query over the tasks whose default order is `order`; its `status` axis groups on the server too. */
function taskContract(order: Order) {
  const contract = ResourceQuery.forRows({ fields }).contract;
  contract.axes.status!.server = { input: "status", key: "status" };
  contract.axes.status!.drill = { kind: "value", field: "status", valueKey: "status", nullMode: "isNull", valueMap: [] };
  return { ...contract, sort: { ...contract.sort, default: [...order] } };
}

/** The tasks' resource, whose default order is `order`. */
function taskResource(order: Order, rowModel: "server" | "client" = "server") {
  return testDataResource("notes.Task", {
    roots: { aggregate: "tasks_aggregate", groups: "tasks_groups" }, typeNames: { filter: "TaskBoolExp", order: "TaskOrderBy" },
    query: taskContract(order), recordRepresentation: "title", rowModel,
    fields: Object.keys(fields).map((name) => ({ name, kind: "scalar", scalar: "String", readable: true,
      aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
  });
}

/**
 * A task list over a resource whose default order is `order`, its view state starting from
 * `initialState`, held by the list (`local`) or by the URL (`route`, opened at `path`).
 */
function renderList(order: Order, { columns = titleAndRank, initialState = {}, rowModel = "server", scope = "local", path = "/tasks" }: {
  columns?: typeof withStatus; initialState?: ResourceViewInitialState; rowModel?: "server" | "client"; scope?: "local" | "route"; path?: string;
} = {}) {
  const resource = taskResource(order, rowModel);
  // A held read answers once `release` runs.
  let held: { promise: Promise<void>; release: () => void } | null = null;
  const getList = vi.fn(async (_params: GetListParams) => {
    await held?.promise;
    return { data: records as Row[], total: records.length };
  });
  // The groups read: one group per status, each carrying its records.
  const custom = vi.fn(async (_params: { meta?: Record<string, unknown> }) => ({ data: {
    tasks_groups: ["closed", "open"].map((status) => {
      const members = records.filter((row) => row.status === status);
      return { key: { status }, aggregate: { count: members.length }, rows: members };
    }),
    totalCount: 2,
  } }));
  const view: { current?: ResourceViewContextValue } = {};
  function ViewState() { view.current = useResourceView(); return null; }
  const tree = (
    <Provider resources={[resource]} dataProvider={{ getList, custom }} queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <OperationDocumentsProvider documents={{ console: { groups: { "notes.Task": "query Groups { tasks_groups { key } totalCount }" } } }}>
        <ToastProvider>
          <ResourceViewProvider resource={resource.modelLabel} scope={scope} initialState={initialState}>
            <ViewState />
            <ListView resource={resource.modelLabel} scope="inherit" columns={columns} />
          </ResourceViewProvider>
        </ToastProvider>
      </OperationDocumentsProvider>
    </Provider>
  );
  let router: ReturnType<typeof createRouter> | null = null;
  if (scope === "route") {
    const root = createRootRoute({ component: () => tree });
    const tasks = createRoute({ getParentRoute: () => root, path: "tasks" });
    router = createRouter({ routeTree: root.addChildren([tasks]), history: createMemoryHistory({ initialEntries: [path] }) });
    render(<RouterProvider router={router} />);
  } else render(tree);
  const keys = (order: Record<string, string> | undefined) => Object.entries(order ?? {}).map(([field, direction]) => `${field} ${direction}`);
  const groupsRead = () => custom.mock.calls.at(-1)?.[0].meta?.gqlVariables as {
    order_by?: { field: string; direction: string }[]; rows_order_by?: Record<string, string>;
  } | undefined;
  return {
    /** The list's view state and commands, as its toolbar and saved views use them. */
    view: () => view.current!,
    /** The route list's address: its path and search, as a reload or a shared link opens it. */
    href: () => router!.state.location.href,
    /** The `sort` key the route list's address holds. */
    urlSort: () => (router!.state.location.search as { sort?: unknown }).sort,
    /** How many reads of the list's records were made. */
    reads: () => getList.mock.calls.length,
    /** The order the last read of the list's records asked for, key by key. */
    readOrder: () => keys((getList.mock.calls.at(-1)?.[0].meta?.gqlVariables as { order_by?: Record<string, string> } | undefined)?.order_by),
    /** The order the last groups read asked for its groups. */
    groupOrder: () => groupsRead()?.order_by?.map(({ field, direction }) => `${field} ${direction.toLowerCase()}`),
    /** The order the last groups read asked its groups' records in. */
    groupRowsOrder: () => keys(groupsRead()?.rows_order_by),
    /** Hold every later read of the records until `release`. */
    hold: () => {
      let release!: () => void;
      held = { promise: new Promise((done) => { release = done; }), release };
    },
    release: () => act(async () => { const current = held; held = null; current?.release(); }),
  };
}

/** The same tasks held in the browser, under a query whose default order is `order`. */
function renderRows(order: Order, { columns = withStatus, grouped = false, initialState = {}, query = ResourceQuery.fromContract(taskContract(order)), rows = records }: {
  columns?: typeof withStatus; grouped?: boolean; initialState?: ResourceViewInitialState; query?: ResourceQuery; rows?: readonly Row[];
} = {}) {
  const view: { current?: ResourceViewContextValue } = {};
  function ViewState() { view.current = useResourceView(); return null; }
  render(<ToastProvider>
    <ResourceViewProvider scope="local" initialState={{ ...(grouped ? { groupStack: [{ field: "status" }] } : {}), ...initialState }}>
      <ViewState />
      <RowsListView scope="inherit" rows={rows as { id: string }[]} query={query} columns={columns} />
    </ResourceViewProvider>
  </ToastProvider>);
  /** The list's sorting state. */
  return { sorting: () => view.current?.state.sorting };
}

/** Tasks with a due date, a creation instant and an owner, ordered by `order`: the axes a group can share a key on. */
const datedQuery = (order: Order) => {
  const contract = ResourceQuery.forRows({ fields: {
    id: { scalar: "ID" }, title: { scalar: "String" }, rank: { scalar: "Int" }, due: { scalar: "Date" }, created_at: { scalar: "DateTime" },
    owner: { kind: "relation", identityPath: "owner.id", labelPath: "owner.name" }, "owner.name": { scalar: "String" },
  } }).contract;
  return ResourceQuery.fromContract({ ...contract, sort: { ...contract.sort, default: [...order] } });
};
const dated: readonly Row[] = [
  { id: "a", title: "Alpha", rank: 2, due: "2026-02-01", created_at: "2026-02-01T10:00:00Z", owner: { id: "o1", name: "Ann" } },
  { id: "b", title: "Bravo", rank: 1, due: "2026-02-02", created_at: "2026-02-01T09:00:00Z", owner: { id: "o2", name: "Bo" } },
  { id: "c", title: "Charlie", rank: 3, due: "2026-02-01", created_at: "2026-02-02T08:00:00Z", owner: { id: "o1", name: "Ann" } },
  { id: "d", title: "Delta", rank: 0, due: "2026-02-02", created_at: "2026-02-01T11:00:00Z", owner: { id: "o2", name: "Bo" } },
];

const header = (name: string) => screen.getByRole("columnheader", { name: new RegExp(name) });
/** What each header's `aria-sort` and sort button show; the two must agree. */
function shown(...names: readonly string[]): Record<string, string> {
  const labels = { ascending: "currently ascending", descending: "currently descending", none: "not sorted" } as const;
  return Object.fromEntries(names.map((name) => {
    const sort = header(name).getAttribute("aria-sort") as keyof typeof labels;
    expect(screen.getByRole("button", { name: `Sort ${name} (${labels[sort]})` })).toBeTruthy();
    return [name, sort];
  }));
}
/** The records' titles in the order the rows show them. */
const rowTitles = () => screen.getAllByText(/^(Alpha|Bravo|Charlie|Delta)$/).map((cell) => cell.textContent);
const press = (name: string) => fireEvent.click(screen.getByRole("button", { name: new RegExp(`^Sort ${name} `) }));
/** A table's header cells, as a list renders them. */
function HeaderCells({ table, resourceView }: { table: ReturnType<typeof useReactTable<Row>>; resourceView: ResourceViewContextValue }) {
  return <table><thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>
    {group.headers.map((cell) => <ListHeaderCell key={cell.id} header={cell} resourceView={resourceView} />)}
  </tr>)}</thead></table>;
}

test.each(["Rank", "Title"])("tie-breakers show no sort; a first press on %s sorts it in its first direction as a fresh single-key order", async (name) => {
  // The rows follow status, then rank, then title; status has no column.
  const list = renderList([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }, { field: "title", direction: "ASC" }]);
  await screen.findByText("Alpha");
  // The read asks for the whole order.
  expect(list.readOrder()).toEqual(["status asc", "rank asc", "title asc"]);
  expect(shown("Title", "Rank")).toEqual({ Title: "none", Rank: "none" });

  // The order holds the column ascending already; the press does not flip it, and the other keys go.
  press(name);
  // The list shows its last settled content until the new read answers.
  await waitFor(() => expect([list.readOrder(), shown(name)]).toEqual([[`${name.toLowerCase()} asc`], { [name]: "ascending" }]));
});

test("a second press while the read is in flight is decided from the table's live state", async () => {
  const list = renderList([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }, { field: "title", direction: "ASC" }]);
  await screen.findByText("Alpha");
  list.hold();
  press("Rank");
  await waitFor(() => expect(list.readOrder()).toEqual(["rank asc"]));
  // The list re-shows its last settled headers until the read answers.
  expect(shown("Rank")).toEqual({ Rank: "none" });
  // Rank now leads the table's order, so the press is the toggle's: descending.
  press("Rank");
  await waitFor(() => expect(list.readOrder()).toEqual(["rank desc"]));
  await list.release();
  await waitFor(() => expect(shown("Rank")).toEqual({ Rank: "descending" }));
});

test("a tie-breaker's first press sorts it in the column's first direction, which the table owns", () => {
  const query = ResourceQuery.fromContract(taskContract([]));
  function DescendingFirst() {
    const resourceView = useResourceView();
    const [sorting, setSorting] = React.useState<SortingState>([{ id: "status", desc: false }, { id: "rank", desc: false }]);
    const columns = React.useMemo(() => buildColumns<Row>(titleAndRank, { query }).map((column) => ({ ...column, sortDescFirst: true })), []);
    const table = useReactTable<Row>({ data: records as Row[], columns, state: { sorting }, onSortingChange: setSorting,
      enableMultiSort: false, manualSorting: true, getCoreRowModel: getCoreRowModel() });
    return <HeaderCells table={table} resourceView={resourceView} />;
  }
  render(<ResourceViewProvider scope="local"><DescendingFirst /></ResourceViewProvider>);
  expect(shown("Rank")).toEqual({ Rank: "none" });
  press("Rank");
  expect(shown("Rank")).toEqual({ Rank: "descending" });
});

test("a press on a column the order does not hold is the table's toggle, which sorts it ascending", async () => {
  const list = renderList([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }]);
  await screen.findByText("Alpha");
  expect(shown("Title", "Rank")).toEqual({ Title: "none", Rank: "none" });

  press("Title");
  await waitFor(() => expect([list.readOrder(), shown("Title", "Rank")]).toEqual([["title asc"], { Title: "ascending", Rank: "none" }]));
});

test("the column that leads the order shows its direction; the press cycle on a leading descending key is the table's", async () => {
  const list = renderList([{ field: "rank", direction: "DESC" }, { field: "title", direction: "ASC" }]);
  await screen.findByText("Alpha");
  expect(list.readOrder()).toEqual(["rank desc", "title asc"]);
  // The tie-breaker beside it shows none.
  expect(shown("Rank", "Title")).toEqual({ Rank: "descending", Title: "none" });

  // Each press, the order the next read asks for, and what the headers then show. The first
  // press sets a fresh single-key order (the tie-breaker goes), the third removes the sort.
  for (const step of [
    [["rank asc"], { Rank: "ascending", Title: "none" }],
    [["rank desc"], { Rank: "descending", Title: "none" }],
    [[], { Rank: "none", Title: "none" }],
  ]) {
    press("Rank");
    await waitFor(() => expect([list.readOrder(), shown("Rank", "Title")]).toEqual(step));
  }
});

test("a list grouped by the field its order leads with shows the sort of the next key", async () => {
  const list = renderList([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }],
    { columns: [...titleAndRank, withStatus[0]!], initialState: { groupStack: [{ field: "status" }] } });
  await screen.findByText("Charlie");
  // The grouping column keeps its place.
  expect(screen.getAllByRole("button", { name: /^Sort / }).map((button) => button.getAttribute("aria-label")?.split(" ")[1]))
    .toEqual(["Title", "Rank", "Status"]);
  // Each group's records are read in the whole order, and within a group they follow rank.
  expect(list.groupRowsOrder()).toEqual(["status asc", "rank asc"]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "none", Title: "none", Rank: "ascending" });

  // Rank is the order's last key and this list's state holds several keys: the press flips it in
  // place, and the group's key stays.
  press("Rank");
  await waitFor(() => expect([list.groupRowsOrder(), shown("Status", "Rank")])
    .toEqual([["status asc", "rank desc"], { Status: "none", Rank: "descending" }]));

  // The grouping column's press is the table's toggle: status descending alone. The server keeps
  // the groups in their own order, and the records' rank order goes.
  press("Status");
  await waitFor(() => expect([list.groupOrder(), list.groupRowsOrder(), shown("Status", "Rank")])
    .toEqual([["status asc"], ["status desc"], { Status: "none", Rank: "none" }]));
});

test("a list grouped by a field it shows no column for still shows the sort of the next key", async () => {
  renderList([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { initialState: { groupStack: [{ field: "status" }] } });
  await screen.findByText("Charlie");
  expect(shown("Title", "Rank")).toEqual({ Title: "none", Rank: "ascending" });
});

test("the server's flat table hands its headers the list's group axes too", async () => {
  const resource = taskResource([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }]);
  const metadata = schemaFieldMetadataFromDataResources([resource]);
  const groupStack: readonly ResourceViewGroup[] = [{ field: "status" }];
  function FlatServerHeaders() {
    const resourceView = useResourceView();
    const { table } = useResourceViewSurface({ resource: resource.modelLabel, modelMetadata: metadata.labels[resource.modelLabel]!,
      columns: titleAndRank, resourceView, groupStack });
    return <HeaderCells table={table} resourceView={resourceView} />;
  }
  render(<Provider metadata={metadata} dataProvider={{ getList: async () => ({ data: records as Row[], total: records.length }) }}>
    <ToastProvider><ResourceViewProvider resource={resource.modelLabel} scope="local"><FlatServerHeaders /></ResourceViewProvider></ToastProvider>
  </Provider>);
  await waitFor(() => expect(shown("Title", "Rank")).toEqual({ Title: "none", Rank: "ascending" }));
});

test("rows sorted in the browser keep the whole order, and a press sets a fresh single-key order", () => {
  renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }]);
  // Closed before open, and the open records by rank.
  expect(rowTitles()).toEqual(["Charlie", "Delta", "Bravo", "Alpha"]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "ascending", Title: "none", Rank: "none" });

  // The table's toggle on the shown key: status descending alone, so the open records keep their places.
  press("Status");
  expect(rowTitles()).toEqual(["Alpha", "Bravo", "Delta", "Charlie"]);
  expect(shown("Status", "Rank")).toEqual({ Status: "descending", Rank: "none" });
});

test("rows grouped in the browser by the field their order leads with show the next key's sort and follow it", () => {
  renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { grouped: true });
  for (const group of ["closed", "open"]) fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${group}`) }));
  expect(rowTitles()).toEqual(["Charlie", "Delta", "Bravo", "Alpha"]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "none", Title: "none", Rank: "ascending" });

  // Rank is the order's last key and this list's state holds several keys: the press flips it in
  // place, so the groups keep their status order.
  press("Rank");
  expect(rowTitles()).toEqual(["Charlie", "Alpha", "Bravo", "Delta"]);
  expect(shown("Status", "Rank")).toEqual({ Status: "none", Rank: "descending" });
});

test("a press on the grouping column of rows grouped in the browser is the table's toggle: the groups turn", () => {
  const list = renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { grouped: true });
  const groups = () => screen.getAllByRole("button", { name: /^(closed|open)/ }).map((group) => group.textContent?.match(/^[a-z]+/)?.[0]);
  expect(groups()).toEqual(["closed", "open"]);

  press("Status");
  expect([list.sorting(), groups()]).toEqual([[{ id: "status", desc: true }], ["open", "closed"]]);
  // Its header still shows no sort: in the browser its key orders the groups, not the records in them.
  expect(shown("Status", "Rank")).toEqual({ Status: "none", Rank: "none" });
});

test("a press on a tie-breaker of rows grouped in the browser sets that key alone: the grouping key goes with the rest, so the groups lose its order", () => {
  const list = renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }, { field: "title", direction: "ASC" }], { grouped: true });
  const groups = () => screen.getAllByRole("button", { name: /^(closed|open)/ }).map((group) => group.textContent?.match(/^[a-z]+/)?.[0]);
  expect(groups()).toEqual(["closed", "open"]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "none", Title: "none", Rank: "ascending" });

  press("Title");
  // A fresh single-key order: the open group's first record, Alpha, now sorts first.
  expect([list.sorting(), groups()]).toEqual([[{ id: "title", desc: false }], ["open", "closed"]]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "none", Title: "ascending", Rank: "none" });
});

test("rows grouped in the browser by a hidden column still show the sort of the next key", () => {
  renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { grouped: true, initialState: { columnVisibility: { status: false } } });
  expect(screen.queryByRole("columnheader", { name: /Status/ })).toBeNull();
  expect(shown("Title", "Rank")).toEqual({ Title: "none", Rank: "ascending" });
});

const due = { field: "due", header: "Due" }, created = { field: "created_at", header: "Created" }, owner = { field: "owner.name", header: "Owner" };
test.each([
  ["a Date by day: each day is one date, so the next key shows", [{ field: "due", granularity: "day" }], [{ field: "due", direction: "ASC" }], [due], { Due: "none", Title: "ascending" }],
  ["a DateTime by day: a day holds many instants, so the instant shows", [{ field: "created_at", granularity: "day" }], [{ field: "created_at", direction: "DESC" }], [created], { Created: "descending", Title: "none" }],
  ["a date by month: a month holds many dates, so the date shows", [{ field: "due", granularity: "month" }], [{ field: "due", direction: "ASC" }], [due], { Due: "ascending", Title: "none" }],
  ["a relation's member under the relation's axis: one owner's records share its name", [{ field: "owner" }], [{ field: "owner.name", direction: "ASC" }], [owner], { Owner: "none", Title: "ascending" }],
] as const)("rows grouped in the browser by %s", (_name, groupStack, lead, columns, expected) => {
  renderRows([], { query: datedQuery([...lead, { field: "title", direction: "ASC" }]), rows: dated,
    columns: [...columns, titleAndRank[0]!], initialState: { groupStack } });
  expect(shown(...Object.keys(expected))).toEqual(expected);
});

test("a client list seeded with a two-key order keeps it, and a press on its tie-breaker sorts by that key alone", async () => {
  renderList([], { rowModel: "client", initialState: { sorting: [{ id: "status", desc: false }, { id: "rank", desc: false }] } });
  await screen.findByText("Alpha");
  expect(rowTitles()).toEqual(["Charlie", "Delta", "Bravo", "Alpha"]);
  expect(shown("Title", "Rank")).toEqual({ Title: "none", Rank: "none" });

  // Rank is the order's last key, held ascending: the press sorts by rank alone, and status goes.
  press("Rank");
  expect(rowTitles()).toEqual(["Delta", "Bravo", "Alpha", "Charlie"]);
  expect(shown("Title", "Rank")).toEqual({ Title: "none", Rank: "ascending" });
});

test("on a list whose state lives in the URL, a press on the shown key behind a grouping key turns it descending and the read asks for it", async () => {
  const list = renderList([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { scope: "route", columns: withStatus,
    initialState: { groupStack: [{ field: "status" }], sorting: [{ id: "status", desc: false }, { id: "rank", desc: false }] } });
  await screen.findByText("Charlie");
  expect(shown("Rank")).toEqual({ Rank: "ascending" });
  press("Rank");
  await waitFor(() => expect([list.groupRowsOrder(), shown("Rank")]).toEqual([["status asc", "rank desc"], { Rank: "descending" }]));
  // The URL holds the whole order, grouping key first.
  expect(list.urlSort()).toBe("status:asc,rank:desc");
});

const statusThenRank: Order = [{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }];
test.each(["server", "client"] as const)("a reload of a %s-grouped route list restores the whole order the press left", async (rowModel) => {
  const initialState = { groupStack: [{ field: "status" }], sorting: [{ id: "status", desc: false }, { id: "rank", desc: false }] };
  const list = renderList(statusThenRank, { scope: "route", rowModel, columns: withStatus, initialState });
  await waitFor(() => expect(shown("Rank")).toEqual({ Rank: "ascending" }));
  press("Rank");
  await waitFor(() => expect(list.urlSort()).toBe("status:asc,rank:desc"));
  const href = list.href();
  cleanup(); clearClients();

  const reloaded = renderList(statusThenRank, { scope: "route", rowModel, columns: withStatus, initialState, path: href });
  await waitFor(() => expect(shown("Status", "Rank")).toEqual({ Status: "none", Rank: "descending" }));
  if (rowModel === "server") expect(reloaded.groupRowsOrder()).toEqual(["status asc", "rank desc"]);
});

test("a route list opened at a link holding several keys reads in that whole order", async () => {
  const list = renderList(statusThenRank, { scope: "route", path: "/tasks?sort=rank%3Adesc%2Ctitle%3Aasc" });
  await waitFor(() => expect(list.readOrder()).toEqual(["rank desc", "title asc"]));
  expect(shown("Rank", "Title")).toEqual({ Rank: "descending", Title: "none" });
});

test.each([
  ["a trailing comma", "rank%3Aasc%2C"],
  ["a key of three parts", "rank%3Aasc%3Atitle"],
  ["a padded field", "status%3Aasc%2C%20rank%3Adesc"],
  ["a duplicate field", "rank%3Aasc%2Crank%3Adesc"],
])("a route list opened at a link whose order has %s reads nothing and offers its reset", async (_name, sort) => {
  const list = renderList(statusThenRank, { scope: "route", path: `/tasks?sort=${sort}` });
  expect(await screen.findByRole("button", { name: "Reset filters, sorting and grouping" })).toBeTruthy();
  expect(list.reads()).toBe(0);
});

const rankThenTitle: Order = [{ field: "rank", direction: "DESC" }, { field: "title", direction: "ASC" }];
test("a saved view of a list in its inherited order stores no order, and applying it restores the order the list declares", async () => {
  // The list's state declares no order: it inherits the resource's, rank then title.
  const list = renderList(rankThenTitle, { scope: "route" });
  await waitFor(() => expect(list.readOrder()).toEqual(["rank desc", "title asc"]));
  const [saved] = resourceViewFavoritesFromUnknown([JSON.parse(JSON.stringify(favoriteFromResourceView(list.view().state, "Declared")))]);
  expect(saved).not.toHaveProperty("sort");
  press("Title");
  await waitFor(() => expect(list.readOrder()).toEqual(["title asc"]));

  act(() => list.view().applyFavorite(saved!));
  await waitFor(() => expect(list.readOrder()).toEqual(["rank desc", "title asc"]));
  expect([shown("Rank", "Title"), list.urlSort()]).toEqual([{ Rank: "descending", Title: "none" }, undefined]);
});

test("a saved view whose order is an explicit null clears the list's order", async () => {
  const list = renderList(rankThenTitle, { scope: "route", initialState: { sorting: [{ id: "rank", desc: true }, { id: "title", desc: false }] } });
  await waitFor(() => expect(list.readOrder()).toEqual(["rank desc", "title asc"]));
  act(() => list.view().applyFavorite({ id: "favorite:unordered", label: "Unordered", sort: null }));
  await waitFor(() => expect(list.readOrder()).toEqual([]));
  expect([shown("Rank", "Title"), list.urlSort()]).toEqual([{ Rank: "none", Title: "none" }, ""]);
});

test("on a list whose view inherits its order, a view of the inherited order and an unordered view are never active together", async () => {
  const list = renderList(rankThenTitle, { scope: "route" });
  await waitFor(() => expect(list.readOrder()).toEqual(["rank desc", "title asc"]));
  const [inherited] = resourceViewFavoritesFromUnknown([JSON.parse(JSON.stringify(favoriteFromResourceView(list.view().state, "Declared")))]);
  const unordered = { id: "favorite:unordered", label: "Unordered", sort: null };
  // What the search model shows active (`activeItems`): the match given the list's inherited order.
  const active = () => [inherited!, unordered].filter((view) => resourceViewFavoriteIsActive(view, list.view().state, list.view().inheritedSorting)).map(({ label }) => label);
  expect(active()).toEqual(["Declared"]);

  act(() => list.view().applyFavorite(unordered));
  await waitFor(() => expect(list.readOrder()).toEqual([]));
  expect(active()).toEqual(["Unordered"]);

  act(() => list.view().applyFavorite(inherited!));
  await waitFor(() => expect(list.readOrder()).toEqual(["rank desc", "title asc"]));
  expect(active()).toEqual(["Declared"]);
});
