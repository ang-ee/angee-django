// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider, type GetListParams } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";

import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { ResourceList, REFINE_CREATE_ID } from "./ResourceList";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";

const workflow = testDataResource("workflows.Workflow", {
  roots: { list: "workflows", detail: "workflows_by_pk", aggregate: "workflows_aggregate" },
  typeNames: { filter: "WorkflowBoolExp", order: "WorkflowOrderBy" },
  query: testResourceQuery({ fields: {
  id: testQueryField("id", { scalar: "ID", nullable: false }),
  name: testQueryField("name", { nullable: false, sort: { field: "name" } }),
  version: testQueryField("version", { scalar: "Int", nullable: false, sort: { field: "version" } }),
  status: testQueryField("status", { nullable: false }),
} }) });
const trigger = testDataResource("workflows.Trigger", {
  roots: { list: "triggers", detail: "triggers_by_pk", aggregate: "triggers_aggregate",
    create: "insert_triggers_one", update: "update_triggers_by_pk" },
  typeNames: { filter: "TriggerBoolExp", order: "TriggerOrderBy" },
  query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID", nullable: false }),
    kind: testQueryField("kind", { nullable: false }),
    enabled: testQueryField("enabled", { scalar: "Boolean", nullable: false }),
    workflow: testQueryField("workflow", { scalar: "ID", nullable: false,
      filter: { field: "workflow", scalar: "ID", values: [], operators: ["exact"] } }),
  } }),
  createFields: ["kind", "enabled", "workflow"], updateFields: ["kind", "enabled"],
});
const resources = [workflow, trigger];
const metadata = schemaFieldMetadataFromDataResources(resources);
const clients: QueryClient[] = [];

afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); });

test("a native controlled child list isolates queries and record UI from its parent collection", async () => {
  const getList = vi.fn(async (params: GetListParams) => ({
    data: [{ id: "trigger-1", kind: "Schedule", enabled: true, workflow: "workflow-1" }],
    total: 1,
  }));
  const getOne = vi.fn(async () => ({ data: {
    id: "trigger-1", kind: "Schedule", enabled: true, workflow: "workflow-1",
  } }));
  const provider = { getApiUrl: () => "test://resource-list-scope", getList, getOne,
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  const router = createRouter({
    routeTree: createRootRoute(),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  let parent!: ResourceViewContextValue;
  let selectedRecordId: string | undefined;

  function TriggerCollection() {
    const [recordId, setRecordId] = React.useState<string>();
    selectedRecordId = recordId;
    return <ResourceList resource={trigger.modelLabel} scope="local" placement="inline"
      baseFilter={{ workflow: { exact: "workflow-1" } }} createDefaults={{ workflow: "workflow-1" }}
      recordId={recordId} onSelect={(id) => setRecordId(id ?? REFINE_CREATE_ID)}
      onClose={() => setRecordId(undefined)}
      columns={[{ field: "kind", header: "Kind" }, { field: "enabled", header: "Enabled" }]}
      formFields={[{ name: "workflow", label: "Workflow", createOnly: true },
        { name: "kind", label: "Kind" }, { name: "enabled", label: "Enabled", widget: "switch" }]} />;
  }
  function Parent() { parent = useResourceView(); return <TriggerCollection />; }

  render(
    <RouterContextProvider router={router}><Refine resources={[...refineResourcesFromDataResources(resources)]}
      dataProvider={{ default: provider, console: provider }}
      options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
      <ModelMetadataProvider metadata={metadata}>
        <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ModalsHost><ToastProvider>
          <ResourceViewProvider resource={workflow.modelLabel} scope="local" initialState={{
            pageSize: 50, groupStack: [{ field: "status" }],
            sorting: [{ id: "name", desc: false }, { id: "version", desc: true }],
          }}><Parent /></ResourceViewProvider>
        </ToastProvider></ModalsHost></AppRuntimeProvider>
      </ModelMetadataProvider>
    </Refine></RouterContextProvider>,
  );

  expect(await screen.findByRole("button", { name: "Open Schedule" })).toBeTruthy();
  const childRequest = getList.mock.calls[0]?.[0];
  expect(childRequest?.meta?.gqlVariables).toMatchObject({ where: { workflow: { _eq: "workflow-1" } } });
  expect(childRequest?.meta?.gqlVariables?.order_by).toEqual({});
  expect(JSON.stringify(childRequest)).not.toContain("status");
  expect(JSON.stringify(childRequest)).not.toContain("version");
  expect(parent.state.groupStack).toEqual([{ field: "status" }]);
  expect(parent.state.sorting).toEqual([{ id: "name", desc: false }, { id: "version", desc: true }]);

  fireEvent.click(screen.getByRole("button", { name: /New Trigger/i }));
  await waitFor(() => expect(selectedRecordId).toBe(REFINE_CREATE_ID));
  expect(await screen.findByLabelText("Kind")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "List view" }));
  await waitFor(() => expect(selectedRecordId).toBeUndefined());

  fireEvent.click(screen.getByRole("button", { name: "Open Schedule" }));
  await waitFor(() => expect(selectedRecordId).toBe("trigger-1"));
  expect(await screen.findByDisplayValue("Schedule")).toBeTruthy();
  expect(getOne).toHaveBeenCalledWith(expect.objectContaining({ resource: "triggers", id: "trigger-1",
    meta: expect.objectContaining({ modelLabel: trigger.modelLabel }) }));
  fireEvent.click(screen.getByRole("button", { name: "List view" }));
  await waitFor(() => expect(selectedRecordId).toBeUndefined());

  expect(parent.state.pagination.pageSize).toBe(50);
  expect(parent.state.groupStack).toEqual([{ field: "status" }]);
  expect(parent.state.sorting).toEqual([{ id: "name", desc: false }, { id: "version", desc: true }]);
});
