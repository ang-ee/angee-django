// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { OperationDocumentsProvider } from "@angee/refine";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider } from "../runtime/runtime";
import { createUiTestProviders } from "../testing";
import { ModalsHost, ToastProvider } from "../feedback";
import { defaultWidgets } from "../widgets";
import { List } from "../views/resource/List";
import { Column } from "../views/page/Column";
import { useResourceView, type ResourceViewContextValue } from "../views/resource/resource-view-context";
import { RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY } from "../views/resource/resource-view-favorites";
import { BUILTIN_DASHBOARD_WIDGET_KINDS } from "./kinds";
import { DashboardSurface } from "./surface";
import type { DashboardWidgetData, HostedResourceViewProps, WidgetSpec } from "./headless";

const resource = testDataResource("notes.Note", {
  roots: { list: "notes", aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteBoolExp", order: "NoteOrderBy" },
  query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID", nullable: false }),
    title: testQueryField("title"),
    status: testQueryField("status"),
    company: testQueryField("company", { scalar: "ID" }),
  } }),
});
const { Provider, clearClients } = createUiTestProviders({ resources: [resource] });
const HostedList = BUILTIN_DASHBOARD_WIDGET_KINDS.find(({ id }) => id === "resourceView")!.Component;
const spec: WidgetSpec = {
  schemaVersion: 1, id: "inbox", kind: "resourceView", kindVersion: 1, title: "Inbox",
  data: { shape: "resourceView", preset: "notes.inbox" }, options: { fullViewRoute: "notes.list", pager: true },
  x: 0, y: 0, w: 12, h: 6, isArchived: false,
};
const data: DashboardWidgetData = {
  value: null, rows: [], series: [], queryFields: {}, identity: null, fetching: false,
  error: null, live: false, updatedAt: null, refetch: () => {},
};

afterEach(() => { cleanup(); clearClients(); });

test("a hosted native list pages its scoped query and resets the window when the page scope changes", async () => {
  const getList = vi.fn(async ({ pagination }: GetListParams) => ({
    data: [{ id: `row-${pagination?.currentPage}`, title: `Window ${pagination?.currentPage}` }], total: 35,
  }));
  const count = vi.fn();
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  function View(props: HostedResourceViewProps) {
    return <List resource={resource.modelLabel} {...props}>
      <Column field="title" header="Title" />
    </List>;
  }
  function Host({ company }: { company: string }) {
    return <RouterContextProvider router={router}><Provider dataProvider={{ getList }}>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets, resourceViews: {
        "notes.inbox": { id: "notes.inbox", label: "Inbox", resource: resource.modelLabel,
          pageSize: 10, fixedFilter: { status: { exact: "open" } } },
      } }}><ToastProvider><HostedList spec={spec} data={data} hostedView={View} onCountChange={count}
        pageScope={{ resource: resource.modelLabel, filter: { company: { exact: company } } }} />
      </ToastProvider></AppRuntimeProvider>
    </Provider></RouterContextProvider>;
  }
  const mounted = render(<Host company="company-a" />);
  await screen.findByText("Window 1");
  await waitFor(() => expect(count).toHaveBeenCalledWith(35));
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText("Window 2");
  expect(getList.mock.lastCall?.[0]).toMatchObject({ pagination: { currentPage: 2, pageSize: 10 },
    meta: { gqlVariables: { where: { _and: [{ company: { _eq: "company-a" } }, { status: { _eq: "open" } }] } } } });

  const before = getList.mock.calls.length;
  mounted.rerender(<Host company="company-b" />);
  await waitFor(() => expect(getList.mock.calls.length).toBeGreaterThan(before));
  expect(getList.mock.calls.slice(before).every(([request]) => request.pagination?.currentPage === 1)).toBe(true);
  expect(getList.mock.lastCall?.[0].meta?.gqlVariables?.where)
    .toEqual({ _and: [{ company: { _eq: "company-b" } }, { status: { _eq: "open" } }] });
});

test.each(["client", "server"] as const)("a hosted %s list applies a grouped favorite through Search options while retaining its fixed preset", async (rowModel) => {
  const groupedResource = { ...resource, rowModel, roots: { ...resource.roots, groups: "notes_groups" },
    query: { ...resource.query, axes: { company: testQueryAxis("company", {
      server: { input: "company", key: "company" },
      drill: { kind: "value", field: "company", valueKey: "company", nullMode: "isNull", valueMap: [] },
    }) } } };
  const getList = vi.fn(async () => ({ data: [
    { id: "1", title: "Bill one", company: "company-a", status: "open" },
    { id: "2", title: "Bill two", company: "company-b", status: "open" },
  ], total: 2 }));
  let view!: ResourceViewContextValue;
  function View(props: HostedResourceViewProps) {
    view = useResourceView();
    return <List resource={resource.modelLabel} {...props} baseFilter={{ status: { inList: ["open", "draft"] } }}>
      <Column field="title" header="Title" /><Column field="company" header="Company" />
    </List>;
  }
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterContextProvider router={router}><Provider resources={[groupedResource]}
    dataProvider={{ getList, custom: vi.fn(async () => ({ data: {
      notes_groups: [{ key: { company: "company-a" }, aggregate: { count: 2 } }], totalCount: 1,
    } })) }}>
    <OperationDocumentsProvider documents={{ console: { groups: {
      "notes.Note": "query Groups { notes_groups { key } totalCount }",
    } } }}>
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets,
      dashboards: { definitions: {}, resourceDefaults: {}, store: null, widgetKinds: Object.fromEntries(
        BUILTIN_DASHBOARD_WIDGET_KINDS.map((kind) => [kind.id, kind])) },
      resourceViews: {
      "notes.inbox": { id: "notes.inbox", preset: "notes.inbox", label: "Bill inbox", resource: resource.modelLabel,
        pageSize: 10, fixedFilter: { status: { exact: "open" } } },
    }, userPreferences: { available: true, preferences: {
      [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: { version: 1, models: { "notes.Note": [
        { id: "favorite:bills-by-company", label: "Bills by company", pageSize: 50, groupStack: [{ field: "company" }] },
      ] } },
    }, patchPreferences: async () => undefined } }}><ToastProvider><ModalsHost><DashboardSurface
      target={{ scope: "addon", key: "diagnosis" }} definition={{ key: "diagnosis", title: "Inbox", revision: "1",
        widgets: [spec], views: { "notes.inbox": View } }}
      pageScope={{ resource: resource.modelLabel, filter: { company: { exact: "company-a" } } }} />
    </ModalsHost></ToastProvider></AppRuntimeProvider></OperationDocumentsProvider>
  </Provider></RouterContextProvider>);
  await screen.findByText("Bill one");
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(await screen.findByRole("button", { name: "Bills by company" }));
  await waitFor(() => expect(view.state.groupStack).toEqual([{ field: "company" }]));
  expect(view.state.preset).toBe("notes.inbox");
  expect(view.state.pagination.pageSize).toBe(50);
  expect(view.baseFilter).toEqual({ company: { exact: "company-a" }, status: { exact: "open" } });
  const trigger = screen.getByRole("button", { name: "Search options" });
  if (trigger.getAttribute("aria-expanded") === "false") fireEvent.click(trigger);
  expect(screen.getAllByRole("button", { name: "Remove Company" }).length).toBeGreaterThan(0);
});
