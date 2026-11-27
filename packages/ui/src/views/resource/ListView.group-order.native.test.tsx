// @vitest-environment happy-dom
// A grouped list orders its groups by their own axis (`GroupAxis.direction`), on the server and in the browser.
// Each list is the real ListView, RowsListView or CollectionTreeView over a data provider only: the table, the surface
// and the table state are the list's own (one row reads the table state alone). The provider's groups read honours the
// order it is asked for, as a server does.

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createTable, getCoreRowModel, getGroupedRowModel, getSortedRowModel, type OnChangeFn, type SortingState } from "@tanstack/react-table";
import { gql } from "graphql-tag";
import { ResourceQuery, type DataResourceFieldMetadata, type LocalQueryField, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider, type TypedDocumentNode } from "@angee/refine";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ToastProvider } from "../../feedback";
import { PaneToolbarHost } from "../../layouts/PaneToolbar";
import { createUiTestProviders } from "../../testing";
import { CollectionTreeView } from "../tree/CollectionTreeView";
import { collectionQuery, type CollectionPage, type CollectionPageRequest, type CollectionSource } from "./collection-source";
import { ListView } from "./ListView";
import { RowsListView } from "./RowsListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { favoriteFromResourceView } from "./model/favorites";
import { resourceViewStateToSearch, type ResourceViewGroup, type ResourceViewInitialState } from "./resource-view-model";
import { useResourceViewTableState } from "./surface/table-state";
import { buildColumns } from "./list-body/columns";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://group-order" });
/** The list's scroll viewport, in pixels: the document has no layout, and the list's virtualizer windows by it. */
let viewport = 600;
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("resource-table-scroll") ? viewport : 0;
  });
});
afterEach(() => { cleanup(); clearClients(); vi.restoreAllMocks(); viewport = 600; });

/**
 * Eight tasks in the order the provider answers them, chosen so that each way a list could order its status groups
 * gives another order: by status blocked, closed, open, pending; by first appearance open, pending, closed, blocked;
 * by the sum of their ranks closed, open, pending, blocked; by their first record's title open, blocked, closed,
 * pending. Owners' names and identities, and priorities' labels and keys, order differently too.
 */
const TASKS: readonly Row[] = [
  { id: "a", title: "Alpha", status: "open", rank: 2, kind: "bug", due: "2026-03-20", owner: { id: "o3", name: "Mia" }, priority: "p2" },
  { id: "p", title: "Papa", status: "pending", rank: 6, kind: "task", due: "2026-01-15", owner: { id: "o1", name: "Zed" }, priority: "p1" },
  { id: "c", title: "Charlie", status: "closed", rank: 1, kind: "task", due: "2026-04-10", owner: { id: "o4", name: "Bob" }, priority: "p3" },
  { id: "b", title: "Bravo", status: "blocked", rank: 9, kind: "bug", due: "2026-02-02", owner: { id: "o2", name: "Ann" }, priority: "p4" },
  { id: "d", title: "Delta", status: "open", rank: 0, kind: "task", due: "2026-01-28", owner: { id: "o1", name: "Zed" }, priority: "p1" },
  { id: "k", title: "Kilo", status: "pending", rank: 7, kind: "bug", due: "2026-04-25", owner: { id: "o3", name: "Mia" }, priority: "p3" },
  { id: "e", title: "Echo", status: "closed", rank: 0, kind: "bug", due: "2026-02-11", owner: { id: "o2", name: "Ann" }, priority: "p2" },
  { id: "l", title: "Lima", status: "blocked", rank: 5, kind: "task", due: "2026-03-05", owner: { id: "o4", name: "Bob" }, priority: "p4" },
];
/** Two tasks with no status, owner or priority; one is second, so first appearance never puts them last. */
const WITH_EMPTY: readonly Row[] = [
  TASKS[0]!, { id: "n", title: "Nova", status: null, rank: 3, kind: "task", due: "2026-02-20", owner: null, priority: null },
  ...TASKS.slice(1), { id: "o", title: "Oscar", status: null, rank: 8, kind: "bug", due: "2026-03-28", owner: null, priority: null },
];
/** Thirty-six more tasks, none of which falls in a first group under any order these tests read. */
const FILLERS: readonly Row[] = Array.from({ length: 36 }, (_, index) => ({
  id: `z${index}`, title: `Zulu ${String(index + 1).padStart(2, "0")}`, status: index % 2 ? "open" : "closed",
  rank: (index * 7) % 13, kind: index % 3 ? "bug" : "task", due: `2026-0${2 + (index % 2)}-${String((index % 27) + 1).padStart(2, "0")}`,
  owner: index % 2 ? { id: "o3", name: "Mia" } : { id: "o4", name: "Bob" }, priority: index % 2 ? "p2" : "p3",
}));
/** More tasks than the table library samples to choose a column's sort function (it reads the rows after the tenth). */
const MANY: readonly Row[] = [...TASKS, ...FILLERS];

const PRIORITIES = [
  { value: "p1", description: "Urgent" }, { value: "p2", description: "Low" },
  { value: "p3", description: "Medium" }, { value: "p4", description: "High" },
];
const FIELDS: Readonly<Record<string, LocalQueryField>> = {
  id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" }, rank: { scalar: "Int" },
  kind: { scalar: "String" }, due: { scalar: "Date" },
  owner: { kind: "relation", identityPath: "owner.id", labelPath: "owner.name" }, "owner.name": { scalar: "String" },
  priority: { kind: "enum", values: PRIORITIES },
};
const HEADERS: Readonly<Record<string, string>> = {
  status: "Status", title: "Title", rank: "Rank", kind: "Kind", due: "Due", "owner.name": "Owner", priority: "Priority",
};

type Order = { field: string; direction: "ASC" | "DESC" }[];
/** "field dir, ..." as an order. */
function order(spec: string): Order {
  return spec ? spec.split(", ").map((key) => {
    const [field, direction] = key.split(" ");
    return { field: field!, direction: direction === "desc" ? "DESC" : "ASC" };
  }) : [];
}

/**
 * The tasks' query, its default order `declared`: every field sortable but those `unsortable` names; status, kind,
 * owner, priority and the due date grouped on the server too.
 */
function taskContract(declared: Order, unsortable: readonly string[] = []) {
  const contract = ResourceQuery.forRows({ fields: FIELDS }).contract;
  for (const field of unsortable) contract.fields[field] = { ...contract.fields[field]!, sort: null };
  for (const field of ["status", "kind", "priority"]) {
    contract.axes[field]!.server = { input: field.toUpperCase(), key: field };
    contract.axes[field]!.drill = { kind: "value", field, valueKey: field, nullMode: "isNull", valueMap: [] };
  }
  contract.axes.owner!.server = { input: "OWNER", key: "owner_id", labelInput: "OWNER__NAME", labelKey: "owner__name" };
  contract.axes.owner!.drill = { kind: "identity", field: "owner", valueKey: "owner_id", nullMode: "isNull", valueMap: [] };
  contract.axes.due!.server = { input: "DUE", key: "due" };
  return { ...contract, sort: { ...contract.sort, default: declared } };
}
const metaField = (name: string, extra: Partial<DataResourceFieldMetadata> = {}): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar: "String", readable: true, aggregatable: false, creatable: false, updatable: false,
  requiredOnCreate: false, ...extra,
});
function taskResource(declared: Order, rowModel: "server" | "client", unsortable: readonly string[] = []) {
  return testDataResource("notes.Task", {
    roots: { aggregate: "tasks_aggregate", groups: "tasks_groups" }, typeNames: { filter: "TaskBoolExp", order: "TaskOrderBy" },
    query: taskContract(declared, unsortable), recordRepresentation: "title", rowModel,
    fields: [metaField("id"), metaField("title"), metaField("status"), metaField("rank", { scalar: "Int" }), metaField("kind"),
      metaField("due", { scalar: "Date" }), metaField("owner.name"), metaField("priority", { kind: "enum", scalar: null, values: PRIORITIES })],
  });
}

function readPath(row: unknown, path: string): unknown {
  let current = row;
  for (const key of path.split(".")) {
    if (current == null || typeof current !== "object") return null;
    current = (current as Record<string, unknown>)[key];
  }
  return current ?? null;
}
function compare(left: unknown, right: unknown): number {
  if (typeof left === "number" && typeof right === "number") return left - right;
  const [a, b] = [String(left), String(right)];
  return a === b ? 0 : a > b ? 1 : -1;
}
/** One group dimension's key from a record, as the groups read keys a bucket. */
const DIMENSIONS: Readonly<Record<string, (row: Row) => Record<string, unknown>>> = {
  OWNER: (row) => ({ owner_id: readPath(row, "owner.id") }),
  OWNER__NAME: (row) => ({ owner__name: readPath(row, "owner.name") }),
  // A date by month: the bucket's first day, under the extraction's key.
  "DUE:MONTH": (row) => ({ month: row.due == null ? null : `${String(row.due).slice(0, 7)}-01` }),
};
/** A drilled parent's `where`: its `_eq` and `_is_null` clauses. */
function matches(row: Row, where: unknown): boolean {
  if (!where || typeof where !== "object") return true;
  return Object.entries(where as Record<string, unknown>).every(([field, clause]) => {
    if (field === "_and") return (clause as unknown[]).every((part) => matches(row, part));
    const ops = (clause ?? {}) as Record<string, unknown>;
    if ("_eq" in ops) return readPath(row, field) === ops._eq;
    if ("_is_null" in ops) return (readPath(row, field) === null) === ops._is_null;
    return true;
  });
}
interface GroupsVariables {
  group_by?: { field: string; granularity?: string }[]; where?: unknown; with_rows?: boolean; limit?: number; offset?: number;
  order_by?: { field: string; direction?: string; nulls?: string }[]; rows_order_by?: Record<string, string>;
}
/** A data provider over `rows`: the list read answers in their order; the groups read honours its order. */
function wire(rows: readonly Row[]) {
  const getList = vi.fn(async (_params: GetListParams) => ({ data: rows as Row[], total: rows.length }));
  const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
    const variables = (meta?.gqlVariables ?? {}) as GroupsVariables;
    const buckets = new Map<string, { key: Record<string, unknown>; rows: Row[] }>();
    for (const row of rows.filter((candidate) => matches(candidate, variables.where))) {
      const key = Object.assign({}, ...(variables.group_by ?? []).map(({ field, granularity }) =>
        DIMENSIONS[granularity ? `${field}:${granularity}` : field]?.(row) ?? { [field.toLowerCase()]: readPath(row, field.toLowerCase()) }));
      const id = JSON.stringify(key);
      const bucket = buckets.get(id);
      if (bucket) bucket.rows.push(row);
      else buckets.set(id, { key, rows: [row] });
    }
    const sorted = [...buckets.values()].sort((left, right) => {
      for (const { field, direction = "ASC", nulls = "LAST" } of variables.order_by ?? []) {
        const [a, b] = [left.key[field] ?? null, right.key[field] ?? null];
        if (a === null || b === null) {
          if (a === b) continue;
          return (a === null) === (nulls === "LAST") ? 1 : -1;
        }
        const order = compare(a, b);
        if (order !== 0) return direction === "DESC" ? -order : order;
      }
      return 0;
    });
    const page = sorted.slice(variables.offset ?? 0, (variables.offset ?? 0) + (variables.limit ?? sorted.length));
    // Each group's records, as PostgreSQL orders them: ascending nulls last, descending nulls first.
    const recordOrder = Object.entries(variables.rows_order_by ?? {});
    const ordered = (members: readonly Row[]) => [...members].sort((left, right) => {
      for (const [field, direction] of recordOrder) {
        const [a, b] = [readPath(left, field), readPath(right, field)];
        if (a === null || b === null) {
          if (a === b) continue;
          return (a === null) === (direction !== "desc") ? 1 : -1;
        }
        const order = compare(a, b);
        if (order !== 0) return direction === "desc" ? -order : order;
      }
      return 0;
    });
    return { data: {
      tasks_groups: page.map(({ key, rows: members }) => ({ key, aggregate: { count: members.length }, rows: ordered(members) })),
      totalCount: sorted.length,
    } };
  });
  return { getList, custom };
}

type Kind = "server" | "client" | "rows";
interface Mounted {
  view: () => ResourceViewContextValue;
  custom: ReturnType<typeof wire>["custom"];
  initial: ResourceViewInitialState;
  titles: ReadonlySet<string>;
}
/**
 * One list over the tasks: `server` and `client` are a ListView over the resource in that row model, its view state
 * seeded with the declared order as a ListView that owns its scope seeds it; `rows` is a RowsListView over the same
 * query, its view state unseeded, as RowsListView leaves it. `regroup` opens it ungrouped and then groups it by
 * `groupStack` through the command the toolbar's "Group by" ends in; `unsortable` names fields no read can sort by.
 */
async function mount({ kind, declared, groupStack, columns, pane = false, rows = TASKS, regroup = false, unsortable = [] }: {
  kind: Kind; declared: string; groupStack: readonly ResourceViewGroup[]; columns: readonly string[];
  pane?: boolean; rows?: readonly Row[]; regroup?: boolean; unsortable?: readonly string[];
}): Promise<Mounted> {
  const declaredOrder = order(declared);
  const resource = taskResource(declaredOrder, kind === "server" ? "server" : "client", unsortable);
  const { getList, custom } = wire(rows);
  const view: { current?: ResourceViewContextValue } = {};
  function ViewState() { view.current = useResourceView(); return null; }
  const initial: ResourceViewInitialState = {
    groupStack: regroup ? [] : [...groupStack],
    ...(kind === "rows" ? {} : { sorting: declaredOrder.map(({ field, direction }) => ({ id: field, desc: direction === "DESC" })) }),
  };
  const listColumns = columns.map((field) => ({ field, header: HEADERS[field]! }));
  const list = kind === "rows"
    ? <RowsListView scope="inherit" rows={rows as { id: string }[]} query={ResourceQuery.fromContract(taskContract(declaredOrder, unsortable))} columns={listColumns} />
    : <ListView resource={resource.modelLabel} scope="inherit" columns={listColumns} {...(pane ? { pane: { selector: "Tasks" } } : {})} />;
  render(
    <Provider resources={[resource]} dataProvider={{ getList, custom }} queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <OperationDocumentsProvider documents={{ console: { groups: { "notes.Task": "query Groups { tasks_groups { key } totalCount }" } } }}>
        <ToastProvider>
          <ResourceViewProvider resource={kind === "rows" ? undefined : resource.modelLabel} scope="local" initialState={initial}>
            <ViewState />
            {pane ? <PaneToolbarHost>{list}</PaneToolbarHost> : list}
          </ResourceViewProvider>
        </ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
  await settled();
  if (regroup) {
    act(() => view.current!.setGroupStack(groupStack));
    await settled();
  }
  await openAll();
  return { view: () => view.current!, custom, initial, titles: new Set(rows.map((row) => String(row.title))) };
}

/** Until the document stops changing for three tasks in a row: the list's reads have answered. */
async function settled(limit = 80): Promise<void> {
  for (let task = 0, quiet = 0; task < limit && quiet < 3; task += 1) {
    let changed = false;
    const observer = new MutationObserver(() => { changed = true; });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    observer.disconnect();
    quiet = changed ? 0 : quiet + 1;
  }
}

/** Open every closed group, level by level (a browser list's groups start closed). */
async function openAll(): Promise<void> {
  for (let round = 0; round < 6; round += 1) {
    const closed = [...document.querySelectorAll("tbody tr button[aria-expanded='false']")]
      .filter((button) => button.getAttribute("aria-disabled") !== "true");
    if (closed.length === 0) return;
    for (const button of closed) fireEvent.click(button);
    await settled();
  }
}

const spell = (sorting: readonly { id: string; desc: boolean }[] | undefined) =>
  sorting === undefined ? "(inherited)" : sorting.map(({ id, desc }) => `${id} ${desc ? "desc" : "asc"}`).join(", ");
type Stored = { field: string; dir: string } | readonly { field: string; dir: string }[] | null | undefined;
const spellStored = (sort: Stored) => sort === undefined ? "(inherited)" : sort === null ? ""
  : (Array.isArray(sort) ? sort : [sort]).map(({ field, dir }) => `${field} ${dir}`).join(", ");

interface Look {
  /** The groups in the order the list shows them; an inner level's prefixed with "· " per level. */
  groups: string[];
  /** The records the first group holds, in the order it shows them. */
  first: string[];
  /** The list's order as its view holds it: what its URL and a saved view hold. */
  sorting: string;
  /**
   * What each header shows, by its label: the direction its sort button's name says, "ascending", "descending", or
   * "none" for not sorted; a header with no sort button left out.
   */
  headers: Record<string, string>;
  /** The one header whose `aria-sort` says a direction, and that direction ("Rank ascending"); absent when none does. */
  ariaSort?: string;
  /** A server list's groups read: the order it asks its groups in. */
  read?: string[];
}
/** What the list shows and holds now. */
function look(mounted: Mounted): Look {
  const groups: string[] = [];
  const first: string[] = [];
  let tops = 0;
  for (const row of document.querySelectorAll("tbody tr")) {
    const toggle = row.querySelector<HTMLElement>("button[aria-expanded]");
    if (toggle) {
      const label = toggle.getAttribute("aria-label") ?? toggle.querySelector(".truncate")?.textContent ?? "";
      const indent = toggle.style.paddingLeft || row.querySelector<HTMLElement>("td[style]")?.style.paddingLeft || "";
      const depth = indent ? Math.round((parseFloat(indent) - 0.75) / 1.25) : 0;
      if (depth === 0) tops += 1;
      groups.push(`${"· ".repeat(depth)}${label}`);
      continue;
    }
    const title = [...row.querySelectorAll("td")].map((cell) => cell.textContent?.trim() ?? "").find((text) => mounted.titles.has(text));
    if (title && tops === 1) first.push(title);
  }
  const state = mounted.view().state;
  // The URL and a saved view hold the list's order as it is, an axis key never added.
  expect(spellStored(favoriteFromResourceView(state, "check").sort as Stored)).toBe(spell(state.sorting));
  const url = (resourceViewStateToSearch(state, mounted.initial) as { sort?: string }).sort;
  if (url !== undefined) expect(url).toBe((state.sorting ?? []).map(({ id, desc }) => `${id}:${desc ? "desc" : "asc"}`).join(","));
  const cells = [...document.querySelectorAll("th[aria-sort]")].map((cell) => ({
    label: (cell.textContent ?? "").replace(/\s+/g, " ").trim(),
    said: cell.getAttribute("aria-sort"),
    named: cell.querySelector("button[aria-label^='Sort ']")?.getAttribute("aria-label")?.match(/\((?:currently (ascending|descending)|not sorted)\)$/),
  })).filter(({ label }) => label);
  const headers = Object.fromEntries(cells.flatMap(({ label, named }) => named ? [[label, named[1] ?? "none"]] : []));
  // WAI-ARIA's one header at a time: at most one header's `aria-sort` says a direction, the one its button shows.
  const saying = cells.filter(({ said }) => said !== "none");
  expect(saying.length).toBeLessThanOrEqual(1);
  for (const { label, said } of saying) expect(said).toBe(headers[label]);
  const ariaSort = saying.map(({ label, said }) => `${label} ${said}`)[0];
  const read = mounted.custom.mock.calls.map(([call]) => call.meta?.gqlVariables as GroupsVariables)
    .filter((variables) => variables?.with_rows).at(-1)?.order_by
    ?.map(({ field, direction = "ASC", nulls }) => `${field} ${direction.toLowerCase()}${nulls ? ` nulls ${nulls.toLowerCase()}` : ""}`);
  return { groups, first, sorting: spell(state.sorting), headers, ...(ariaSort ? { ariaSort } : {}), ...(read ? { read } : {}) };
}

/** Each top-level group's records, in the order the list shows them, by the group's label. */
function recordsByGroup(mounted: Mounted): Record<string, string[]> {
  const records: Record<string, string[]> = {};
  let current: string[] | undefined;
  for (const row of document.querySelectorAll("tbody tr")) {
    const toggle = row.querySelector<HTMLElement>("button[aria-expanded]");
    if (toggle) {
      const indent = toggle.style.paddingLeft || row.querySelector<HTMLElement>("td[style]")?.style.paddingLeft || "";
      if (!indent || Math.round((parseFloat(indent) - 0.75) / 1.25) === 0) {
        current = records[toggle.getAttribute("aria-label") ?? toggle.querySelector(".truncate")?.textContent ?? ""] = [];
      }
      continue;
    }
    const title = [...row.querySelectorAll("td")].map((cell) => cell.textContent?.trim() ?? "").find((text) => mounted.titles.has(text));
    if (title) current?.push(title);
  }
  return records;
}

/** Press a header's sort button, then look once the list has settled and its groups are open. */
async function press(mounted: Mounted, name: string): Promise<Look> {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^Sort ${name} `) }));
  await settled();
  await openAll();
  return look(mounted);
}

/** The pane menu's Order group, `[label, aria-checked]` each, opening the menu when it is closed. */
async function orderItems(): Promise<[string, string][]> {
  if (!screen.queryByRole("group", { name: "Order" })) fireEvent.click(screen.getByRole("button", { name: "More" }));
  const group = within(await screen.findByRole("group", { name: "Order" }));
  return group.getAllByRole("menuitemradio").map((item) => [item.textContent ?? "", item.getAttribute("aria-checked") ?? ""]);
}
/** Choose in the pane's Order select, then look once the list has settled. */
async function choose(mounted: Mounted, name: string): Promise<Look> {
  await orderItems();
  fireEvent.click(within(await screen.findByRole("group", { name: "Order" })).getByRole("menuitemradio", { name }));
  await settled();
  await openAll();
  return look(mounted);
}

const ASC = ["blocked", "closed", "open", "pending"];
const DESC = ["pending", "open", "closed", "blocked"];
const STATUS_COLUMNS = ["status", "title", "rank"];
const byStatus = [{ field: "status" }];
/** Two levels' groups, each inner level under each outer group. */
const levels = (outer: readonly string[], inner: readonly string[]) => outer.flatMap((group) => [group, ...inner.map((label) => `· ${label}`)]);

test("1. a server list whose order leads with the group key: a press on the grouping column turns the groups read, and its button says so", async () => {
  const list = await mount({ kind: "server", declared: "status asc, rank asc", groupStack: byStatus, columns: STATUS_COLUMNS });
  expect(look(list)).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: "status asc, rank asc",
    headers: { Status: "ascending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending", read: ["status asc nulls last"] });
  // The grouping column's press is the table's single sort: the rank tie-breaker goes, and no key the records follow is left.
  expect(await press(list, "Status")).toEqual({ groups: DESC, first: ["Papa", "Kilo"], sorting: "status desc",
    headers: { Status: "descending", Title: "none", Rank: "none" }, read: ["status desc nulls last"] });
  expect(await press(list, "Status")).toEqual({ groups: ASC, first: ["Bravo", "Lima"], sorting: "status asc",
    headers: { Status: "ascending", Title: "none", Rank: "none" }, read: ["status asc nulls last"] });
  // A press on another column orders the records and keeps the group key, behind it.
  expect(await press(list, "Rank")).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: "rank asc, status asc",
    headers: { Status: "ascending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending", read: ["status asc nulls last"] });
  expect(await press(list, "Status")).toEqual({ groups: DESC, first: ["Papa", "Kilo"], sorting: "status desc",
    headers: { Status: "descending", Title: "none", Rank: "none" }, read: ["status desc nulls last"] });
});

test("1. a server list whose order holds the group key descending opens with its groups descending", async () => {
  const list = await mount({ kind: "server", declared: "status desc, rank asc", groupStack: byStatus, columns: STATUS_COLUMNS });
  expect(look(list)).toEqual({ groups: DESC, first: ["Papa", "Kilo"], sorting: "status desc, rank asc",
    headers: { Status: "descending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending", read: ["status desc nulls last"] });
});

test("2. a server list whose order does not hold the group key: groups ascending, and the first press on the grouping column turns them", async () => {
  const list = await mount({ kind: "server", declared: "rank asc", groupStack: byStatus, columns: STATUS_COLUMNS });
  expect(look(list)).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: "rank asc",
    headers: { Status: "ascending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending", read: ["status asc nulls last"] });
  // It shows ascending already, so the first press writes the key descending: a press that changed nothing would be a dead control.
  expect(await press(list, "Status")).toEqual({ groups: DESC, first: ["Papa", "Kilo"], sorting: "status desc",
    headers: { Status: "descending", Title: "none", Rank: "none" }, read: ["status desc nulls last"] });
  expect(await press(list, "Status")).toEqual({ groups: ASC, first: ["Bravo", "Lima"], sorting: "status asc",
    headers: { Status: "ascending", Title: "none", Rank: "none" }, read: ["status asc nulls last"] });
  // Held from then on, never removed.
  expect((await press(list, "Status")).sorting).toBe("status desc");
});

test.each(["client", "rows"] as const)("3. a %s list grouped in the browser, its order leading with the group key: the groups as before, the grouping button shows their direction", async (kind) => {
  const list = await mount({ kind, declared: "status asc, rank asc", groupStack: byStatus, columns: STATUS_COLUMNS });
  expect(look(list)).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: kind === "rows" ? "(inherited)" : "status asc, rank asc",
    headers: { Status: "ascending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending" });
  expect(await press(list, "Status")).toEqual({ groups: DESC, first: ["Papa", "Kilo"], sorting: "status desc",
    headers: { Status: "descending", Title: "none", Rank: "none" } });
  expect(await press(list, "Title")).toEqual({ groups: DESC, first: ["Kilo", "Papa"], sorting: "title asc, status desc",
    headers: { Status: "descending", Title: "ascending", Rank: "none" }, ariaSort: "Title ascending" });
  expect(await press(list, "Status")).toEqual({ groups: ASC, first: ["Bravo", "Lima"], sorting: "status asc",
    headers: { Status: "ascending", Title: "none", Rank: "none" } });
});

test("4. a browser list whose order holds the group key behind another: its groups follow their axis as it opens, its order unwritten", async () => {
  const list = await mount({ kind: "client", declared: "rank asc, status asc", groupStack: byStatus, columns: STATUS_COLUMNS });
  // Before, the groups followed the sum of their ranks (closed, open, pending, blocked).
  expect(look(list)).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: "rank asc, status asc",
    headers: { Status: "ascending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending" });
  expect(await press(list, "Title")).toEqual({ groups: ASC, first: ["Bravo", "Lima"], sorting: "title asc, status asc",
    headers: { Status: "ascending", Title: "ascending", Rank: "none" }, ariaSort: "Title ascending" });
  expect(await press(list, "Status")).toEqual({ groups: DESC, first: ["Papa", "Kilo"], sorting: "status desc",
    headers: { Status: "descending", Title: "none", Rank: "none" } });
});

test("4. a browser list whose order holds the group key descending behind another opens with its groups descending", async () => {
  const list = await mount({ kind: "client", declared: "title asc, status desc", groupStack: byStatus, columns: STATUS_COLUMNS });
  // Before, the groups followed their first record's title (open, blocked, closed, pending).
  expect(look(list)).toEqual({ groups: DESC, first: ["Kilo", "Papa"], sorting: "title asc, status desc",
    headers: { Status: "descending", Title: "ascending", Rank: "none" }, ariaSort: "Title ascending" });
  expect(await press(list, "Rank")).toEqual({ groups: DESC, first: ["Papa", "Kilo"], sorting: "rank asc, status desc",
    headers: { Status: "descending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending" });
});

test.each(["client", "rows"] as const)("5. a %s list grouped in the browser by a key its order does not hold: groups ascending, and a press on another column orders the records only", async (kind) => {
  const list = await mount({ kind, declared: "rank asc", groupStack: byStatus, columns: STATUS_COLUMNS });
  expect(look(list)).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: kind === "rows" ? "(inherited)" : "rank asc",
    headers: { Status: "ascending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending" });
  for (const [name, first, sorting, headers, ariaSort] of [
    ["Title", ["Bravo", "Lima"], "title asc", { Title: "ascending", Rank: "none" }, "Title ascending"],
    ["Title", ["Lima", "Bravo"], "title desc", { Title: "descending", Rank: "none" }, "Title descending"],
    ["Rank", ["Lima", "Bravo"], "rank asc", { Title: "none", Rank: "ascending" }, "Rank ascending"],
    ["Rank", ["Bravo", "Lima"], "rank desc", { Title: "none", Rank: "descending" }, "Rank descending"],
    // The third press removes the key: the records keep the provider's order, the groups their axis's.
    ["Rank", ["Bravo", "Lima"], "", { Title: "none", Rank: "none" }, undefined],
  ] as const) {
    expect(await press(list, name)).toEqual({ groups: ASC, first, sorting, headers: { Status: "ascending", ...headers }, ariaSort });
  }
});

test("5. a browser list with no order at all lists its groups ascending", async () => {
  const list = await mount({ kind: "client", declared: "", groupStack: byStatus, columns: STATUS_COLUMNS });
  expect(look(list)).toEqual({ groups: ASC, first: ["Bravo", "Lima"], sorting: "", headers: { Status: "ascending", Title: "none", Rank: "none" } });
});

test.each(["client", "server"] as const)("6. a %s list a person groups through \"Group by\" lists the groups by their axis", async (kind) => {
  const list = await mount({ kind, declared: "rank asc, title asc", groupStack: byStatus, columns: STATUS_COLUMNS, regroup: true });
  const read = kind === "server" ? { read: ["status asc nulls last"] } : {};
  expect(look(list)).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: "rank asc, title asc",
    headers: { Status: "ascending", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending", ...read });
  // Title only breaks the rank ties: its first press sorts it ascending, alone with no group key to keep.
  expect(await press(list, "Title")).toEqual({ groups: ASC, first: ["Bravo", "Lima"], sorting: "title asc",
    headers: { Status: "ascending", Title: "ascending", Rank: "none" }, ariaSort: "Title ascending", ...read });
  expect(await press(list, "Status")).toEqual({ groups: DESC, first: ["Papa", "Kilo"], sorting: "status desc",
    headers: { Status: "descending", Title: "none", Rank: "none" }, ...(kind === "server" ? { read: ["status desc nulls last"] } : {}) });
});

const MONTHS = ["January 2026", "February 2026", "March 2026", "April 2026"];
const DATE_COLUMNS = ["due", "title", "rank"];
test.each([
  // [the bucket, how many buckets, the first bucket's records ascending, descending, and with the date's key cleared]
  ["month", 4, ["Papa", "Delta"], ["Kilo", "Charlie"], ["Papa", "Delta"]],
  ["quarter", 2, ["Papa", "Delta", "Bravo", "Echo", "Lima", "Alpha"], ["Kilo", "Charlie"], ["Alpha", "Papa", "Bravo", "Delta", "Echo", "Lima"]],
  ["week", 8, ["Papa"], ["Kilo"], ["Papa"]],
] as const)("7. a browser list grouped by %s lists its buckets in date order, and a press on the date turns them while the date's header keeps its own sort", async (granularity, count, ascending, descending, cleared) => {
  const list = await mount({ kind: "client", declared: "due asc, title asc", groupStack: [{ field: "due", granularity }], columns: DATE_COLUMNS });
  // A bucket holds many dates, so its records share no key of the order: the date column shows its own sort and
  // keeps its three-state press, and the buckets follow the direction of the date they bucket.
  const opened = look(list);
  // The date's key orders the records too, so its header's `aria-sort` says so: a bucket's records share no date.
  expect(opened).toMatchObject({ first: ascending, sorting: "due asc, title asc", headers: { Due: "ascending", Title: "none", Rank: "none" },
    ariaSort: "Due ascending" });
  expect(opened.groups).toHaveLength(count);
  if (granularity === "month") expect(opened.groups).toEqual(MONTHS);
  // Descending: the newest bucket first, its records newest first.
  expect(await press(list, "Due")).toEqual({ groups: [...opened.groups].reverse(), first: descending, sorting: "due desc",
    headers: { Due: "descending", Title: "none", Rank: "none" }, ariaSort: "Due descending" });
  // The third press clears the key: the buckets go ascending, their records in the provider's order.
  expect(await press(list, "Due")).toEqual({ groups: opened.groups, first: cleared, sorting: "",
    headers: { Due: "none", Title: "none", Rank: "none" } });
});

test("7. a server list grouped by month asks for its months in the direction its date holds, and a press on the date turns them", async () => {
  const list = await mount({ kind: "server", declared: "due desc, title asc", groupStack: [{ field: "due", granularity: "month" }], columns: DATE_COLUMNS });
  // The month buckets carry no drill here, so they list no records.
  expect(look(list)).toEqual({ groups: [...MONTHS].reverse(), first: [], sorting: "due desc, title asc",
    headers: { Due: "descending", Title: "none", Rank: "none" }, ariaSort: "Due descending", read: ["month desc nulls last"] });
  // Due leads the order but is not its last key: the table's toggle sets it alone, ascending.
  expect(await press(list, "Due")).toEqual({ groups: MONTHS, first: [], sorting: "due asc",
    headers: { Due: "ascending", Title: "none", Rank: "none" }, ariaSort: "Due ascending", read: ["month asc nulls last"] });
  expect(await press(list, "Due")).toEqual({ groups: [...MONTHS].reverse(), first: [], sorting: "due desc",
    headers: { Due: "descending", Title: "none", Rank: "none" }, ariaSort: "Due descending", read: ["month desc nulls last"] });
});

test("7. a browser list grouped by month and ordered by another column still lists the months in date order", async () => {
  const list = await mount({ kind: "client", declared: "rank asc", groupStack: [{ field: "due", granularity: "month" }], columns: DATE_COLUMNS });
  // Before, the months followed the sum of their ranks.
  expect(look(list)).toEqual({ groups: MONTHS, first: ["Delta", "Papa"], sorting: "rank asc",
    headers: { Due: "none", Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending" });
});

const DAYS = ["2026-01-15", "2026-01-28", "2026-02-02", "2026-02-11", "2026-03-05", "2026-03-20", "2026-04-10", "2026-04-25"];
test("7. a browser list grouped by day lists the days in date order, and a press on the date turns them", async () => {
  const list = await mount({ kind: "client", declared: "rank asc", groupStack: [{ field: "due", granularity: "day" }], columns: DATE_COLUMNS });
  // A day is one date, so the date column is the grouping column: its button shows the days' direction.
  expect(look(list)).toEqual({ groups: DAYS, first: ["Papa"], sorting: "rank asc", headers: { Due: "ascending", Title: "none", Rank: "ascending" },
    ariaSort: "Rank ascending" });
  expect(await press(list, "Due")).toEqual({ groups: [...DAYS].reverse(), first: ["Kilo"], sorting: "due desc",
    headers: { Due: "descending", Title: "none", Rank: "none" } });
  expect(await press(list, "Due")).toEqual({ groups: DAYS, first: ["Papa"], sorting: "due asc", headers: { Due: "ascending", Title: "none", Rank: "none" } });
  expect(await press(list, "Title")).toEqual({ groups: DAYS, first: ["Papa"], sorting: "title asc, due asc",
    headers: { Due: "ascending", Title: "ascending", Rank: "none" }, ariaSort: "Title ascending" });
});

const TWO_LEVELS = [{ field: "status" }, { field: "kind" }];
const KIND_COLUMNS = ["status", "kind", "title", "rank"];
test.each([
  ["both levels' keys", "status asc, kind asc, title asc", ASC, ["bug", "task"], ["Bravo", "Lima"], { Status: "ascending", Kind: "ascending", Title: "ascending", Rank: "none" }, "Title ascending"],
  ["the outer level's key only", "status asc, title asc", ASC, ["bug", "task"], ["Bravo", "Lima"], { Status: "ascending", Kind: "ascending", Title: "ascending", Rank: "none" }, "Title ascending"],
  // Before, the outer groups followed their first record's kind (closed, pending, open, blocked).
  ["the inner level's key only, descending", "kind desc, title asc", ASC, ["task", "bug"], ["Lima", "Bravo"], { Status: "ascending", Kind: "descending", Title: "ascending", Rank: "none" }, "Title ascending"],
  ["neither level's key", "title asc", ASC, ["bug", "task"], ["Bravo", "Lima"], { Status: "ascending", Kind: "ascending", Title: "ascending", Rank: "none" }, "Title ascending"],
  ["both, the inner one first and the outer one descending", "kind asc, status desc", DESC, ["bug", "task"], ["Kilo", "Papa"], { Status: "descending", Kind: "ascending", Title: "none", Rank: "none" }, undefined],
] as const)("8. two levels grouped in the browser, the order holding %s: each level by its own key and direction", async (_name, declared, outer, inner, first, headers, ariaSort) => {
  const list = await mount({ kind: "client", declared, groupStack: TWO_LEVELS, columns: KIND_COLUMNS });
  expect(look(list)).toEqual({ groups: levels(outer, inner), first, sorting: declared, headers, ariaSort });
});

test("8. on two levels grouped in the browser, a press on either grouping column turns its own level and keeps the other's key", async () => {
  const list = await mount({ kind: "client", declared: "status asc, title asc", groupStack: TWO_LEVELS, columns: KIND_COLUMNS });
  // Kind's key is not held, so it shows ascending and its first press writes it descending.
  expect(await press(list, "Kind")).toEqual({ groups: levels(ASC, ["task", "bug"]), first: ["Lima", "Bravo"], sorting: "kind desc, status asc",
    headers: { Status: "ascending", Kind: "descending", Title: "none", Rank: "none" } });
  expect(await press(list, "Status")).toEqual({ groups: levels(DESC, ["task", "bug"]), first: ["Papa", "Kilo"], sorting: "status desc, kind desc",
    headers: { Status: "descending", Kind: "descending", Title: "none", Rank: "none" } });
  expect(await press(list, "Title")).toEqual({ groups: levels(DESC, ["task", "bug"]), first: ["Papa", "Kilo"], sorting: "title asc, status desc, kind desc",
    headers: { Status: "descending", Kind: "descending", Title: "ascending", Rank: "none" }, ariaSort: "Title ascending" });
});

/** Three tasks with no status, of both kinds; one is second, so first appearance never puts them last. */
const OUTER_EMPTY: readonly Row[] = [
  TASKS[0]!, { id: "n", title: "Nova", status: null, rank: 3, kind: "task", due: "2026-02-20", owner: null, priority: null },
  ...TASKS.slice(1), { id: "o", title: "Oscar", status: null, rank: 8, kind: "bug", due: "2026-03-28", owner: null, priority: null },
  { id: "q", title: "Quebec", status: null, rank: 4, kind: "task", due: "2026-03-29", owner: null, priority: null },
];
test("8. on two levels grouped in the browser, the inner groups under the outer group with no value follow their axis too, each level turned on its own", async () => {
  // The table's groups with no value read as undefined, and its `sortUndefined: "last"` never tied two of them, so
  // before, the inner groups under "No value" kept their first appearance (task, bug) both ways.
  const list = await mount({ kind: "client", declared: "title asc", groupStack: TWO_LEVELS, columns: KIND_COLUMNS, rows: OUTER_EMPTY });
  expect(look(list).groups).toEqual([...levels(ASC, ["bug", "task"]), EMPTY, "· bug", "· task"]);
  expect((await press(list, "Kind")).groups).toEqual([...levels(ASC, ["task", "bug"]), EMPTY, "· task", "· bug"]);
  expect((await press(list, "Status")).groups).toEqual([...levels(DESC, ["task", "bug"]), EMPTY, "· task", "· bug"]);
  expect((await press(list, "Kind")).groups).toEqual([...levels(DESC, ["bug", "task"]), EMPTY, "· bug", "· task"]);
});

/** Delta (open) and Lima (blocked) have no kind, and come first in their groups. */
const INNER_EMPTY: readonly Row[] = [{ ...TASKS[4]!, kind: null }, { ...TASKS[7]!, kind: null }, ...TASKS.filter((row) => row.id !== "d" && row.id !== "l")];
test("8. on two levels grouped in the browser, an inner group with no value is last under its outer group, ascending and descending", async () => {
  const list = await mount({ kind: "client", declared: "kind asc", groupStack: TWO_LEVELS, columns: KIND_COLUMNS, rows: INNER_EMPTY });
  expect(look(list).groups).toEqual(["blocked", "· bug", `· ${EMPTY}`, "closed", "· bug", "· task", "open", "· bug", `· ${EMPTY}`, "pending", "· bug", "· task"]);
  expect((await press(list, "Kind")).groups)
    .toEqual(["blocked", "· bug", `· ${EMPTY}`, "closed", "· task", "· bug", "open", "· bug", `· ${EMPTY}`, "pending", "· task", "· bug"]);
});

test.each([
  ["rank asc", ASC, ["Lima", "Bravo"]],
  ["status desc, rank asc", DESC, ["Papa", "Kilo"]],
] as const)("9. a browser list grouped by a key it shows no column for, ordered by %s: no press reorders the groups", async (declared, groups, first) => {
  const list = await mount({ kind: "client", declared, groupStack: byStatus, columns: ["title", "rank"] });
  expect(look(list)).toEqual({ groups, first, sorting: declared, headers: { Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending" });
  for (const name of ["Title", "Title", "Rank", "Rank", "Rank"]) expect((await press(list, name)).groups).toEqual(groups);
  // The group key the order holds stays through every press.
  expect(list.view().state.sorting?.some(({ id }) => id === "status")).toBe(declared.startsWith("status"));
});

test("9. a server list grouped by a key it shows no column for asks for its groups ascending under every press", async () => {
  const list = await mount({ kind: "server", declared: "rank asc", groupStack: byStatus, columns: ["title", "rank"] });
  expect(look(list).read).toEqual(["status asc nulls last"]);
  for (const name of ["Title", "Rank", "Rank"]) expect(await press(list, name)).toMatchObject({ groups: ASC, read: ["status asc nulls last"] });
});

test.each(["client", "rows"] as const)("9. a %s list grouped by a field no read can sort by still lists its groups by their axis, and the field's header offers no press", async (kind) => {
  const list = await mount({ kind, declared: "rank asc", groupStack: byStatus, columns: STATUS_COLUMNS, unsortable: ["status"] });
  // The axis's key sorts in the table whatever the header offers: its header has no button, so shows no direction,
  // and its `aria-sort` says none, as a grouping column's does.
  expect(look(list)).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: kind === "rows" ? "(inherited)" : "rank asc",
    headers: { Title: "none", Rank: "ascending" }, ariaSort: "Rank ascending" });
  expect(screen.queryByRole("button", { name: /^Sort Status / })).toBeNull();
  expect(screen.getByRole("columnheader", { name: "Status" }).getAttribute("aria-sort")).toBe("none");
  for (const name of ["Title", "Rank", "Rank", "Rank"]) expect((await press(list, name)).groups).toEqual(ASC);
});

const OWNERS = ["Ann", "Bob", "Mia", "Zed"];
const OWNER_COLUMNS = ["owner.name", "title", "rank"];
test.each([
  ["its name", "title asc"],
  // Before, the groups followed the owners' identities (Zed, Ann, Mia, Bob), and a press on Owner never turned them.
  ["its identity", "owner asc, title asc"],
] as const)("10. a browser list grouped by a relation, ordered by %s, lists the groups by name, then identity", async (_name, declared) => {
  const list = await mount({ kind: "client", declared, groupStack: [{ field: "owner" }], columns: OWNER_COLUMNS });
  expect(look(list)).toEqual({ groups: OWNERS, first: ["Bravo", "Echo"], sorting: declared, headers: { Owner: "ascending", Title: "ascending", Rank: "none" },
    ariaSort: "Title ascending" });
  // The owner's name is a member of the grouped relation: its press turns the groups, and the identity key goes with it.
  expect(await press(list, "Owner")).toEqual({ groups: [...OWNERS].reverse(), first: ["Papa", "Delta"], sorting: "owner.name desc",
    headers: { Owner: "descending", Title: "none", Rank: "none" } });
  expect((await press(list, "Owner")).groups).toEqual(OWNERS);
});

test("10. a server list grouped by a relation asks for its groups by name, then identity, in the direction its order holds", async () => {
  const list = await mount({ kind: "server", declared: "owner.name desc, title asc", groupStack: [{ field: "owner" }], columns: OWNER_COLUMNS });
  expect(look(list)).toEqual({ groups: [...OWNERS].reverse(), first: ["Delta", "Papa"], sorting: "owner.name desc, title asc",
    headers: { Owner: "descending", Title: "ascending", Rank: "none" }, ariaSort: "Title ascending", read: ["owner__name desc nulls last", "owner_id desc nulls last"] });
  expect(await press(list, "Owner")).toEqual({ groups: OWNERS, first: ["Bravo", "Echo"], sorting: "owner.name asc",
    headers: { Owner: "ascending", Title: "none", Rank: "none" }, read: ["owner__name asc nulls last", "owner_id asc nulls last"] });
});

const PRIORITY_LABELS = ["Urgent", "Low", "Medium", "High"];
test.each(["client", "rows", "server"] as const)("10. a %s list grouped by a choice lists the groups by its stored key", async (kind) => {
  const list = await mount({ kind, declared: "title asc", groupStack: [{ field: "priority" }], columns: ["priority", "title"] });
  const read = (direction: string) => kind === "server" ? { read: [`priority ${direction} nulls last`] } : {};
  // Rows held in the browser have no metadata naming the choices: their groups show the keys.
  const labels = kind === "rows" ? ["p1", "p2", "p3", "p4"] : PRIORITY_LABELS;
  expect(look(list)).toEqual({ groups: labels, first: ["Delta", "Papa"], sorting: kind === "rows" ? "(inherited)" : "title asc",
    headers: { Priority: "ascending", Title: "ascending" }, ariaSort: "Title ascending", ...read("asc") });
  expect(await press(list, "Priority")).toEqual({ groups: [...labels].reverse(), first: ["Bravo", "Lima"], sorting: "priority desc",
    headers: { Priority: "descending", Title: "none" }, ...read("desc") });
});

const PANE_COLUMNS = ["status", "rank", "title"];
test("11. a pane list's Order select orders the records only, and choosing the checked choice changes nothing", async () => {
  const list = await mount({ kind: "client", declared: "status asc, rank asc, title asc", groupStack: byStatus, columns: PANE_COLUMNS, pane: true });
  expect(await orderItems()).toEqual([["Rank, ascending", "true"], ["Rank, descending", "false"], ["Title, ascending", "false"], ["Title, descending", "false"]]);
  const opened = look(list);
  expect(opened).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: "status asc, rank asc, title asc",
    headers: { Status: "ascending", Rank: "ascending", Title: "none" }, ariaSort: "Rank ascending" });
  expect(await choose(list, "Rank, ascending")).toEqual(opened);
  expect(await choose(list, "Title, descending")).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: "title desc, status asc",
    headers: { Status: "ascending", Rank: "none", Title: "descending" }, ariaSort: "Title descending" });
  expect((await orderItems()).find(([, checked]) => checked === "true")?.[0]).toBe("Title, descending");
  expect(await choose(list, "Rank, descending")).toEqual({ groups: ASC, first: ["Bravo", "Lima"], sorting: "rank desc, status asc",
    headers: { Status: "ascending", Rank: "descending", Title: "none" }, ariaSort: "Rank descending" });
  expect((await choose(list, "Rank, descending")).sorting).toBe("rank desc, status asc");
});

test.each([
  ["rank asc", ASC, "Title, descending", ["Lima", "Bravo"], "title desc"],
  ["rank asc, status desc", DESC, "Rank, descending", ["Kilo", "Papa"], "rank desc, status desc"],
] as const)("11. a pane list ordered by %s: its Order select's choices keep the groups where they are", async (declared, groups, choice, first, sorting) => {
  const list = await mount({ kind: "client", declared, groupStack: byStatus, columns: PANE_COLUMNS, pane: true });
  expect(look(list).groups).toEqual(groups);
  expect(await choose(list, choice)).toMatchObject({ groups, first, sorting });
});

test("11. a server pane list's Order select asks for the records in the chosen order and for the groups as before", async () => {
  const list = await mount({ kind: "server", declared: "status asc, rank asc, title asc", groupStack: byStatus, columns: PANE_COLUMNS, pane: true });
  expect(await choose(list, "Title, descending")).toEqual({ groups: ASC, first: ["Lima", "Bravo"], sorting: "title desc, status asc",
    headers: { Status: "ascending", Rank: "none", Title: "descending" }, ariaSort: "Title descending", read: ["status asc nulls last"] });
});

const EMPTY = "No value";
test.each([
  ["client", "status asc, title asc", [...ASC, EMPTY], [...DESC, EMPTY]],
  ["client", "rank asc", [...ASC, EMPTY], [...DESC, EMPTY]],
  ["rows", "status desc", [...DESC, EMPTY], [...ASC, EMPTY]],
  ["server", "status asc, title asc", [...ASC, EMPTY], [...DESC, EMPTY]],
  ["server", "status desc, title asc", [...DESC, EMPTY], [...ASC, EMPTY]],
] as const)("12. a %s list ordered by %s lists the group with no status last, ascending and descending", async (kind, declared, opened, pressed) => {
  const list = await mount({ kind, declared, groupStack: byStatus, columns: STATUS_COLUMNS, rows: WITH_EMPTY });
  const seen = look(list);
  expect(seen.groups).toEqual(opened);
  if (kind === "server") expect(seen.read).toEqual([`status ${opened[0] === "blocked" ? "asc" : "desc"} nulls last`]);
  expect((await press(list, "Status")).groups).toEqual(pressed);
});

test.each([
  ["client", "owner.name asc", [...OWNERS, EMPTY], [...OWNERS].reverse().concat(EMPTY)],
  ["server", "owner.name desc", [...OWNERS].reverse().concat("No owner"), [...OWNERS, "No owner"]],
] as const)("12. a %s list grouped by a relation lists the group with no owner last, ascending and descending", async (kind, declared, opened, pressed) => {
  const list = await mount({ kind, declared, groupStack: [{ field: "owner" }], columns: ["owner.name", "title"], rows: WITH_EMPTY });
  expect(look(list).groups).toEqual(opened);
  expect((await press(list, "Owner")).groups).toEqual(pressed);
});

test("12. a browser list grouped by a relation lists a related record with no name after every name, descending as ascending, as the server's groups read does", async () => {
  // The comparator carries the direction, so a missing label stays last; before, the table turned it first.
  const unnamed = { id: "u", title: "Uma", status: "open", rank: 4, kind: "bug", due: "2026-02-14", owner: { id: "o5", name: null }, priority: "p1" };
  const list = await mount({ kind: "client", declared: "owner.name desc", groupStack: [{ field: "owner" }], columns: ["owner.name", "title"],
    rows: [...TASKS, unnamed] });
  expect(look(list).groups).toEqual([...OWNERS].reverse().concat(EMPTY));
  expect((await press(list, "Owner")).groups).toEqual([...OWNERS, EMPTY]);
});

test.each([
  ["title desc, status asc", [...ASC, EMPTY]],
  ["title desc, status desc", [...DESC, EMPTY]],
] as const)("12. the records of a browser list's group with no value follow the order's next key, its groups ordered by %s", async (declared, groups) => {
  // Two records with no status tie on the group's key, so the title orders them.
  const list = await mount({ kind: "client", declared, groupStack: byStatus, columns: STATUS_COLUMNS, rows: WITH_EMPTY });
  expect(look(list).groups).toEqual(groups);
  expect(recordsByGroup(list)[EMPTY]).toEqual(["Oscar", "Nova"]);
});

test.each([
  ["8 tasks", TASKS],
  ["44 tasks", MANY],
] as const)("13. with %s, more or fewer than the table library samples, a browser list orders its groups the same", async (_name, rows) => {
  viewport = 10_000;
  const days = [...new Set(rows.map((row) => String(row.due)))].sort();
  for (const [declared, groupStack, columns, groups, first] of [
    ["rank asc, status asc", byStatus, STATUS_COLUMNS, ASC, ["Lima", "Bravo"]],
    ["title asc", [{ field: "due", granularity: "month" }], DATE_COLUMNS, MONTHS, ["Delta", "Papa"]],
    ["rank asc", [{ field: "due", granularity: "day" }], DATE_COLUMNS, days, ["Papa"]],
    ["title asc", [{ field: "owner" }], OWNER_COLUMNS, OWNERS, ["Bravo", "Echo"]],
    ["title asc", [{ field: "priority" }], ["priority", "title"], PRIORITY_LABELS, ["Delta", "Papa"]],
  ] as const) {
    const list = await mount({ kind: "client", declared, groupStack, columns, rows });
    expect(look(list)).toMatchObject({ groups, first, sorting: declared });
    cleanup(); clearClients();
  }
  const withEmpty = await mount({ kind: "client", declared: "status desc", groupStack: byStatus, columns: STATUS_COLUMNS,
    rows: [...WITH_EMPTY, ...rows.slice(TASKS.length)] });
  expect(look(withEmpty)).toMatchObject({ groups: [...DESC, EMPTY], first: ["Papa", "Kilo"] });
  expect(await press(withEmpty, "Status")).toMatchObject({ groups: [...ASC, EMPTY], first: ["Bravo", "Lima"], sorting: "status asc" });
});

/** The header cell holding the sort button named `name`. */
const headerOf = (name: string) => screen.getByRole("button", { name }).closest("th")!;

test.each(["client", "server"] as const)("14. a grouped %s list puts aria-sort on the one header of the key its records follow; the grouping column's sort button names its groups' direction", async (kind) => {
  // WAI-ARIA asks for aria-sort on one header at a time, and the records follow one key: the grouping column's header
  // says "none", as an unsorted header does, and states its groups' direction in its button's name and arrow instead.
  // (`look` checks on every look that at most one header says a direction.)
  const list = await mount({ kind, declared: "rank asc", groupStack: byStatus, columns: STATUS_COLUMNS });
  expect(look(list)).toMatchObject({ headers: { Status: "ascending", Rank: "ascending" }, ariaSort: "Rank ascending" });
  expect(headerOf("Sort Status (currently ascending)").getAttribute("aria-sort")).toBe("none");
  // Its press turns its button's name while its aria-sort stays "none"; the records then follow no key, so no header says one.
  const pressed = await press(list, "Status");
  expect(pressed).toMatchObject({ groups: DESC, headers: { Status: "descending", Rank: "none" } });
  expect(pressed.ariaSort).toBeUndefined();
  expect(headerOf("Sort Status (currently descending)").getAttribute("aria-sort")).toBe("none");
  expect(await press(list, "Title")).toMatchObject({ sorting: "title asc, status desc", headers: { Status: "descending", Title: "ascending" }, ariaSort: "Title ascending" });
  expect(headerOf("Sort Status (currently descending)").getAttribute("aria-sort")).toBe("none");
});

test.each(["client", "server"] as const)("14. a %s list with no groups is unchanged: the header of the key its records follow says its direction, in its aria-sort and its button", async (kind) => {
  const list = await mount({ kind, declared: "status asc, rank asc", groupStack: [], columns: STATUS_COLUMNS });
  expect(look(list)).toEqual({ groups: [], first: [], sorting: "status asc, rank asc",
    headers: { Status: "ascending", Title: "none", Rank: "none" }, ariaSort: "Status ascending" });
  expect(await press(list, "Status")).toEqual({ groups: [], first: [], sorting: "status desc",
    headers: { Status: "descending", Title: "none", Rank: "none" }, ariaSort: "Status descending" });
});

test("14. a press on a grouping column keeps its sort button, and the focus on it, while the columns are built again for the turned groups", async () => {
  const list = await mount({ kind: "client", declared: "rank asc", groupStack: byStatus, columns: STATUS_COLUMNS });
  const button = screen.getByRole("button", { name: "Sort Status (currently ascending)" });
  act(() => button.focus());
  expect((await press(list, "Status")).groups).toEqual(DESC);
  expect(button.isConnected).toBe(true);
  expect(button.getAttribute("aria-label")).toBe("Sort Status (currently descending)");
  expect(document.activeElement).toBe(button);
});

test("15. a sorting change applies to the list's order, never to the keys the table is handed for its groups", () => {
  const seen: { view?: ResourceViewContextValue; change?: OnChangeFn<SortingState>; table?: SortingState; meta?: SortingState } = {};
  const query = ResourceQuery.forRows({ fields: FIELDS });
  const columns = STATUS_COLUMNS.map((field) => ({ field, header: HEADERS[field]! }));
  function TableState() {
    const view = useResourceView();
    const state = useResourceViewTableState({ columns, resourceView: view, modelMetadata: null, groupStack: view.state.groupStack, query });
    Object.assign(seen, { view, change: state.handleSortingChange, table: state.sorting, meta: state.meta.sorting });
    return null;
  }
  render(<Provider><ResourceViewProvider scope="local" initialState={{ groupStack: byStatus, sorting: [{ id: "status", desc: true }, { id: "rank", desc: false }] }}>
    <TableState />
  </ResourceViewProvider></Provider>);
  // The table is handed the axis's key ascending (its column carries the direction); the headers read the order.
  expect(seen.table).toEqual([{ id: "status", desc: false }, { id: "rank", desc: false }]);
  expect(seen.meta).toEqual([{ id: "status", desc: true }, { id: "rank", desc: false }]);
  act(() => seen.change!((old) => old));
  expect(seen.view!.state.sorting).toEqual([{ id: "status", desc: true }, { id: "rank", desc: false }]);
  act(() => seen.change!((old) => [...old, { id: "title", desc: true }]));
  expect(seen.view!.state.sorting).toEqual([{ id: "status", desc: true }, { id: "rank", desc: false }, { id: "title", desc: true }]);
});

const TreePage: TypedDocumentNode<{ page: CollectionPage<Row> }, { request: CollectionPageRequest }> = gql`
  query Page($request: JSON!) { page(request: $request) { rows { id title status rank } total } }
`;
test("16. a tree whose view holds a group stack: its Order select reads the list's order, and a choice keeps the group key held descending", async () => {
  // A tree lists no groups, but its view can hold a stack (a link's), which groups its table: the select reads the
  // order (`TableMeta.sorting` on the resource surface), not the table's sorting, which leads with the key ascending.
  const source: CollectionSource<Row> = {
    query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" }, rank: { scalar: "Int" } } }),
    rows: collectionQuery({ document: TreePage, variables: (request: CollectionPageRequest) => ({ request }), select: (data) => data.page }),
  };
  const custom = vi.fn(async () => ({ data: { page: { rows: [{ id: "root", title: "Root", status: "open", rank: 1 }], total: 1 } } }));
  const view: { current?: ResourceViewContextValue } = {};
  function ViewState() { view.current = useResourceView(); return null; }
  render(<Provider resources={[testDataResource("catalog.Reference")]} dataProvider={{ custom, getList: vi.fn() }}>
    <OperationDocumentsProvider documents={{}}><ToastProvider><PaneToolbarHost>
      <ResourceViewProvider scope="local" initialState={{ pageSize: 25, groupStack: byStatus, sorting: [{ id: "status", desc: true }, { id: "title", desc: false }] }}>
        <ViewState />
        <CollectionTreeView resource="authored.Task" source={source} rowKey="id" parent="parent" label="title" hasChildren="expandable"
          columns={[{ field: "title", header: "Title" }, { field: "rank", header: "Rank" }]} textFilterField="title" customFilterFields={[]}
          filterOptions={[]} pane={{ selector: "Tasks" }} />
      </ResourceViewProvider>
    </PaneToolbarHost></ToastProvider></OperationDocumentsProvider>
  </Provider>);
  await screen.findByText("Root");
  expect((await orderItems()).find(([, checked]) => checked === "true")?.[0]).toBe("Title, ascending");
  fireEvent.click(within(await screen.findByRole("group", { name: "Order" })).getByRole("menuitemradio", { name: "Rank, ascending" }));
  await settled();
  expect(view.current!.state.sorting).toEqual([{ id: "rank", desc: false }, { id: "status", desc: true }]);
});

test.each(["ASC", "DESC"] as const)("17. a JSON axis whose values are objects orders its groups %s by their identity, the table's grouping value, not the column's objects", (direction) => {
  // A JSON group's identity is its value's JSON text, where its column reads the object itself.
  const query = ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, title: { scalar: "String" }, scope: { kind: "json" } } });
  const axis = query.axis("scope");
  const rows: Row[] = [{ team: "c" }, { team: "a" }, null, { team: "b" }, { team: "a" }].map((scope, index) => ({ id: String(index), title: `T${index}`, scope }));
  const table = createTable<Row>({
    data: rows, columns: buildColumns<Row>([{ field: "scope", header: "Scope" }, { field: "title", header: "Title" }], { axes: [axis], directions: [direction], clientOperations: true, query }),
    state: { sorting: [{ id: axis.id, desc: false }], grouping: [axis.id], columnPinning: {}, columnVisibility: {}, expanded: {} },
    onStateChange: () => {}, renderFallbackValue: null,
    getCoreRowModel: getCoreRowModel(), getGroupedRowModel: getGroupedRowModel(), getSortedRowModel: getSortedRowModel(),
  });
  // The table names each group by its grouping value's text, the group with no value "null" (last both ways).
  const teams = ['{"team":"a"}', '{"team":"b"}', '{"team":"c"}'];
  expect(table.getSortedRowModel().rows.map((row) => row.groupingValue)).toEqual([...(direction === "ASC" ? teams : [...teams].reverse()), "null"]);
});

interface Held extends Mounted {
  /** Hand the list these rows, as a page does with a read's answer. */
  setRows: (rows: readonly Row[]) => void;
  /** Rename the Title column's header. */
  setTitle: (title: string) => void;
}
/** A RowsListView over the tasks' query that stays mounted while the test hands it other rows or renames a header. */
function mountHeld({ declared, groupStack, columns, rows }: {
  declared: string; groupStack: readonly ResourceViewGroup[]; columns: readonly string[]; rows: readonly Row[];
}): Held {
  const query = ResourceQuery.fromContract(taskContract(order(declared)));
  const held: { view?: ResourceViewContextValue; setRows?: (rows: readonly Row[]) => void; setTitle?: (title: string) => void } = {};
  function Host() {
    const [current, setRows] = React.useState(rows);
    const [title, setTitle] = React.useState("Title");
    const listColumns = React.useMemo(() => columns.map((field) => ({ field, header: field === "title" ? title : HEADERS[field]! })), [title]);
    Object.assign(held, { view: useResourceView(), setRows, setTitle });
    return <RowsListView scope="inherit" rows={current as { id: string }[]} query={query} columns={listColumns} />;
  }
  const initial: ResourceViewInitialState = { groupStack: [...groupStack] };
  render(<Provider><ResourceViewProvider scope="local" initialState={initial}><Host /></ResourceViewProvider></Provider>);
  return {
    view: () => held.view!, custom: wire([]).custom, initial, titles: new Set(TASKS.map((row) => String(row.title))),
    setRows: (next) => act(() => held.setRows!(next)), setTitle: (next) => act(() => held.setTitle!(next)),
  };
}

test("18. a column's header renamed while the list stays mounted shows the new name, and its sort button's name follows", async () => {
  // The header is one stable component (`DisplayHeader`), so it must read its column's label at every render.
  const list = mountHeld({ declared: "rank asc", groupStack: byStatus, columns: STATUS_COLUMNS, rows: TASKS });
  await settled();
  expect(screen.getByRole("button", { name: "Sort Title (not sorted)" }).textContent).toBe("Title");
  list.setTitle("Name");
  await settled();
  expect([screen.queryByRole("button", { name: /^Sort Title / }), screen.getByRole("button", { name: "Sort Name (not sorted)" }).textContent])
    .toEqual([null, "Name"]);
});

type Owned = Row & { owner: { id: string; name: string } | null };
test("19. a browser list's groups follow a renamed owner, whether the list is read again or its records are changed in place and handed back", async () => {
  const fresh = (): Owned[] => TASKS.map((row) => ({ ...row, owner: row.owner ? { ...(row.owner as { id: string; name: string }) } : null }));
  const rows = fresh();
  const list = mountHeld({ declared: "title asc", groupStack: [{ field: "owner" }], columns: OWNER_COLUMNS, rows });
  await settled();
  expect(look(list).groups).toEqual(OWNERS);
  // Read again: new records, Zed now Aaron.
  list.setRows(fresh().map((row) => row.owner?.id === "o1" ? { ...row, owner: { id: "o1", name: "Aaron" } } : row));
  await settled();
  expect(look(list).groups).toEqual(["Aaron", "Ann", "Bob", "Mia"]);
  // In place: the first records again, Bob renamed Abe on them, in a new array. A label kept by the record itself
  // stayed Bob's, and Abe's group sorted where Bob's was.
  for (const row of rows) if (row.owner?.id === "o4") row.owner.name = "Abe";
  list.setRows([...rows]);
  await settled();
  expect(look(list).groups).toEqual(["Abe", "Ann", "Mia", "Zed"]);
});
