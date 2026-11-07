// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ResourceQuery } from "@angee/metadata";
import { testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { RowsListView } from "./RowsListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import type { ResourceViewFilter } from "./resource-view-model";
import { readDndPayload, writeDndPayload } from "../../lib/dnd";
import { testDndTransfer } from "../../lib/dnd-test-fixtures";

const rows = [
  { id: "1", name: "Alpha", status: "active" },
  { id: "2", name: "Alpha archived", status: "archived" },
  { id: "3", name: "Beta", status: "active" },
];
const columns = [{ field: "name", header: "Name" }, { field: "status", header: "Status" }];
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test.each([false, true])("a spanning footer follows optional columns and selection=%s, even when empty", async (selectable) => {
  render(<ToastProvider><RowsListView scope="local" presentation="embedded" rows={[]} selectable={selectable}
    columns={[columns[0]!, { ...columns[1]!, id: "state", hiddenByDefault: true, minWidth: 144 }]}
    onReorder={vi.fn()} footerRow={<button type="button">Add record</button>} emptyContent="Nothing here" />
  </ToastProvider>);
  const footer = screen.getByRole("button", { name: "Add record" }).closest("td")!;
  expect(footer.closest("tfoot")).toBeTruthy();
  // Reorder handle, the name column and the trailing fields-menu column (the optional column is hidden).
  expect(footer.colSpan).toBe(3 + Number(selectable));
  expect(screen.getByText("Nothing here").closest("td")?.colSpan).toBe(footer.colSpan);
  expect(screen.queryByRole("columnheader", { name: /^Status/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Visible fields" }));
  const status = await screen.findByRole("menuitemcheckbox", { name: "Status" });
  expect(status.getAttribute("aria-checked")).toBe("false");
  expect(screen.getByRole("menuitemcheckbox", { name: "Name" }).getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(status);
  expect(screen.getByRole("columnheader", { name: /^Status/ }).style.minWidth).toBe("144px");
  expect(footer.colSpan).toBe(4 + Number(selectable));
});

test("native visibility overrides defaults, while a required column remains visible", async () => {
  render(<ToastProvider><ResourceViewProvider scope="local" initialState={{ columnVisibility: { state: true, name: false } }}>
    <RowsListView rows={rows} columns={[{ ...columns[0]!, hideable: false }, { ...columns[1]!, id: "state", hiddenByDefault: true }]} />
  </ResourceViewProvider></ToastProvider>);
  expect(screen.getByRole("columnheader", { name: "Name" })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: /^Status/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Visible fields" }));
  expect((await screen.findByRole("menuitemcheckbox", { name: "Name" })).getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Status" }));
  expect(screen.queryByRole("columnheader", { name: /^Status/ })).toBeNull();
});

test("refreshing descriptors and row values preserves a focused cell editor", () => {
  const fixture = (name: string) => <ToastProvider><RowsListView scope="local" rows={[{ id: "1", name }]}
    columns={[{ field: "name", header: "Name", interactive: true,
      render: (row) => <input aria-label="Edit name" readOnly value={row.name} /> }]} />
  </ToastProvider>;
  const view = render(fixture("Alpha"));
  const editor = screen.getByRole("textbox", { name: "Edit name" });
  editor.focus();
  view.rerender(fixture("Changed"));
  expect(screen.getByRole("textbox", { name: "Edit name" })).toBe(editor);
  expect(document.activeElement).toBe(editor);
  expect((editor as HTMLInputElement).value).toBe("Changed");
});

test("reorder handles compose native drag/drop, isolate lists and retain external row dragging", () => {
  const onReorder = vi.fn();
  const view = render(<AppRuntimeProvider runtime={{}}><ToastProvider>
    <RowsListView scope="local" rows={rows} columns={columns} onReorder={onReorder}
      draggableRow={(row) => ({ type: "demo.record", data: row.id })} />
    <RowsListView scope="local" rows={rows} columns={columns} onReorder={onReorder} />
  </ToastProvider></AppRuntimeProvider>);
  const [first, second] = screen.getAllByRole("table").map((table) => within(table));
  const handles = first!.getAllByRole("button", { name: "Reorder row" });
  const target = handles[1]!.closest("tr")!;
  const dataTransfer = testDndTransfer();
  fireEvent.dragStart(handles[0]!, { dataTransfer });
  const payload = readDndPayload<string>(dataTransfer)!;
  expect(payload.data).toBe("1");
  fireEvent.dragEnter(target, { dataTransfer });
  fireEvent.dragOver(target, { dataTransfer });
  fireEvent.dragOver(target, { dataTransfer });
  expect(target.hasAttribute("data-drop-target")).toBe(true);
  fireEvent.dragLeave(target, { dataTransfer });
  expect(target.hasAttribute("data-drop-target")).toBe(false);
  fireEvent.drop(target, { dataTransfer });
  expect(onReorder).toHaveBeenCalledExactlyOnceWith("1", "2");
  fireEvent.drop(handles[0]!.closest("tr")!, { dataTransfer });
  fireEvent.drop(second!.getAllByRole("button", { name: "Reorder row" })[1]!.closest("tr")!, { dataTransfer });
  writeDndPayload(dataTransfer, { type: payload.type, data: { id: "1" } });
  fireEvent.drop(target, { dataTransfer });
  writeDndPayload(dataTransfer, { type: payload.type, data: "missing" });
  fireEvent.drop(target, { dataTransfer });
  expect(onReorder).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(handles[0]!, { key: "ArrowUp", altKey: true });
  expect(onReorder).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(handles[0]!, { key: "ArrowDown", altKey: true });
  expect(onReorder).toHaveBeenLastCalledWith("1", "2");
  fireEvent.dragStart(handles[0]!.closest("tr")!, { dataTransfer });
  expect(readDndPayload<string>(dataTransfer)?.type).toBe("demo.record");
  writeDndPayload(dataTransfer, payload);
  view.rerender(<AppRuntimeProvider runtime={{ auth: { user: null, status: "authenticated", hasRole: () => false,
    viewAs: { viewAs: { userId: "person" }, currentUser: null, realUser: null, viewablePeople: [], enter: vi.fn(), exit: vi.fn() },
  } }}><ToastProvider><RowsListView scope="local" rows={rows} columns={columns} onReorder={onReorder} /></ToastProvider></AppRuntimeProvider>);
  const blockedHandles = screen.getAllByRole("button", { name: "Reorder row" });
  const calls = onReorder.mock.calls.length;
  expect(blockedHandles[0]!.hasAttribute("disabled")).toBe(true);
  fireEvent.keyDown(blockedHandles[0]!, { key: "ArrowDown", altKey: true });
  fireEvent.dragStart(blockedHandles[0]!, { dataTransfer });
  fireEvent.drop(blockedHandles[1]!.closest("tr")!, { dataTransfer });
  expect(onReorder).toHaveBeenCalledTimes(calls);
  view.rerender(<AppRuntimeProvider runtime={{}}><ToastProvider><RowsListView scope="local" rows={rows} columns={columns} /></ToastProvider></AppRuntimeProvider>);
  expect(screen.queryByRole("button", { name: "Reorder row" })).toBeNull();
  expect(screen.queryByRole("columnheader", { name: "Reorder row" })).toBeNull();
});

test("a row that may not move shows a lock instead of a handle and never starts a reorder", () => {
  const onReorder = vi.fn();
  render(<AppRuntimeProvider runtime={{}}><ToastProvider>
    <RowsListView scope="local" rows={rows} columns={columns} onReorder={onReorder}
      canReorderRow={(row) => row.id !== "1"} />
  </ToastProvider></AppRuntimeProvider>);
  const [, locked, free] = screen.getAllByRole("row");
  expect(within(locked!).getByRole("img", { name: "Locked row" })).toBeTruthy();
  expect(within(locked!).queryByRole("button", { name: "Reorder row" })).toBeNull();
  const handle = within(free!).getByRole("button", { name: "Reorder row" });
  // Another row may still move past it.
  fireEvent.keyDown(handle, { key: "ArrowUp", altKey: true });
  expect(onReorder).toHaveBeenLastCalledWith("2", "1");
  // A forged drag of the locked row is refused by the owner.
  const dataTransfer = testDndTransfer();
  fireEvent.dragStart(handle, { dataTransfer });
  const payload = readDndPayload<string>(dataTransfer)!;
  writeDndPayload(dataTransfer, { type: payload.type, data: "1" });
  fireEvent.drop(free!, { dataTransfer });
  expect(onReorder).toHaveBeenCalledTimes(1);
});

test.each(["unregistered widgets", "registered widgets", "scalar defaults"] as const)(
  "local rows display DateTime, Date and Float values with %s",
  (mode) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
    const query = ResourceQuery.forRows({ fields: {
      submitted: { scalar: "DateTime" }, due: { scalar: "Date" }, duration: { scalar: "Float" },
    } });
    render(<AppRuntimeProvider runtime={{ widgets: mode === "registered widgets" ? defaultWidgets : {} }}>
      <ToastProvider>
        <RowsListView scope="local" query={query}
          rows={[{ id: "1", submitted: "2026-10-04T09:00:00Z", due: "2026-10-08", duration: 2 }]}
          columns={[
            { field: "submitted", header: "Submitted", widget: mode === "scalar defaults" ? undefined : "datetime" },
            { field: "due", header: "Need by", widget: mode === "scalar defaults" ? undefined : "date" },
            { field: "duration", header: "Duration" },
          ]} />
      </ToastProvider>
    </AppRuntimeProvider>);
    // The last cell is the empty trailing cell under the visible-fields header.
    expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["Oct 4", "Oct 8", "2", ""]);
  },
);

function fixture(filter: ResourceViewFilter) {
  render(<ToastProvider><ResourceViewProvider scope="local" initialState={{ filter }}>
    <RowsListView rows={rows} columns={columns} emptyContent="No matching rows" />
  </ResourceViewProvider></ToastProvider>);
}

test("embedded rows show search and a pager only once they outgrow a page", () => {
  const many = Array.from({ length: 60 }, (_, index) => ({ id: String(index + 1), name: `Row ${index + 1}`, status: "active" }));
  const view = (presentation: "embedded" | "page", list = rows) =>
    render(<ToastProvider><RowsListView scope="local" rows={list} columns={columns} presentation={presentation} /></ToastProvider>);
  view("embedded");
  expect(screen.queryByLabelText("Search options")).toBeNull();
  expect(screen.queryByLabelText(/^Records /)).toBeNull();
  cleanup();
  // Past the default page size the list is browsed, so search and the pager return.
  view("embedded", many);
  expect(screen.getByLabelText("Search options")).toBeTruthy();
  expect(screen.getByLabelText("Records 1-50 / 60")).toBeTruthy();
  cleanup();
  view("page");
  expect(screen.getByLabelText("Search options")).toBeTruthy();
});

test("bare rows search across declared columns keeps its other filters", () => {
  fixture({ title: { iContains: "alpha" }, status: { exact: "active" } });
  expect(screen.getByText("Alpha")).toBeTruthy();
  expect(screen.queryByText("Alpha archived")).toBeNull();
  expect(screen.queryByText("Beta")).toBeNull();
});

test("invalid local query shows a repairable error before rendering records", () => {
  fixture({ stale: { exact: "value" } });
  expect(screen.getByText(/unknown or non-filterable field/)).toBeTruthy();
  expect(screen.queryByText("Alpha")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reset filters, sorting and grouping" }));
  expect(screen.getByText("Alpha")).toBeTruthy();
  expect(screen.getByText("Beta")).toBeTruthy();
});

test("typed local rows search only text-capable columns while preserving Boolean constraints", () => {
  const query = ResourceQuery.forRows({ fields: { name: { scalar: "String" }, active: { scalar: "Boolean" } } });
  render(<ToastProvider><ResourceViewProvider scope="local" initialState={{ filter: { title: { iContains: "alpha" }, active: { exact: false } } }}>
    <RowsListView query={query} rows={[
      { id: "1", name: "Alpha", active: false },
      { id: "2", name: "Alpha active", active: true },
      { id: "3", name: "Beta", active: false },
    ]} columns={[{ field: "name", header: "Name" }, { field: "active", header: "Active" }]} />
  </ResourceViewProvider></ToastProvider>);
  expect(screen.getByText("Alpha")).toBeTruthy();
  expect(screen.queryByText("Alpha active")).toBeNull();
  expect(screen.queryByText("Beta")).toBeNull();
});

test("local rows keep curated group shortcuts separate from complete query capabilities", async () => {
  const query = ResourceQuery.forRows({ fields: {
    name: { scalar: "String" },
    owner: { scalar: "String" },
  } });
  render(<ToastProvider><ResourceViewProvider scope="local">
    <RowsListView
      query={query}
      rows={[{ id: "1", name: "Alpha", owner: "Ada" }]}
      columns={[{ field: "name", header: "Name" }]}
      groupOptions={[]}
    />
  </ResourceViewProvider></ToastProvider>);

  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  expect(screen.queryByRole("button", { name: "Owner" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "More axes…" }));
  const field = screen.getByRole("combobox", { name: "Group axis" });
  fireEvent.click(field);
  expect(await screen.findByRole("option", { name: "Owner" })).toBeTruthy();
});

test("drops a curated date shortcut when its granularity is no longer supported", async () => {
  const query = ResourceQuery.fromContract(testResourceQuery({
    fields: { document_date: testQueryField("document_date", { scalar: "Date" }) },
    axes: {
      document_date: testQueryAxis("document_date", {
        kind: "date",
        extractions: [{ name: "month", input: "MONTH", key: "document_date_month" }],
      }),
    },
  }));
  render(<ToastProvider><ResourceViewProvider scope="local">
    <RowsListView
      query={query}
      rows={[{ id: "1", document_date: "2026-09-19" }]}
      columns={[{ field: "document_date", header: "Document date" }]}
      groupOptions={[{
        id: "document-date-day",
        label: "Document date by day",
        group: { field: "document_date", granularity: "day" },
      }]}
    />
  </ResourceViewProvider></ToastProvider>);

  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  expect(screen.queryByRole("button", { name: "Document date by day" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "More axes…" }));
  const granularity = screen.getByRole("combobox", { name: "Group granularity" });
  expect(granularity.textContent).toContain("Month");
  fireEvent.click(granularity);
  expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(["Month"]);
});

test("the native row model sorts declared Decimal strings numerically without losing precision", () => {
  const query = ResourceQuery.forRows({ fields: { name: { scalar: "String" }, amount: { scalar: "Decimal" } } });
  render(<ToastProvider><ResourceViewProvider scope="local" initialState={{ sorting: [{ id: "amount", desc: false }] }}>
    <RowsListView query={query} rows={[
      { id: "1", name: "Larger", amount: "9007199254740993.01" },
      { id: "2", name: "Small", amount: "2" },
      { id: "3", name: "Smaller", amount: "9007199254740993.00" },
    ]} columns={[{ field: "name", header: "Name" }]} />
  </ResourceViewProvider></ToastProvider>);
  expect(screen.getAllByRole("cell").map((cell) => cell.textContent).filter(Boolean)).toEqual(["Small", "Smaller", "Larger"]);
});

test("declared local fields support aliases and text search beside a virtual render column", () => {
  const query = ResourceQuery.forRows({ fields: {
    name: { scalar: "String" }, status: { scalar: "String", identityPath: "state" },
  } });
  let view!: ResourceViewContextValue;
  function Records() {
    view = useResourceView();
    return <RowsListView query={query} rows={[{ id: "1", name: "Alpha", state: "clean", ahead: 1 }]}
      columns={[
        { field: "name", header: "Name" },
        { field: "status", header: "Status", render: (row) => row.state },
        { field: "drift", header: "Drift", render: (row) => `↑${row.ahead}` },
      ]} emptyContent="No matching rows" />;
  }
  render(<ToastProvider><ResourceViewProvider scope="local" initialState={{ filter: { title: { iContains: "absent" } } }}>
    <Records />
  </ResourceViewProvider></ToastProvider>);
  expect(screen.getByText("No matching records")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  act(() => view.setFilter({ status: { exact: "clean" } }));
  expect(screen.getByText("Alpha")).toBeTruthy();
  expect(screen.getByText("↑1")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Drift.*sort/i })).toBeNull();
});
