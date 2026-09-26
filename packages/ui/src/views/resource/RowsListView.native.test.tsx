// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ResourceQuery } from "@angee/metadata";
import { testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { ToastProvider } from "../../feedback";
import { RowsListView } from "./RowsListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import type { ResourceViewFilter } from "./resource-view-model";

const rows = [
  { id: "1", name: "Alpha", status: "active" },
  { id: "2", name: "Alpha archived", status: "archived" },
  { id: "3", name: "Beta", status: "active" },
];
const columns = [{ field: "name", header: "Name" }, { field: "status", header: "Status" }];
afterEach(cleanup);

function fixture(filter: ResourceViewFilter) {
  render(<ToastProvider><ResourceViewProvider scope="local" initialState={{ filter }}>
    <RowsListView rows={rows} columns={columns} emptyContent="No matching rows" />
  </ResourceViewProvider></ToastProvider>);
}

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

  fireEvent.click(screen.getByLabelText("Filter and group"));
  expect(screen.queryByRole("button", { name: "Owner" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
  const field = screen.getByLabelText("Group field");
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

  fireEvent.click(screen.getByLabelText("Filter and group"));
  expect(screen.queryByRole("button", { name: "Document date by day" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add custom group" }));
  expect(screen.getByLabelText("Group granularity").textContent).toContain("Month");
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
  expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["Small", "Smaller", "Larger"]);
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
