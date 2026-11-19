// @vitest-environment happy-dom

import { ResourceQuery, type QueryFilter } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { GetListParams } from "@refinedev/core";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { ListView } from "./ListView";
import { ResourceViewProvider } from "./resource-view-context";
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

function collection(options: {
  archivable?: boolean;
  embedded?: boolean;
  baseFilter?: QueryFilter;
  filter?: QueryFilter;
  preset?: ResourceViewPreset;
} = {}) {
  const metadata = options.archivable === false
    ? { ...resource, fields: resource.fields?.map((field) => ({ ...field, archivable: false })) }
    : resource;
  const getList = vi.fn(async (_params: GetListParams) => ({
    data: [{ id: "record-1", name: "One", is_archived: false }], total: 1,
  }));
  render(<Provider resources={[metadata]} dataProvider={{ getList }}>
    <AppRuntimeProvider runtime={{ resourceViews: options.preset ? { [options.preset.id]: options.preset } : {} }}>
      <ToastProvider>
        <ResourceViewProvider resource={resource.modelLabel} scope="local"
          presetIds={options.preset ? [options.preset.id] : []}
          initialState={{ filter: options.filter, preset: options.preset?.id }}>
          <ListView resource={resource.modelLabel} columns={[{ field: "name" }]}
            scope="inherit" presentation={options.embedded ? "embedded" : "page"}
            baseFilter={options.baseFilter} />
        </ResourceViewProvider>
      </ToastProvider>
    </AppRuntimeProvider>
  </Provider>);
  return getList;
}

test("a default archive-aware collection requests unarchived rows and selects their flag", async () => {
  const getList = collection();
  await waitFor(() => expect(getList).toHaveBeenCalled());
  for (const [request] of getList.mock.calls) {
    expect(request.meta?.gqlVariables?.where).toEqual({ is_archived: { _eq: false } });
    expect(request.meta?.fields).toContain("is_archived");
  }
});

test.each(["base", "editable", "preset"] as const)("an explicit %s filter can request archived rows", async (source) => {
  const filter = { OR: [{ is_archived: { exact: true } }, { name: { exact: "One" } }] };
  const getList = collection(source === "base" ? { baseFilter: filter }
    : source === "editable" ? { filter }
    : { preset: { id: "example.archived", preset: "example.archived", label: "Archived", resource: resource.modelLabel,
      view: "list", fixedFilter: filter } });
  await waitFor(() => expect(getList).toHaveBeenCalled());
  for (const [request] of getList.mock.calls) {
    expect(request.meta?.gqlVariables?.where).toEqual(ResourceQuery.from(resource).toWhere(filter));
  }
});

test.each(["ordinary", "embedded"] as const)("an %s collection retains its original scope", async (kind) => {
  const filter = { name: { exact: "One" } };
  const getList = collection({ archivable: kind !== "ordinary", embedded: kind === "embedded", baseFilter: filter });
  await waitFor(() => expect(getList).toHaveBeenCalled());
  for (const [request] of getList.mock.calls) {
    expect(request.meta?.gqlVariables?.where).toEqual({ name: { _eq: "One" } });
  }
});
