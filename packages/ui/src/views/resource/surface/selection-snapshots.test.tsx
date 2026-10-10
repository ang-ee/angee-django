// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterContextProvider, RouterProvider } from "@tanstack/react-router";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { ResourceQuery } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

import { baseIcons } from "../../../chrome/icon-registry";
import { ToastProvider } from "../../../feedback";
import { AppRuntimeProvider } from "../../../runtime";
import { createUiTestProviders } from "../../../testing";
import { Column } from "../../page";
import { List } from "../List";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "../resource-view-context";

const resource = testDataResource("notes.Note", {
  capabilities: ["list"], roots: { aggregate: "notes_aggregate", create: undefined, delete: undefined },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, title: {}, revision: { scalar: "Int" } } }).contract,
});
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource], queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

test.each([
  ["local", "row"], ["local", "page"], ["route", "row"], ["route", "page"],
] as const)("%s %s selection snapshots survive page unloads and List remounts until reselection", async (scope, selection) => {
  const original = { id: "one", title: "First row", revision: 1 };
  const updated = { ...original, title: "Updated row", revision: 2 };
  const second = { id: "two", title: "Second row", revision: 1 };
  let revised = false;
  let view!: ResourceViewContextValue;
  let showList!: (visible: boolean) => void;
  const bulk = vi.fn();
  function Collection() {
    view = useResourceView();
    const [visible, setVisible] = useState(true);
    showList = setVisible;
    return visible ? <List resource={resource.modelLabel} scope="inherit" selectable fields={["revision"]}
      bulkActions={(_ids, _clear, collection) => { bulk(collection); return <span>Selected rows</span>; }}>
      <Column field="title" />
    </List> : null;
  }
  function View() {
    return <ResourceViewProvider resource={resource.modelLabel} scope={scope} initialState={{ pageSize: 10 }}>
      <Collection />
    </ResourceViewProvider>;
  }
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: "/", validateSearch: (search) => search, component: View });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const content = scope === "route" ? <RouterProvider router={router} /> : <RouterContextProvider router={router}><View /></RouterContextProvider>;
  render(<Provider dataProvider={{ getList: async ({ pagination }) => ({
    data: pagination?.currentPage === 2 ? [second] : [revised ? updated : original], total: 11,
  }) }}><AppRuntimeProvider runtime={{ icons: baseIcons }}><ToastProvider>{content}</ToastProvider></AppRuntimeProvider></Provider>, { reactStrictMode: true });
  await screen.findByText(original.title);
  fireEvent.click(screen.getByRole("checkbox", { name: selection === "row" ? "Select row" : "Select all rows on this page" }));
  expect(view.selectedRows).toEqual([original]);
  act(() => view.setPage(2));
  await screen.findByText(second.title);
  expect(view.selectedRows).toEqual([original]);
  act(() => showList(false));
  expect(view.selectedRows).toEqual([original]);
  act(() => showList(true));
  await screen.findByText(second.title);
  expect(bulk).toHaveBeenLastCalledWith(expect.objectContaining({ selectedRows: [original] }));
  revised = true;
  act(() => view.setPage(1));
  await screen.findByText(updated.title);
  expect(view.selectedRows).toEqual([original]);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select row" }));
  expect(view.selectedRows).toEqual([]);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select row" }));
  await waitFor(() => expect(view.selectedRows).toEqual([updated]));
  expect(bulk).toHaveBeenLastCalledWith(expect.objectContaining({ selectedRows: [updated] }));
});
