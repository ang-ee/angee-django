// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
import { ResourceViewProvider, useResourceView } from "./resource-view-context";
import { RowsListView } from "./RowsListView";

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
afterEach(() => { cleanup(); clearClients(); });

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
