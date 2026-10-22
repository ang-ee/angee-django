// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { GetListParams } from "@refinedev/core";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider } from "../runtime/runtime";
import { createUiTestProviders } from "../testing";
import { ToastProvider } from "../feedback";
import { defaultWidgets } from "../widgets";
import { List } from "../views/resource/List";
import { Column } from "../views/page/Column";
import { BUILTIN_DASHBOARD_WIDGET_KINDS } from "./kinds";
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
