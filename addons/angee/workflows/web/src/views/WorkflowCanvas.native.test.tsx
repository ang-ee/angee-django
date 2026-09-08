// @vitest-environment happy-dom

import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, Form, ModalsHost, ToastProvider, defaultWidgets, type RecordPanelContext } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  workflowStatus: "DRAFT",
  record: {
    id: "step_1",
    name: "Import files",
    workflow: "workflow_1",
    key: "import-files",
    step_class: "workflows.steps.CallableStep",
    join_rule: "ALL_SUCCESS",
    is_entry: true,
    config: {},
    config_errors: {},
  } as Record<string, unknown>,
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeResourceSave: () => ({ save: vi.fn(), fetching: false, error: null, reset: vi.fn() }),
    useAuthoredQuery: (_document: unknown, variables?: unknown) => ({
      data: variables == null
        ? {
            workflow_step_operations: [
              {
                key: "workflows.steps.CallableStep",
                label: "Run callable",
                category: "Activity",
                defaults: { config: { mode: "safe" }, join_rule: "ONE_SUCCESS" },
                config_schema: {
                  type: "object",
                  properties: { mode: { type: "string", label: "Mode" } },
                },
                description: "Run the configured callable.",
                selectable: true,
                effect: "UNKNOWN",
                effect_description: "The callable declares no effect metadata.",
                idempotent: null,
                subject_declaration: "",
              },
              {
                key: "handler",
                label: "Handler",
                category: "Activity",
                defaults: {},
                config_schema: null,
                description: "Legacy handler.",
                selectable: false,
                effect: "UNKNOWN",
                effect_description: "",
                idempotent: null,
                subject_declaration: "",
              },
              {
                key: "gate",
                label: "Wait for approval",
                category: "Control",
                defaults: {},
                config_schema: {
                  type: "object",
                  properties: {
                    retry: {
                      type: "object",
                      label: "Retry",
                      nullable: true,
                      properties: { max_attempts: { type: "integer", label: "Max attempts" } },
                    },
                    slots: {
                      type: "array",
                      widget: "list",
                      label: "Slots",
                      items: { type: "string" },
                    },
                  },
                },
                description: "Wait for approval.",
                selectable: true,
                effect: "NONE",
                effect_description: "",
                idempotent: null,
                subject_declaration: "",
              },
            ],
          }
        : {
            workflows_by_pk: { id: "workflow_1", status: mocks.workflowStatus, version: 1 },
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

import { WorkflowCanvas, graphWithOperation } from "./WorkflowCanvas";

let canvasSurface: RecordPanelContext["form"] | null = null;

beforeAll(() => {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: ResizeObserverStub });
});

beforeEach(() => {
  cleanup();
  canvasSurface = null;
  mocks.workflowStatus = "DRAFT";
  mocks.record = {
    id: "step_1",
    name: "Import files",
    workflow: "workflow_1",
    key: "import-files",
    step_class: "workflows.steps.CallableStep",
    join_rule: "ALL_SUCCESS",
    is_entry: true,
    config: {},
    config_errors: {},
  };
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

const workflowResource = testDataResource("workflows.Workflow", {
  modelName: "Workflow",
  typeNames: { node: "WorkflowType" },
  fields: [scalarField("id", "ID"), scalarField("name")],
});
const stepResource = testDataResource("workflows.Step", {
  modelName: "Step",
  typeNames: { node: "WorkflowStepType" },
  fields: [
    scalarField("id", "ID"),
    scalarField("name"),
    scalarField("workflow", "ID"),
    scalarField("key"),
    scalarField("step_class", "String", ["workflows.steps.CallableStep", "handler", "gate"]),
    scalarField("join_rule", "String", ["ALL_SUCCESS", "ONE_SUCCESS"]),
    scalarField("is_entry", "Boolean"),
    scalarField("config", "JSON"),
  ],
});

function renderCanvas(initial?: { nodes?: Record<string, Record<string, unknown>>; edges?: Record<string, Record<string, unknown>> }): void {
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
  const dataResources = [workflowResource, stepResource];
  const node = { ...mocks.record, position: { x: 0, y: 0 }, clientKey: undefined };
  const initialNodes = initial?.nodes ?? { step_1: node };
  const initialEdges = initial?.edges ?? {};
  render(
    <Refine resources={[...refineResourcesFromDataResources(dataResources)]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(dataResources)}>
          <ModalsHost>
            <ToastProvider>
              <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
                <Form
                  resource="workflows.Workflow"
                  id="workflow_1"
                  readOnly={mocks.workflowStatus !== "DRAFT"}
                  acknowledgedSource={{
                    record: { id: "workflow_1", name: "Workflow" },
                    values: {
                      id: "workflow_1", name: "Workflow", status: mocks.workflowStatus, version: 1,
                      definition: { revision: 1, nodes: initialNodes, edges: initialEdges, readiness: [] },
                    },
                  }}
                  recordTabs={[{ id: "editor", label: "Editor", render: (context) => { canvasSurface = context.form; return <WorkflowCanvas context={context} />; }, keepMounted: true }]}
                  defaultRecordTab="editor"
                />
              </AppRuntimeProvider>
            </ToastProvider>
          </ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );
}

describe("WorkflowCanvas native narrow inspector", () => {
  test("insertion replaces one route with two and preserves the source outcome", () => {
    const node = { id: "", clientKey: "new", name: "Middle", key: "middle", step_class: "gate", config: {}, config_errors: {}, join_rule: "ALL_SUCCESS", is_entry: false, position: {} } as never;
    const result = graphWithOperation(node, { kind: "insert", identity: "route" }, {} as never, { route: { id: "edge_1", clientKey: undefined, source: "first", target: "last", condition: "completed" } } as never)!;
    expect(result.edges.route).toBeUndefined();
    expect(Object.values(result.edges)).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "first", target: "new", condition: "completed" }),
      expect.objectContaining({ source: "new", target: "last", condition: "" }),
    ]));
  });
  test("adds a declared operation after the selected step in the shared draft", async () => {
    renderCanvas();
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));
    fireEvent.click(await screen.findByRole("button", { name: "Add after" }));
    const search = await screen.findByPlaceholderText("Search operations…");
    fireEvent.change(search, { target: { value: "Run callable" } });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getAllByText("Run callable").length).toBeGreaterThan(0);
    expect((await screen.findByLabelText("Mode") as HTMLInputElement).value).toBe("safe");
    expect(screen.getAllByText("ONE_SUCCESS").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  test("connects two steps without a drag gesture and keeps the entry action explicit", async () => {
    renderCanvas();
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));
    expect(screen.queryByLabelText("Is entry")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(await screen.findByText("Import files copy")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Connect steps" }));
    const source = await screen.findByRole("combobox", { name: "Source" });
    expect(source.textContent).toContain("Import files copy");
    const target = screen.getByRole("combobox", { name: "Target" });
    fireEvent.click(target);
    fireEvent.click(screen.getByRole("option", { name: /^Import files$/ }));
    fireEvent.change(screen.getByLabelText("Outcome"), { target: { value: "completed" } });
    const connect = screen.getByRole("button", { name: /^Connect$/ });
    await waitFor(() => expect(connect.hasAttribute("disabled")).toBe(false));
    fireEvent.click(connect);

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(Object.values(canvasSurface!.form.getValues("definition.edges") ?? {})).toEqual([
      expect.objectContaining({ source: expect.stringMatching(/^node-/), target: "step_1", condition: "completed" }),
    ]);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  test("makes an entry explicit and deletes a node with all incident connections", async () => {
    renderCanvas({
      nodes: {
        step_1: { ...mocks.record, position: { x: 0, y: 0 }, clientKey: undefined },
        second: { id: "step_2", name: "Finish", key: "finish", step_class: "workflows.steps.CallableStep", join_rule: "ALL_SUCCESS", is_entry: false, config: {}, config_errors: {}, position: { x: 300, y: 0 } },
      },
      edges: { route: { id: "edge_1", source: "step_1", target: "second", condition: "completed", clientKey: undefined } },
    });
    await screen.findByText("Import files");
    await screen.findByText("Finish");
    fireEvent.click(screen.getByTestId("rf__node-second"));
    fireEvent.click(await screen.findByRole("button", { name: "Make entry" }));
    expect(canvasSurface!.form.getValues("definition.nodes.second.is_entry")).toBe(true);
    expect(canvasSurface!.form.getValues("definition.nodes.step_1.is_entry")).toBe(false);
    fireEvent.click(screen.getByTestId("rf__node-step_1"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete step" }));
    expect(canvasSurface!.form.getValues("definition.nodes.step_1")).toBeUndefined();
    expect(Object.values(canvasSurface!.form.getValues("definition.edges") ?? {}).every((edge) => (edge as { source: string; target: string }).source !== "step_1" && (edge as { source: string; target: string }).target !== "step_1")).toBe(true);
  });

  test("retains a typed inspector value across Back and reopen", async () => {
    renderCanvas();
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));

    const name = await screen.findByLabelText("Name");
    const operation = screen.getByLabelText("Operation");
    expect(screen.getByText(/Effect: depends on the operation/)).toBeTruthy();
    expect(screen.getByLabelText("Mode")).toBeTruthy();
    fireEvent.click(operation);
    expect(screen.queryByRole("option", { name: "Handler" })).toBeNull();
    fireEvent.keyDown(operation, { key: "Escape" });
    fireEvent.change(name, { target: { value: "Import every file" } });
    expect((name as HTMLInputElement).value).toBe("Import every file");

    fireEvent.click(screen.getByRole("button", { name: "Back to canvas" }));
    await waitFor(() => expect(screen.getByTestId("inspector").firstElementChild?.className).toContain("hidden"));
    fireEvent.click(screen.getByTestId("rf__node-step_1"));

    expect((await screen.findByLabelText("Name") as HTMLInputElement).value).toBe("Import every file");
  });

  test("keeps only the persisted nonselectable operation readable", async () => {
    mocks.record.step_class = "HANDLER";
    renderCanvas();
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));

    const operation = await screen.findByLabelText("Operation");
    expect(operation.textContent).toContain("Handler");
    expect(screen.getByText(/Legacy handler/)).toBeTruthy();
    expect(screen.queryByText(/Operation details are unavailable/)).toBeNull();
    fireEvent.click(operation);
    expect(screen.getByRole("option", { name: "Handler" }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("option", { name: "Run callable" })).toBeTruthy();
  });

  test("keeps an uppercase persisted operation clean when its canonical choice is reopened", async () => {
    mocks.record.step_class = "GATE";
    mocks.record.config = { retry: null, slots: [] };
    renderCanvas();
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));

    const operation = await screen.findByRole("combobox", { name: "Operation" });
    expect(operation.textContent).toContain("Wait for approval");
    expect(screen.getByText("Slots")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getByText("Retry")).toBeTruthy();
    expect(screen.queryByLabelText("Mode")).toBeNull();
    expect(screen.queryByText("Advanced configuration")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    fireEvent.click(operation);
    const currentOperation = screen.getByRole("option", { name: "Wait for approval" });
    fireEvent.pointerDown(currentOperation);
    fireEvent.pointerUp(currentOperation);
    fireEvent.click(currentOperation);
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
    expect(screen.getByText("Retry")).toBeTruthy();
    expect(screen.getByText("Slots")).toBeTruthy();
    expect(screen.queryByText("Advanced configuration")).toBeNull();

  });

  test("keeps representable incomplete config in typed fields", async () => {
    mocks.record.config = { mode: 42 };
    mocks.record.config_errors = { "config.mode": ["Input should be a valid string"] };
    renderCanvas();
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));

    expect(await screen.findByLabelText("Mode")).toBeTruthy();
    expect(screen.queryByText("Advanced configuration")).toBeNull();
  });

  test("renders a published JSX inspector without edit affordances", async () => {
    mocks.workflowStatus = "PUBLISHED";
    mocks.record.step_class = "workflows.steps.CallableStep";
    mocks.record.config = { mode: "safe" };
    renderCanvas();
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));

    expect(await screen.findByText("Version 1 · Read-only")).toBeTruthy();
    expect(screen.getAllByText("Import files").length).toBeGreaterThan(0);
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Operation" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Set value|Leave empty|Add item|Save/ })).toBeNull();
  });
});
