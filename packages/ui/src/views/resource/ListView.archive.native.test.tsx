// @vitest-environment happy-dom

import { ResourceQuery, type QueryFilter, type Row } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { GetListParams } from "@refinedev/core";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { ListView } from "./ListView";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { RecordChromeProvider } from "./record-chrome-context";
import { BUILTIN_DASHBOARD_WIDGET_KINDS } from "../../dashboard/kinds";
import { useDashboardWidgetData } from "../../dashboard/data";
import type { DashboardRegistry, HostedResourceViewProps, WidgetSpec } from "../../dashboard/headless";
import type { ResourceViewPreset } from "./resource-view-model";

const contract = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, name: { scalar: "String" }, is_archived: { scalar: "Boolean" },
} }).contract;
const resource = testDataResource("example.Record", {
  recordRepresentation: "name",
  roots: { aggregate: "records_aggregate" },
  typeNames: { filter: "RecordBoolExp", order: "RecordOrderBy" },
  query: contract,
  fields: Object.entries(contract.fields).map(([name, field]) => ({ name, kind: "scalar" as const,
    scalar: field.scalar, archivable: name === "is_archived", readable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false })),
});
const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://archive-list",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

const savedRecord = { resource: "example.Parent", canonicalResource: "example.Parent", recordId: "parent-1",
  record: { id: "parent-1" }, dataProviderName: "console", formReadOnly: false };
const dashboards: DashboardRegistry = {
  store: null,
  resourceDefaults: { [resource.modelLabel]: "example.records" },
  definitions: { "example.records": { key: "example.records", title: "Record summary", revision: "1", widgets: [{
    schemaVersion: 1, id: "records", kind: "table", kindVersion: 1, title: "Records",
    data: { shape: "rows", source: { resource: resource.modelLabel, fields: ["name"] } },
    options: {}, x: 0, y: 0, w: 6, h: 4, isArchived: false,
  }] } },
  widgetKinds: Object.fromEntries(BUILTIN_DASHBOARD_WIDGET_KINDS.map((kind) => [kind.id, kind])),
};

function collection(options: {
  archivable?: boolean;
  embedded?: boolean;
  savedRecord?: boolean;
  clientRowModel?: boolean;
  rows?: Row[];
  baseFilter?: QueryFilter;
  filter?: QueryFilter;
  preset?: ResourceViewPreset;
} = {}) {
  const declared = options.archivable === false
    ? { ...resource, fields: resource.fields?.map((field) => ({ ...field, archivable: false })) }
    : resource;
  const metadata = options.clientRowModel ? { ...declared, rowModel: "client" as const } : declared;
  const rows = options.rows ?? [{ id: "record-1", name: "One", is_archived: false }];
  const getList = vi.fn(async (_params: GetListParams) => ({
    data: rows, total: rows.length,
  }));
  let view!: ResourceViewContextValue;
  function Collection() {
    view = useResourceView();
    return <ListView resource={resource.modelLabel} columns={[{ field: "name" }]}
      scope="inherit" presentation={options.embedded ? "embedded" : "page"} baseFilter={options.baseFilter} />;
  }
  const content = <ResourceViewProvider resource={resource.modelLabel} scope="local"
    presetIds={options.preset ? [options.preset.id] : []}
    initialState={{ filter: options.filter, preset: options.preset?.id }}><Collection /></ResourceViewProvider>;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterContextProvider router={router}><Provider resources={[metadata]} dataProvider={{ getList }}>
    <AppRuntimeProvider runtime={{ dashboards, resourceViews: options.preset ? { [options.preset.id]: options.preset } : {} }}>
      <ModalsHost><ToastProvider>
        {options.savedRecord ? <RecordChromeProvider value={savedRecord}>{content}</RecordChromeProvider> : content}
      </ToastProvider></ModalsHost>
    </AppRuntimeProvider>
  </Provider></RouterContextProvider>);
  return { getList, get view() { return view; } };
}

test("a default archive-aware collection requests unarchived rows and selects their flag", async () => {
  const { getList } = collection();
  await waitFor(() => expect(getList).toHaveBeenCalled());
  for (const [request] of getList.mock.calls) {
    expect(request.meta?.gqlVariables?.where).toEqual({ is_archived: { _eq: false } });
    expect(request.meta?.fields).toContain("is_archived");
  }
});

test.each(["base", "editable", "preset"] as const)("an explicit %s filter can request archived rows", async (source) => {
  const filter = { OR: [{ is_archived: { exact: true } }, { name: { exact: "One" } }] };
  const { getList } = collection(source === "base" ? { baseFilter: filter }
    : source === "editable" ? { filter }
    : { preset: { id: "example.archived", preset: "example.archived", label: "Archived", resource: resource.modelLabel,
      view: "list", fixedFilter: filter } });
  await waitFor(() => expect(getList).toHaveBeenCalled());
  for (const [request] of getList.mock.calls) {
    expect(request.meta?.gqlVariables?.where).toEqual(ResourceQuery.from(resource).toWhere(filter));
  }
});

test("an ordinary resource retains its original scope", async () => {
  const filter = { name: { exact: "One" } };
  const { getList } = collection({ archivable: false, baseFilter: filter });
  await waitFor(() => expect(getList).toHaveBeenCalled());
  for (const [request] of getList.mock.calls) {
    expect(request.meta?.gqlVariables?.where).toEqual({ name: { _eq: "One" } });
  }
});

test.each([
  { embedded: false, savedRecord: false, scoped: true },
  { embedded: true, savedRecord: false, scoped: true },
  { embedded: false, savedRecord: true, scoped: true },
  { embedded: true, savedRecord: true, scoped: false },
])("a list and its dashboard agree: embedded=$embedded savedRecord=$savedRecord", async (context) => {
  const f = collection(context);
  await waitFor(() => expect(f.getList).toHaveBeenCalled());
  const where = context.scoped ? { is_archived: { _eq: false } } : {};
  expect(f.getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual(where);
  f.getList.mockClear();
  act(() => f.view.setView("dashboard"));
  await screen.findByText("Record summary");
  await waitFor(() => expect(f.getList).toHaveBeenCalled());
  expect(f.getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual(where);
});

test("a client row-model list filters archived rows locally without changing the full-set read", async () => {
  const f = collection({ clientRowModel: true, rows: [
    { id: "active", name: "Active record", is_archived: false },
    { id: "archived", name: "Archived record", is_archived: true },
  ] });
  await screen.findByText("Active record");
  expect(screen.queryByText("Archived record")).toBeNull();
  expect(f.getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({});
  expect(f.getList.mock.calls.at(-1)?.[0].meta?.fields).toContain("is_archived");
  const reads = f.getList.mock.calls.length;
  act(() => f.view.setFilter({ is_archived: { exact: true } }));
  await screen.findByText("Archived record");
  expect(screen.queryByText("Active record")).toBeNull();
  expect(f.getList).toHaveBeenCalledTimes(reads);
});

test("a dashboard resource-view widget keeps the default archive scope despite compact chrome", async () => {
  const ResourceViewWidget = BUILTIN_DASHBOARD_WIDGET_KINDS.find(({ id }) => id === "resourceView")!.Component;
  const preset: ResourceViewPreset = { id: "example.records", label: "Records", resource: resource.modelLabel, view: "list" };
  const spec: WidgetSpec = { schemaVersion: 1, id: "records", kind: "resourceView", kindVersion: 1, title: "Records",
    data: { shape: "resourceView", preset: preset.id }, options: {}, x: 0, y: 0, w: 6, h: 4, isArchived: false };
  function HostedList(props: HostedResourceViewProps) {
    return <ListView {...props} resource={resource.modelLabel} columns={[{ field: "name" }]} />;
  }
  function Widget() {
    return <ResourceViewWidget spec={spec} data={useDashboardWidgetData(spec)} hostedView={HostedList} />;
  }
  const getList = vi.fn(async (_params: GetListParams) => ({ data: [], total: 0 }));
  render(<Provider resources={[resource]} dataProvider={{ getList }}>
    <AppRuntimeProvider runtime={{ resourceViews: { [preset.id]: preset } }}><ToastProvider><Widget /></ToastProvider></AppRuntimeProvider>
  </Provider>);
  await waitFor(() => expect(getList).toHaveBeenCalled());
  expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({ is_archived: { _eq: false } });
});
