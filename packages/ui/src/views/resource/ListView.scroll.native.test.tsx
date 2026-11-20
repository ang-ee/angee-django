// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { ResourceQuery, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { OperationDocumentsProvider } from "@angee/refine";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { ToastProvider } from "../../feedback";
import { ResourceViewProvider } from "./resource-view-context";
import { ListView } from "./ListView";
import { RowsListView } from "./RowsListView";
import type { GroupLabelContext } from "./resource-view-types";
import type { ColumnDescriptor } from "../page";

// What each row's and each group header's render reaches: its title cell, through the
// column's own renderer, and its label, through the page's group label; and each time the
// grouped body hands a record its row again, through a test-only wrapper over that row,
// which the memoised row then skips or renders. The table header's cells are counted
// through a test-only wrapper over the table's header cell.
const rendered = vi.hoisted(() => ({
  rows: new Map<string, number>(), headers: new Map<string, number>(), handed: new Map<string, number>(), headCells: { count: 0 },
}));
const count = (counts: Map<string, number>, key: string) => counts.set(key, (counts.get(key) ?? 0) + 1);
vi.mock("./resource-view-list-body", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./resource-view-list-body")>();
  const React = await import("react");
  function RecordRow(props: React.ComponentProps<typeof actual.RecordRow>): React.ReactElement {
    rendered.handed.set(props.row.id, (rendered.handed.get(props.row.id) ?? 0) + 1);
    return React.createElement(actual.RecordRow, props);
  }
  return { ...actual, RecordRow };
});
vi.mock("../../ui/table", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../ui/table")>();
  const React = await import("react");
  function TableHead(props: React.ComponentProps<typeof actual.TableHead>): React.ReactElement {
    rendered.headCells.count += 1;
    return React.createElement(actual.TableHead, props);
  }
  return { ...actual, TableHead };
});
const columns = [{ field: "title", header: "Title", render: (row: Row) => {
  count(rendered.rows, String(row.id));
  return String(row.title);
} }];
const groupLabel = ({ label }: GroupLabelContext) => {
  count(rendered.headers, String(label));
  return label;
};
const href = (row: Row) => `/tasks/${String(row.id)}`;

const contract = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" },
} }).contract;
contract.axes.status!.server = { input: "status", key: "status" };
contract.axes.status!.drill = { kind: "value", field: "status", valueKey: "status", nullMode: "isNull", valueMap: [] };
const resource = testDataResource("notes.Task", {
  roots: { groups: "tasks_groups", aggregate: "tasks_aggregate" }, typeNames: { filter: "TaskBoolExp", order: "TaskOrderBy" },
  query: contract, recordRepresentation: "title",
  fields: ["id", "title", "status"].map((name) => ({ name, kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
});
// Three groups of twenty: more rows than one window holds.
const records = ["a", "b", "c"].flatMap((status) => Array.from({ length: 20 }, (_, index) => ({
  id: `${status}${index}`, title: `Task ${status}${index}`, status,
})));
const matches = (row: Row, where: Record<string, unknown> | undefined): boolean =>
  Object.entries(where ?? {}).every(([key, condition]) => key === "_and"
    ? (condition as Record<string, unknown>[]).every((branch) => matches(row, branch))
    : Object.entries(condition as Record<string, unknown>).every(([operator, operand]) => operator === "_eq" && row[key] === operand));
const custom = vi.fn(async ({ meta }: { meta?: Record<string, unknown> }) => {
  const variables = meta!.gqlVariables as { group_by?: { field: string }[]; where?: Record<string, unknown>; rows_limit?: number };
  const rows = records.filter((row) => matches(row, variables.where));
  if (!variables.group_by) return { data: { tasks_aggregate: { aggregate: { count: rows.length } } } };
  const field = variables.group_by[0]!.field;
  const buckets = [...new Set(rows.map((row) => String(row[field as keyof typeof row])))].map((key) => {
    const members = rows.filter((row) => row[field as keyof typeof row] === key);
    return { key: { [field]: key }, aggregate: { count: members.length }, rows: members.slice(0, variables.rows_limit ?? 0) };
  });
  return { data: { tasks_groups: buckets, totalCount: buckets.length } };
});
const getList = vi.fn(async ({ pagination, meta }: GetListParams) => {
  const rows = records.filter((row) => matches(row, (meta?.gqlVariables as { where?: Record<string, unknown> } | undefined)?.where));
  const size = pagination?.pageSize ?? rows.length;
  const page = pagination?.currentPage ?? 1;
  return { data: rows.slice((page - 1) * size, page * size), total: rows.length };
});
const documents = { console: {
  groups: { "notes.Task": "query Groups { tasks_groups { key } totalCount }" },
  aggregates: { "notes.Task": "query Aggregate { tasks_aggregate { aggregate { count } } }" },
} };
const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://scroll" });

beforeEach(() => {
  rendered.rows.clear();
  rendered.headers.clear();
  rendered.handed.clear();
  rendered.headCells.count = 0;
  // The document has no layout: the list's scroll element is a 600px viewport, which
  // the virtualizer reads when it observes the element.
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("resource-table-scroll") ? 600 : 0;
  });
});
afterEach(() => { cleanup(); clearClients(); vi.restoreAllMocks(); });

function renderList(initialState: Record<string, unknown>, listColumns: readonly ColumnDescriptor<Row>[] = columns) {
  render(
    <Provider resources={[resource]} dataProvider={{ custom, getList }}
      queryClientConfig={{ defaultOptions: { queries: { retry: false } } }}>
      <OperationDocumentsProvider documents={documents}>
        <ToastProvider>
          <ResourceViewProvider resource={resource.modelLabel} scope="local" initialState={initialState}>
            <ListView resource={resource.modelLabel} columns={listColumns} scope="inherit" selectable rowHref={href}
              renderGroupLabel={groupLabel} />
          </ResourceViewProvider>
        </ToastProvider>
      </OperationDocumentsProvider>
    </Provider>,
  );
}

/** The list's reads have settled (the frame's `aria-busy`). */
async function settled(): Promise<void> {
  await waitFor(() => expect(document.querySelector("[data-resource-presentation]")?.getAttribute("aria-busy")).not.toBe("true"));
}

/** Scroll the list ten record rows down, then let the virtualizer report the scroll's end. */
async function scrollTenRows(): Promise<void> {
  const scroller = document.querySelector<HTMLElement>(".resource-table-scroll")!;
  scroller.scrollTop = 400;
  fireEvent.scroll(scroller);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 200)); });
}

/** Show or hide a field through the table header's fields menu. */
async function toggleField(name: string): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Visible fields" }));
  const item = await screen.findByRole("menuitemcheckbox", { name });
  fireEvent.click(item);
  fireEvent.keyDown(item, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
}

/** After a range change, what stayed in the window rendered no more, and what entered rendered once. */
function expectOnlyEnteredRendered(before: ReadonlyMap<string, number>, after: ReadonlyMap<string, number>): void {
  const entered = [...after.keys()].filter((key) => !before.has(key));
  expect(entered.length).toBeGreaterThan(0);
  expect(Object.fromEntries(entered.map((key) => [key, after.get(key)]))).toEqual(Object.fromEntries(entered.map((key) => [key, 1])));
  expect(Object.fromEntries([...before.keys()].map((key) => [key, after.get(key)]))).toEqual(Object.fromEntries(before));
}

test("a flat list's range change renders the rows that enter its window and none that stay", async () => {
  renderList({ pageSize: 60 });
  await screen.findByText("Task a0");
  await settled();
  // The fields menu shows and there are no row actions: each row holds an empty trailing cell.
  expect(screen.getByRole("button", { name: "Visible fields" })).toBeTruthy();
  const before = new Map(rendered.rows);
  await scrollTenRows();
  expectOnlyEnteredRendered(before, rendered.rows);
  expect(screen.getByText("Task a0").closest("a")?.getAttribute("href")).toBe("/tasks/a0");
});

test("a grouped list's range change renders the headers and rows that enter its window and none that stay", async () => {
  renderList({ pageSize: 20, groupStack: [{ field: "status" }] });
  await screen.findByText("Task b0");
  await settled();
  const before = { rows: new Map(rendered.rows), headers: new Map(rendered.headers), handed: new Map(rendered.handed) };
  expect(before.headers.has("b")).toBe(true);
  await scrollTenRows();
  expectOnlyEnteredRendered(before.rows, rendered.rows);
  expect(rendered.headers).toEqual(before.headers);
  // No item that stayed in the window rendered at all: the body handed none its row again.
  expectOnlyEnteredRendered(before.handed, rendered.handed);
});

test("ticking a record renders its own group header and leaves the other groups' alone", async () => {
  renderList({ pageSize: 20, groupStack: [{ field: "status" }] });
  const row = (await screen.findByText("Task b0")).closest("tr")!;
  await settled();
  const before = new Map(rendered.headers);
  fireEvent.click(within(row).getByRole("checkbox", { name: "Select row" }));
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "Select the loaded rows in b" }).getAttribute("aria-checked")).toBe("mixed"));
  expect(rendered.headers.get("a")).toBe(before.get("a"));
  expect(rendered.headers.get("b")).toBeGreaterThan(before.get("b")!);
});

test.each([
  ["flat", { pageSize: 20 }],
  ["grouped", { pageSize: 20, groupStack: [{ field: "status" }] }],
])("a %s list's rows show the cells of the fields it shows, as a field is shown or hidden", async (_kind, initialState) => {
  renderList(initialState, [...columns, { field: "status", header: "Status", hiddenByDefault: true }]);
  const cells = async () => (await screen.findByText("Task a0")).closest("tr")!.querySelectorAll("td").length;
  await settled();
  const hidden = await cells();
  await toggleField("Status");
  await waitFor(async () => expect(await cells()).toBe(hidden + 1));
  await toggleField("Status");
  await waitFor(async () => expect(await cells()).toBe(hidden));
});

test.each([
  ["flat", { pageSize: 60 }],
  ["grouped", { pageSize: 20, groupStack: [{ field: "status" }] }],
])("a %s list's range change leaves its header row alone, which a sort, a field shown and a selection still reach", async (_kind, initialState) => {
  renderList(initialState, [...columns, { field: "status", header: "Status", hiddenByDefault: true }]);
  await screen.findByText("Task a0");
  await settled();
  const before = rendered.headCells.count;
  await scrollTenRows();
  expect(rendered.headCells.count).toBe(before);
  const header = () => within(document.querySelector("thead")!);
  fireEvent.click(header().getByRole("button", { name: "Sort Title (not sorted)" }));
  expect(await header().findByRole("button", { name: "Sort Title (currently ascending)" })).toBeTruthy();
  await settled();
  await toggleField("Status");
  expect(await header().findByText("Status")).toBeTruthy();
  fireEvent.click(within((await screen.findByText("Task a0")).closest("tr")!).getByRole("checkbox", { name: "Select row" }));
  await waitFor(() => expect(header().getByRole("checkbox", { name: /^Select all/ }).getAttribute("aria-checked")).toBe("mixed"));
});

test("a reorderable in-memory list's range change renders the rows that enter its window and none that stay", async () => {
  render(<ToastProvider>
    <RowsListView scope="local" rows={records} columns={columns} pageSize={60} rowHref={href} onReorder={vi.fn()} />
  </ToastProvider>);
  await screen.findByText("Task a0");
  expect(screen.getAllByRole("button", { name: "Reorder row" }).length).toBeGreaterThan(0);
  const before = new Map(rendered.rows);
  await scrollTenRows();
  expectOnlyEnteredRendered(before, rendered.rows);
});
