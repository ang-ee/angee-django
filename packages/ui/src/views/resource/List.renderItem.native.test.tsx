// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { ResourceQuery } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { baseIcons } from "../../chrome/icon-registry";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { Column } from "../page";
import { List } from "./List";
import { ResourceList } from "./ResourceList";
import { ResourceViewProvider } from "./resource-view-context";
import type { ResourceViewInitialState } from "./resource-view-model";

const resource = testDataResource("notes.Note", {
  capabilities: ["list"], roots: { aggregate: "notes_aggregate", create: undefined, delete: undefined },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, title: { scalar: "String" } } }).contract,
});
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://ordered-items", resources: [resource],
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

function fixture({ state, mode }: { state?: ResourceViewInitialState; mode?: "loading" | "empty" | "error" } = {}) {
  const getList = vi.fn(async ({ pagination }: GetListParams) => {
    if (mode === "loading") return new Promise<never>(() => undefined);
    if (mode === "error") throw new Error("Read unavailable");
    const all = mode === "empty" ? [] : [
      { id: "a", title: "First note" }, { id: "b", title: "Second note" }, { id: "c", title: "Third note" },
    ];
    const size = pagination?.pageSize ?? 2;
    const offset = ((pagination?.currentPage ?? 1) - 1) * size;
    return { data: all.slice(offset, offset + size), total: all.length };
  });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterContextProvider router={router}><Provider dataProvider={{ getList }}>
    <AppRuntimeProvider runtime={{ icons: baseIcons }}><ModalsHost><ToastProvider>
      <ResourceViewProvider resource={resource.modelLabel} scope="local" initialState={{ pageSize: 2, ...state }}>
        <ResourceList resource={resource.modelLabel} scope="inherit" presentation="embedded" hideCreate>
          <List renderItem={(row) => <h3>{String(row.title)}</h3>} emptyContent="No notes">
            <Column field="title" />
          </List>
        </ResourceList>
      </ResourceViewProvider>
    </ToastProvider></ModalsHost></AppRuntimeProvider>
  </Provider></RouterContextProvider>);
  return getList;
}

test("List item declarations retain native paging and ordered list semantics", async () => {
  const getList = fixture();
  await screen.findByRole("heading", { name: "First note" });
  const list = screen.getByRole("list");
  expect(list.tagName).toBe("OL");
  expect(list.getAttribute("start")).toBe("1");
  expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByRole("heading", { name: "Third note" });
  expect(screen.queryByRole("heading", { name: "First note" })).toBeNull();
  expect(screen.getByRole("list").getAttribute("start")).toBe("3");
  expect(getList.mock.calls.at(-1)?.[0].pagination).toMatchObject({ currentPage: 2, pageSize: 2 });
});

test.each(["loading", "empty", "error"] as const)("item collections retain the shared %s surface", async (mode) => {
  fixture({ mode });
  if (mode === "loading") expect(await screen.findByRole("status")).toBeTruthy();
  else if (mode === "empty") expect(await screen.findByText("No notes")).toBeTruthy();
  else expect((await screen.findByRole("alert")).textContent).toContain("Read unavailable");
  expect(screen.queryByRole("listitem")).toBeNull();
});

test.each([
  { view: "board" as const },
  { groupStack: [{ field: "title" }] },
])("unsupported item view state blocks reads and resets through the resource owner", async (state) => {
  const getList = fixture({ state });
  expect((await screen.findByRole("alert")).textContent).toContain("Ordered item lists");
  expect(getList).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Reset filters, sorting and grouping" }));
  expect(await screen.findByRole("heading", { name: "First note" })).toBeTruthy();
});
