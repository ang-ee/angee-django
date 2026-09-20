// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { ModelMetadataProvider, ResourceQuery, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, IMPLEMENTATION_DETAIL_SLOT, ImplementationDetails, ModalsHost, ToastProvider, baseIcons, defaultWidgets } from "@angee/ui";

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({
    isFetching: false,
    data: { workflow_step_operations: [{
      key: "wait", label: "Wait", effect: "NONE", effect_description: "Pauses execution",
      idempotent: true, subject_declaration: "", outcomes: [{ key: "done", label: "Done" }],
      input_schema: { type: "object" }, output_schema: { type: "string" },
      input_contract: { root_node_id: 1, raw_schema: { type: "object" }, nodes: [{ id: 1, kind: "object", json_type: "object", title: "Input", description: null, nullable: false }], edges: [] },
      output_contract: { root_node_id: 1, raw_schema: { type: "string" }, nodes: [{ id: 1, kind: "scalar", json_type: "string", title: "Output", description: null, nullable: false }], edges: [] },
    }] },
  }),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiRouteTestDoubles, createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, createUiRouteTestDoubles());
});

import { WorkflowImplementationDetails } from "./WorkflowImplementationDetails";

const resource = testDataResource("workflows.Step", {
  rowModel: "client",
  roots: { aggregate: "steps_aggregate" },
  typeNames: { filter: "StepBoolExp", order: "StepOrderBy" },
  query: ResourceQuery.forRows({ fields: {
    id: { scalar: "ID" },
    name: { scalar: "String" },
    key: { scalar: "String" },
    step_class: { scalar: "String" },
    workflow: { kind: "relation", identityPath: "workflow.id", labelPath: "workflow.name" },
  } }).contract,
});

afterEach(cleanup);

describe("WorkflowImplementationDetails", () => {
  test("shows declared contracts and scopes configured usage to the implementation key", async () => {
    const workflow = { id: "flow-1", name: "Dispatch", version: 2, status: "PUBLISHED" };
    const rows = [
      { id: "step-wait", name: "Wait for dispatch", key: "wait-for-dispatch", step_class: "wait", workflow },
      { id: "step-call", name: "Send dispatch", key: "send-dispatch", step_class: "call", workflow },
    ];
    const provider = {
      getApiUrl: () => "test://workflows",
      getList: vi.fn(async () => ({ data: rows, total: rows.length })),
      getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
    } as DataProvider;
    const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory() });
    render(
      <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false, gcTime: 0 } } } } }}>
        <RouterContextProvider router={router}>
          <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
            <ModalsHost><ToastProvider>
              <AppRuntimeProvider runtime={{ widgets: defaultWidgets, icons: baseIcons, slots: [
                { slot: IMPLEMENTATION_DETAIL_SLOT, model: "workflows.Step", id: "usage", content: <WorkflowImplementationDetails /> },
              ] }}>
                <ImplementationDetails value={{ model: "workflows.Step", field: "step_class", choice: { key: "wait", category: "Flow", defaults: {}, config_schema: null } }} />
              </AppRuntimeProvider>
            </ToastProvider></ModalsHost>
          </ModelMetadataProvider>
        </RouterContextProvider>
      </Refine>,
    );

    expect(screen.getByRole("heading", { name: "Data contracts" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Workflow behavior" })).toBeTruthy();
    expect(screen.getByText("Pauses execution")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Dispatch 1", expanded: false }));
    expect((await screen.findByRole("link", { name: "Wait for dispatch" })).getAttribute("href")).toBe("/workflows.step/step-wait");
    expect(screen.getByRole("link", { name: "Dispatch · v2 · PUBLISHED" }).getAttribute("href")).toBe("/workflows.workflow/flow-1");
    expect(screen.queryByText("Send dispatch")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
