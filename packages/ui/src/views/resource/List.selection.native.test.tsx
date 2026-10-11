// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { Filter, ResourceQuery } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";

import { baseIcons } from "../../chrome/icon-registry";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { Column } from "../page";
import { List } from "./List";
import { ResourceList } from "./ResourceList";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { RowsListView } from "./RowsListView";
import { ganttResources, ganttLane, ganttLanes, ganttRecord, scheduledRecord } from "../../../tests/gantt-fixtures";
import type { GanttViewProps } from "../gantt/GanttView";

const drawing = vi.hoisted(() => ({ props: null as GanttViewProps | null }));
vi.mock("../gantt/GanttView", () => ({ GanttView: (props: GanttViewProps) => {
  drawing.props = props;
  return <div aria-label="Schedule chart" />;
} }));

const held = { status: "draft" };
const resource = testDataResource("notes.Note", {
  capabilities: ["list"], roots: { aggregate: "notes_aggregate", create: undefined, delete: undefined },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  query: ResourceQuery.forRows({ fields: {
    id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" },
  } }).contract,
});
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://view-selection", resources: [resource],
  queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); drawing.props = null; });

function SelectionControls() {
  const view = useResourceView();
  return <>
    <button onClick={() => view.setFilter(held)}>Held drafts</button>
    <button onClick={() => view.setFilter({})}>All notes</button>
  </>;
}

test("List resolves selection and bulk actions against the current view", async () => {
  const observeBulkCollection = vi.fn();
  const baseFilter = { title: { exact: "First note" } };
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterContextProvider router={router}><Provider dataProvider={{
    getList: async () => ({ data: [{ id: "one", title: "First note", status: "draft" }], total: 1 }),
  }}><AppRuntimeProvider runtime={{ icons: baseIcons }}><ModalsHost><ToastProvider>
    <ResourceViewProvider resource={resource.modelLabel} scope="local" baseFilter={baseFilter}>
      <SelectionControls />
      <RowsListView rows={[{ id: "one", title: "First note", status: "draft" }]}
        columns={[{ field: "title", render: () => "Readonly note" }, { field: "status", hiddenByDefault: true }]} scope="inherit" />
      <ResourceList resource={resource.modelLabel} scope="inherit" presentation="embedded" hideCreate>
        <List selectable
          bulkActions={(ids, clear, collection) => {
            observeBulkCollection(collection);
            return <button onClick={clear}>Clear {ids.size} held selection</button>;
          }}
        >
          <Column field="title" />
        </List>
      </ResourceList>
    </ResourceViewProvider>
  </ToastProvider></ModalsHost></AppRuntimeProvider></Provider></RouterContextProvider>);
  await screen.findByText("First note");
  expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Held drafts" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select row" }));
  expect(observeBulkCollection).toHaveBeenLastCalledWith({
    filter: Filter.combine(baseFilter, held),
    selectedRows: [{ id: "one", title: "First note", status: "draft" }],
  });
  expect(screen.getByText("Readonly note").closest("tr")?.hasAttribute("data-selected")).toBe(false);
  fireEvent.click(await screen.findByRole("button", { name: "Clear 1 held selection" }));
  expect(screen.queryByRole("button", { name: "Clear 1 held selection" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select row" }));
  fireEvent.click(screen.getByRole("button", { name: "All notes" }));
  expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  expect(screen.queryByRole("button", { name: "Clear 1 held selection" })).toBeNull();
});

test.each([
  { selectable: false, presentation: "page" },
  { selectable: undefined, presentation: "embedded" },
  { selectable: true, presentation: "page" },
  { selectable: undefined, presentation: "page" },
] as const)("linked Gantt List resolves selection for %j", async (props) => {
  let view!: ResourceViewContextValue;
  function Probe() { view = useResourceView(); return null; }
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterContextProvider router={router}><Provider resources={ganttResources} dataProvider={{
    getList: async ({ resource: name }) => name === "lanes"
      ? { data: ganttLanes, total: 2 } : { data: [scheduledRecord], total: 1 },
  }}><ResourceViewProvider resource={ganttLane.modelLabel} scope="local"
    initialState={{ view: "gantt", anchor: "2026-09-01", selectedIds: ["lane-a"] }}>
    <Probe />
    <List resource={ganttLane.modelLabel} scope="inherit" {...props}
      gantt={{ linked: { resource: ganttRecord.modelLabel, lane: "lane" }, start: "start", end: "end" }}>
      <Column field="name" />
    </List>
  </ResourceViewProvider></Provider></RouterContextProvider>);
  await screen.findByLabelText("Schedule chart");
  await waitFor(() => expect(drawing.props?.resources).toHaveLength(2));
  if (props.selectable ?? props.presentation !== "embedded") {
    expect(screen.getByText("1 selected")).toBeDefined();
    expect(drawing.props?.selectedRows).toEqual(["lane-a"]);
    expect(drawing.props?.onSelectedRowsChange).toBeTypeOf("function");
    act(() => drawing.props?.onSelectedRowsChange?.(["lane-b"]));
    expect(drawing.props?.selectedRows).toEqual(["lane-b"]);
    expect(view.selectedRows).toEqual([ganttLanes[1]]);
  } else {
    expect(drawing.props?.onSelectedRowsChange).toBeUndefined();
    expect(drawing.props?.selectedRows).toEqual([]);
    expect(screen.queryByText("1 selected")).toBeNull();
  }
});

test("RowsListView bulk actions describe the normalized filter its table applies", () => {
  const collection = vi.fn();
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterContextProvider router={router}><Provider>
    <ResourceViewProvider scope="local" initialState={{ filter: { title: { iContains: "needle" } } }}>
      <RowsListView rows={[{ id: "one", title: "Other", name: "Needle" }]}
        columns={[{ field: "title" }, { field: "name" }]} scope="inherit" selectable
        bulkActions={(_ids, _clear, value) => { collection(value); return <span>Selected row</span>; }} />
    </ResourceViewProvider>
  </Provider></RouterContextProvider>);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select row" }));
  expect(collection).toHaveBeenLastCalledWith({
    filter: { OR: [{ title: { iContains: "needle" } }, { name: { iContains: "needle" } }] },
    selectedRows: [{ id: "one", title: "Other", name: "Needle" }],
  });
});
