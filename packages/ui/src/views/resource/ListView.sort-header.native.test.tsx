// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { getCoreRowModel, useReactTable, type SortingState } from "@tanstack/react-table";
import { ResourceQuery, schemaFieldMetadataFromDataResources, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ToastProvider } from "../../feedback";
import { PaneToolbarHost } from "../../layouts/PaneToolbar";
import { createUiTestProviders } from "../../testing";
import { ListView } from "./ListView";
import { RowsListView } from "./RowsListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { buildColumns, ListHeaderCell, listOrderChoice, type ListOrderChoice } from "./resource-view-list-body";
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
 * `initialState`, held by the list (`local`) or by the URL (`route`, opened at `path`), drawn
 * in a pane, its Order select in the pane's menu, with `pane`.
 */
function renderList(order: Order, { columns = titleAndRank, initialState = {}, rowModel = "server", scope = "local", path = "/tasks", pane = false }: {
  columns?: typeof withStatus; initialState?: ResourceViewInitialState; rowModel?: "server" | "client"; scope?: "local" | "route"; path?: string;
  pane?: boolean;
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
            {pane
              ? <PaneToolbarHost><ListView resource={resource.modelLabel} scope="inherit" columns={columns} pane={{ selector: "Tasks" }} /></PaneToolbarHost>
              : <ListView resource={resource.modelLabel} scope="inherit" columns={columns} />}
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
/**
 * What each header shows: the direction its sort button's name says ("none" for not sorted). Its `aria-sort` says the
 * same or "none", and at most one header's says a direction (`ariaSort`): a grouping column's says "none".
 */
function shown(...names: readonly string[]): Record<string, string> {
  const said = screen.getAllByRole("columnheader").filter((cell) => (cell.getAttribute("aria-sort") ?? "none") !== "none");
  expect(said.length).toBeLessThanOrEqual(1);
  return Object.fromEntries(names.map((name) => {
    const label = screen.getByRole("button", { name: new RegExp(`^Sort ${name} \\(`) }).getAttribute("aria-label") ?? "";
    const sort = /\(currently (ascending|descending)\)$/.exec(label)?.[1] ?? "none";
    expect([sort, "none"]).toContain(header(name).getAttribute("aria-sort"));
    return [name, sort];
  }));
}
/** The one header whose `aria-sort` says a direction, and that direction ("Rank ascending"); "" when none does. */
function ariaSort(): string {
  const cell = screen.getAllByRole("columnheader").find((candidate) => (candidate.getAttribute("aria-sort") ?? "none") !== "none");
  const name = cell?.querySelector("button[aria-label^='Sort ']")?.getAttribute("aria-label")?.split(" ")[1];
  return cell ? `${name} ${cell.getAttribute("aria-sort")}` : "";
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

test("a list grouped by the field its order leads with shows its groups' direction on the grouping column's button, and aria-sort only on the key its records follow", async () => {
  const list = renderList([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }],
    { columns: [...titleAndRank, withStatus[0]!], initialState: { groupStack: [{ field: "status" }] } });
  await screen.findByText("Charlie");
  // The grouping column keeps its place.
  expect(screen.getAllByRole("button", { name: /^Sort / }).map((button) => button.getAttribute("aria-label")?.split(" ")[1]))
    .toEqual(["Title", "Rank", "Status"]);
  // Each group's records are read in the whole order, and within a group they follow rank. The
  // grouping column's sort button and arrow show the direction its groups go in, since groups are
  // never unsorted; its `aria-sort` says "none", since WAI-ARIA puts aria-sort on one header at a
  // time, and that header is the key the records follow, here Rank's.
  expect(list.groupRowsOrder()).toEqual(["status asc", "rank asc"]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "ascending", Title: "none", Rank: "ascending" });
  expect([header("Status").getAttribute("aria-sort"), ariaSort()]).toEqual(["none", "Rank ascending"]);

  // Rank is the only key the rows follow: the press turns it, and the group's key stays behind it.
  press("Rank");
  await waitFor(() => expect([list.groupRowsOrder(), shown("Status", "Rank"), ariaSort()])
    .toEqual([["rank desc", "status asc"], { Status: "ascending", Rank: "descending" }, "Rank descending"]));

  // The grouping column's press turns its groups: status descending alone, which the groups read
  // asks for, and the records' rank order goes, so no header says a direction in its aria-sort.
  press("Status");
  await waitFor(() => expect([list.groupOrder(), list.groupRowsOrder(), shown("Status", "Rank"), ariaSort()])
    .toEqual([["status desc"], ["status desc"], { Status: "descending", Rank: "none" }, ""]));
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

test("rows grouped in the browser by the field their order leads with show the groups' direction and the next key's sort, and follow it", () => {
  renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { grouped: true });
  for (const group of ["closed", "open"]) fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${group}`) }));
  expect(rowTitles()).toEqual(["Charlie", "Delta", "Bravo", "Alpha"]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "ascending", Title: "none", Rank: "ascending" });

  // Rank is the only key the rows follow: the press turns it, and the groups keep their status order.
  press("Rank");
  expect(rowTitles()).toEqual(["Charlie", "Alpha", "Bravo", "Delta"]);
  expect(shown("Status", "Rank")).toEqual({ Status: "ascending", Rank: "descending" });
});

test("a press on the grouping column of rows grouped in the browser turns the groups, and its sort button shows their direction", () => {
  const list = renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { grouped: true });
  const groups = () => screen.getAllByRole("button", { name: /^(closed|open)/ }).map((group) => group.textContent?.match(/^[a-z]+/)?.[0]);
  expect(groups()).toEqual(["closed", "open"]);

  press("Status");
  expect([list.sorting(), groups()]).toEqual([[{ id: "status", desc: true }], ["open", "closed"]]);
  // Its key orders the groups, not the records in them: its button shows the groups' direction, and its aria-sort none.
  expect(shown("Status", "Rank")).toEqual({ Status: "descending", Rank: "none" });
  expect([header("Status").getAttribute("aria-sort"), ariaSort()]).toEqual(["none", ""]);
});

test("a press on a tie-breaker of rows grouped in the browser keeps the grouping key behind it, so the groups keep its order", () => {
  const list = renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }, { field: "title", direction: "ASC" }], { grouped: true });
  const groups = () => screen.getAllByRole("button", { name: /^(closed|open)/ }).map((group) => group.textContent?.match(/^[a-z]+/)?.[0]);
  expect(groups()).toEqual(["closed", "open"]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "ascending", Title: "none", Rank: "ascending" });

  press("Title");
  // The pressed column leads and the status key stays, behind it; the rank tie-breaker goes.
  expect([list.sorting(), groups()]).toEqual([[{ id: "title", desc: false }, { id: "status", desc: false }], ["closed", "open"]]);
  expect(shown("Status", "Title", "Rank")).toEqual({ Status: "ascending", Title: "ascending", Rank: "none" });
});

test("a grouping column's key turns in place at each press and is never removed: the groups turn back", () => {
  const list = renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { grouped: true });
  const groups = () => screen.getAllByRole("button", { name: /^(closed|open)/ }).map((group) => group.textContent?.match(/^[a-z]+/)?.[0]);
  press("Status");
  expect([list.sorting(), groups()]).toEqual([[{ id: "status", desc: true }], ["open", "closed"]]);
  press("Status");
  expect([list.sorting(), groups()]).toEqual([[{ id: "status", desc: false }], ["closed", "open"]]);
  press("Status");
  expect([list.sorting(), groups()]).toEqual([[{ id: "status", desc: true }], ["open", "closed"]]);
});

test("on rows grouped in the browser by two levels, a press on one level's column turns that level and keeps the other's key", () => {
  const query = ResourceQuery.forRows({ fields: { ...fields, kind: { scalar: "String" } } });
  const rows = records.map((row, index) => ({ ...row, kind: index % 2 ? "task" : "bug" }));
  const list = renderRows([], { query, rows, columns: [{ field: "status", header: "Status" }, { field: "kind", header: "Kind" }, { field: "title", header: "Title" }],
    initialState: { groupStack: [{ field: "status" }, { field: "kind" }], sorting: [{ id: "status", desc: false }] } });
  // The order holds no kind key, so Kind shows its groups ascending and its first press turns them
  // descending; the status key stays, behind it. Each level reads its own key wherever the order holds it.
  expect(shown("Status", "Kind")).toEqual({ Status: "ascending", Kind: "ascending" });
  press("Kind");
  expect(list.sorting()).toEqual([{ id: "kind", desc: true }, { id: "status", desc: false }]);
  // Status turns, and the kind key stays.
  press("Status");
  expect(list.sorting()).toEqual([{ id: "status", desc: true }, { id: "kind", desc: true }]);
  expect(shown("Status", "Kind")).toEqual({ Status: "descending", Kind: "descending" });
});

const pressCases = [
  // [the press, the declared order, the pressed column, the order it leaves, what the headers then show]
  ["a tie-breaker", "status rank title", "Title", "title:asc,status:asc", { Title: "ascending", Rank: "none" }],
  ["the shown key, not the order's last", "status rank title", "Rank", "rank:desc,status:asc", { Title: "none", Rank: "descending" }],
  ["the shown key, the order's last", "status rank", "Rank", "rank:desc,status:asc", { Title: "none", Rank: "descending" }],
  ["a column outside the order", "status rank", "Title", "title:asc,status:asc", { Title: "ascending", Rank: "none" }],
] as const;
test.each((["client", "server"] as const).flatMap((rowModel) => pressCases.map((press) => [rowModel, ...press] as const)))(
  "on a route list grouped by status (%s row model), a press on %s keeps the grouping key behind it: the groups keep their order, the column shows its direction, the URL and a reload agree",
  async (rowModel, _name, declared, column, sort, headers) => {
    const order: Order = declared.split(" ").map((field) => ({ field, direction: "ASC" }));
    const initialState = { groupStack: [{ field: "status" }], sorting: order.map(({ field }) => ({ id: field, desc: false })) };
    // In the browser the groups' order is the table's; on the server, the order the groups read asks for.
    const groups = (rendered: ReturnType<typeof renderList>) => rowModel === "client"
      ? screen.queryAllByRole("button", { name: /^(closed|open)/ }).map((group) => group.textContent?.match(/^[a-z]+/)?.[0])
      : rendered.groupOrder();
    const groupsInStatusOrder = rowModel === "client" ? ["closed", "open"] : ["status asc"];
    const recordsRead = sort.split(",").map((key) => key.replace(":", " "));
    const list = renderList(order, { scope: "route", rowModel, columns: withStatus, initialState });
    await waitFor(() => expect([shown("Rank"), groups(list)]).toEqual([{ Rank: "ascending" }, groupsInStatusOrder]));
    press(column);
    await waitFor(() => expect(list.urlSort()).toBe(sort));
    await waitFor(() => expect([shown("Status", "Title", "Rank"), groups(list)]).toEqual([{ Status: "ascending", ...headers }, groupsInStatusOrder]));
    if (rowModel === "server") await waitFor(() => expect(list.groupRowsOrder()).toEqual(recordsRead));

    const href = list.href();
    cleanup(); clearClients();
    const reloaded = renderList(order, { scope: "route", rowModel, columns: withStatus, initialState, path: href });
    await waitFor(() => expect([shown("Status", "Title", "Rank"), groups(reloaded)]).toEqual([{ Status: "ascending", ...headers }, groupsInStatusOrder]));
    if (rowModel === "server") expect(reloaded.groupRowsOrder()).toEqual(recordsRead);
  },
);

/** The Order select's checked choice in the pane's menu, opening the menu if it is closed. */
async function checkedOrder(): Promise<string | undefined> {
  if (!screen.queryByRole("group", { name: "Order" })) fireEvent.click(screen.getByRole("button", { name: "More" }));
  const order = within(await screen.findByRole("group", { name: "Order" }));
  return order.queryAllByRole("menuitemradio").find((item) => item.getAttribute("aria-checked") === "true")?.textContent ?? undefined;
}

test.each([
  ["an order of several keys", "rank desc, title asc", [], true, "Rank, descending"],
  ["a grouping key ahead of the shown one", "status asc, rank asc", [{ field: "status" }], true, "Rank, ascending"],
  ["a grouping key ahead of the shown one, the order inherited", "status asc, rank asc", [{ field: "status" }], false, "Rank, ascending"],
] as const)("the pane's Order select checks what the headers show, with %s", async (_name, declared, groupStack, seeded, checked) => {
  const order: Order = declared.split(", ").map((key) => {
    const [field, dir] = key.split(" ");
    return { field: field!, direction: dir === "desc" ? "DESC" : "ASC" };
  });
  const sorting = order.map(({ field, direction }) => ({ id: field, desc: direction === "DESC" }));
  renderList(order, { pane: true, columns: withStatus, initialState: { groupStack: [...groupStack], ...(seeded ? { sorting } : {}) } });
  await screen.findByText("Charlie");
  expect(await checkedOrder()).toBe(checked);
});

test.each(["server", "client"] as const)("choosing in the Order select of a list grouped by status (%s row model) keeps the grouping key behind the choice, as a press does", async (rowModel) => {
  const list = renderList(statusThenRank, { pane: true, scope: "route", rowModel, columns: withStatus,
    initialState: { groupStack: [{ field: "status" }], sorting: [{ id: "status", desc: false }, { id: "rank", desc: false }] } });
  await waitFor(() => expect(shown("Rank")).toEqual({ Rank: "ascending" }));
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(await screen.findByRole("menuitemradio", { name: "Title, descending" }));
  await waitFor(() => expect(list.urlSort()).toBe("title:desc,status:asc"));
  if (rowModel === "server") await waitFor(() => expect(list.groupRowsOrder()).toEqual(["title desc", "status asc"]));
  await waitFor(() => expect(shown("Status", "Title", "Rank")).toEqual({ Status: "ascending", Title: "descending", Rank: "none" }));
  expect(await checkedOrder()).toBe("Title, descending");
});

test("the Order select offers only the keys that order the records, and choosing what it shows changes nothing", async () => {
  const order: Order = ["status", "rank", "title"].map((field) => ({ field, direction: "ASC" }));
  const list = renderList(order, { pane: true, rowModel: "client", columns: withStatus,
    initialState: { groupStack: [{ field: "status" }], sorting: order.map(({ field }) => ({ id: field, desc: false })) } });
  await waitFor(() => expect(shown("Rank")).toEqual({ Rank: "ascending" }));
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  const select = within(await screen.findByRole("group", { name: "Order" }));
  // The status key is one every group's records share: it orders the groups, which its own header
  // turns, not the records the select orders, so it is not offered.
  expect(select.getAllByRole("menuitemradio").map((item) => item.textContent))
    .toEqual(["Title, ascending", "Title, descending", "Rank, ascending", "Rank, descending"]);
  fireEvent.click(select.getByRole("menuitemradio", { name: "Rank, ascending" }));
  expect(list.view().state.sorting).toEqual(order.map(({ field }) => ({ id: field, desc: false })));
});

test("the Order select chooses on the column the header presses, and only one its header can sort", () => {
  const query = ResourceQuery.fromContract(taskContract([]));
  const choice: { current?: ListOrderChoice } = {};
  let sorting: SortingState = [{ id: "status", desc: false }, { id: "rank", desc: false }];
  function Choice() {
    const [state, setState] = React.useState<SortingState>(sorting);
    sorting = state;
    // A rank column under its own id, and a note column the resource cannot sort by.
    const columns = React.useMemo(() => buildColumns<Row>([{ id: "position", field: "rank", header: "Rank" }, { field: "note", header: "Note" }, ...withStatus.slice(0, 2)], { query }), []);
    const table = useReactTable<Row>({ data: records as Row[], columns, state: { sorting: state }, onSortingChange: setState,
      meta: { groupAxes: [query.group({ field: "status" })] }, enableMultiSort: false, manualSorting: true, getCoreRowModel: getCoreRowModel() });
    choice.current = listOrderChoice(table);
    return null;
  }
  render(<Choice />);
  expect(["rank", "note", "status", "title"].map((field) => choice.current!.offers(field))).toEqual([true, false, false, true]);
  act(() => choice.current!.choose("note", true));
  act(() => choice.current!.choose("status", true));
  expect(sorting).toEqual([{ id: "status", desc: false }, { id: "rank", desc: false }]);
  act(() => choice.current!.choose("rank", true));
  // A table of its own that sets no `meta.sorting`: its sorting is the list's order, the group key kept behind the choice.
  expect(sorting).toEqual([{ id: "position", desc: true }, { id: "status", desc: false }]);
});

test("rows grouped in the browser by a hidden column still show the sort of the next key", () => {
  renderRows([{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }], { grouped: true, initialState: { columnVisibility: { status: false } } });
  expect(screen.queryByRole("columnheader", { name: /Status/ })).toBeNull();
  expect(shown("Title", "Rank")).toEqual({ Title: "none", Rank: "ascending" });
});

const due = { field: "due", header: "Due" }, created = { field: "created_at", header: "Created" }, owner = { field: "owner.name", header: "Owner" };
test.each([
  ["a Date by day: each day is one date, so the date shows the days' direction and the next key its sort", [{ field: "due", granularity: "day" }], [{ field: "due", direction: "ASC" }], [due], { Due: "ascending", Title: "ascending" }, "Title ascending"],
  ["a DateTime by day: a day holds many instants, so the instant shows", [{ field: "created_at", granularity: "day" }], [{ field: "created_at", direction: "DESC" }], [created], { Created: "descending", Title: "none" }, "Created descending"],
  ["a date by month: a month holds many dates, so the date shows", [{ field: "due", granularity: "month" }], [{ field: "due", direction: "ASC" }], [due], { Due: "ascending", Title: "none" }, "Due ascending"],
  ["a relation's member under the relation's axis: one owner's records share its name, which shows the groups' direction", [{ field: "owner" }], [{ field: "owner.name", direction: "ASC" }], [owner], { Owner: "ascending", Title: "ascending" }, "Title ascending"],
] as const)("rows grouped in the browser by %s", (_name, groupStack, lead, columns, expected, said) => {
  renderRows([], { query: datedQuery([...lead, { field: "title", direction: "ASC" }]), rows: dated,
    columns: [...columns, titleAndRank[0]!], initialState: { groupStack } });
  expect(shown(...Object.keys(expected))).toEqual(expected);
  // Only the key the records follow says its direction in its aria-sort.
  expect(ariaSort()).toBe(said);
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
  await waitFor(() => expect([list.groupRowsOrder(), shown("Rank")]).toEqual([["rank desc", "status asc"], { Rank: "descending" }]));
  // The URL holds the whole order, the pressed key first and the grouping key behind it.
  expect(list.urlSort()).toBe("rank:desc,status:asc");
});

const statusThenRank: Order = [{ field: "status", direction: "ASC" }, { field: "rank", direction: "ASC" }];
test.each(["server", "client"] as const)("a reload of a %s-grouped route list restores the whole order the press left", async (rowModel) => {
  const initialState = { groupStack: [{ field: "status" }], sorting: [{ id: "status", desc: false }, { id: "rank", desc: false }] };
  const list = renderList(statusThenRank, { scope: "route", rowModel, columns: withStatus, initialState });
  await waitFor(() => expect(shown("Rank")).toEqual({ Rank: "ascending" }));
  press("Rank");
  await waitFor(() => expect(list.urlSort()).toBe("rank:desc,status:asc"));
  const href = list.href();
  cleanup(); clearClients();

  const reloaded = renderList(statusThenRank, { scope: "route", rowModel, columns: withStatus, initialState, path: href });
  await waitFor(() => expect(shown("Status", "Rank")).toEqual({ Status: "ascending", Rank: "descending" }));
  if (rowModel === "server") expect(reloaded.groupRowsOrder()).toEqual(["rank desc", "status asc"]);
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
