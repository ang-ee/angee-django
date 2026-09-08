// @vitest-environment happy-dom

import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { beforeAll, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  record: {
    id: "step_1",
    name: "Import files",
    workflow: "workflow_1",
    key: "import-files",
    step_class: "workflows.steps.CallableStep",
    join_rule: "ALL_SUCCESS",
    is_entry: true,
    config: {},
  } as Record<string, unknown>,
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeResourceSave: () => ({ save: vi.fn(), fetching: false, error: null, reset: vi.fn() }),
    useAuthoredQuery: () => ({
      data: {
        workflows_by_pk: { id: "workflow_1", status: "DRAFT" },
        workflow_steps: [{ ...mocks.record, position: { x: 0, y: 0 } }],
        workflow_edges: [],
      },
      isFetching: false,
      error: null,
    }),
    useAuthoredMutation: () => [vi.fn(async () => undefined), { fetching: false, error: null }],
  };
});

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useInvalidate: () => vi.fn(async () => undefined),
    useOne: () => ({ result: mocks.record, query: { isFetching: false, error: null, refetch: vi.fn() } }),
    useList: () => ({ result: { data: [], total: 0 }, query: { isFetching: false, error: null, refetch: vi.fn() } }),
    useUpdate: () => ({ mutateAsync: vi.fn(), mutation: { isPending: false, error: null } }),
  };
});

import { WorkflowCanvas } from "./WorkflowCanvas";

beforeAll(() => {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: ResizeObserverStub });
});

const scalarField = (name: string, scalar = "String", values: readonly string[] = []) => ({
  name,
  kind: "scalar" as const,
  scalar,
  values: values.map((value) => ({ value })),
  readable: true,
  filterable: true,
  sortable: true,
  aggregatable: false,
  groupable: false,
  creatable: true,
  updatable: true,
  requiredOnCreate: false,
});

const stepResource = testDataResource("workflows.Step", {
  modelName: "Step",
  typeNames: { node: "WorkflowStepType" },
  fields: [
    scalarField("id", "ID"),
    scalarField("name"),
    scalarField("workflow", "ID"),
    scalarField("key"),
    scalarField("step_class", "String", ["workflows.steps.CallableStep"]),
    scalarField("join_rule", "String", ["ALL_SUCCESS", "ANY_SUCCESS"]),
    scalarField("is_entry", "Boolean"),
    scalarField("config", "JSON"),
  ],
});

function renderCanvas(): void {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => null });
  const router = createRouter({ routeTree: rootRoute.addChildren([indexRoute]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const provider = {
    getApiUrl: () => "test://workflows",
    getOne: vi.fn(async () => ({ data: mocks.record })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(async () => ({ data: mocks.record })),
    update: vi.fn(async () => ({ data: mocks.record })),
    deleteOne: vi.fn(async () => ({ data: mocks.record })),
  } as DataProvider;
  render(
    <Refine resources={[...refineResourcesFromDataResources([stepResource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([stepResource])}>
          <ModalsHost>
            <ToastProvider>
              <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
                <WorkflowCanvas workflowId="workflow_1" />
              </AppRuntimeProvider>
            </ToastProvider>
          </ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );
}

describe("WorkflowCanvas native narrow inspector", () => {
  test("retains a typed inspector value across Back and reopen", async () => {
    renderCanvas();
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));

    const name = await screen.findByLabelText("Name");
    fireEvent.change(name, { target: { value: "Import every file" } });
    expect((name as HTMLInputElement).value).toBe("Import every file");

    fireEvent.click(screen.getByRole("button", { name: "Back to canvas" }));
    await waitFor(() => expect(screen.getByTestId("inspector").firstElementChild?.className).toContain("hidden"));
    fireEvent.click(screen.getByTestId("rf__node-step_1"));

    expect((await screen.findByLabelText("Name") as HTMLInputElement).value).toBe("Import every file");
  });
});
