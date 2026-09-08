// @vitest-environment happy-dom

import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import * as React from "react";
import { testDataResource } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, Field, Form, ModalsHost, ToastProvider, defaultWidgets, type GraphViewGeometry, type RecordPanelContext } from "@angee/ui";
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
                key: "wait",
                label: "Wait until",
                category: "Control",
                defaults: { config: {} },
                config_schema: {
                  type: "object",
                  properties: { until: { type: "string", format: "date-time", label: "Until" } },
                  required: ["until"],
                },
                description: "Wait until a timestamp.",
                selectable: true,
                effect: "NONE",
                effect_description: "",
                idempotent: true,
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

import { WorkflowCanvas } from "./WorkflowCanvas";
import { graphWithOperation } from "./workflow-graph-authoring";
import { DefinitionHistoryProvider, useDefinitionHistory } from "./workflow-definition-history";
import { workflowNodeStyles, type WorkflowGraphNodeKind } from "./graph-data";

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
const edgeResource = testDataResource("workflows.Edge", {
  modelName: "Edge",
  typeNames: { node: "WorkflowEdgeType" },
  fields: [scalarField("id", "ID"), scalarField("source", "ID"), scalarField("target", "ID"), scalarField("condition")],
});

function CanvasHistoryHarness({ context }: { context: RecordPanelContext }): React.ReactElement {
  const surface = React.useRef<RecordPanelContext["form"] | null>(context.form);
  const history = useDefinitionHistory(surface, false);
  return <DefinitionHistoryProvider value={history}><button type="button" onClick={history.undo} disabled={!history.canUndo}>Undo</button><WorkflowCanvas context={context} /></DefinitionHistoryProvider>;
}

function renderCanvas(initial?: { nodes?: Record<string, Record<string, unknown>>; edges?: Record<string, Record<string, unknown>>; readiness?: Record<string, unknown>[]; settings?: boolean; history?: boolean }): void {
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
  const dataResources = [workflowResource, stepResource, edgeResource];
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
                      definition: { revision: 1, nodes: initialNodes, edges: initialEdges, readiness: initial?.readiness ?? [] },
                    },
                  }}
                  recordTabs={[{ id: "editor", label: "Editor", render: (context) => { canvasSurface = context.form; return initial?.history ? <CanvasHistoryHarness context={context} /> : <WorkflowCanvas context={context} />; }, keepMounted: true }]}
                  defaultRecordTab="editor"
                  overviewTab={{ label: "Settings", position: "last" }}
                >{initial?.settings ? <Field name="name" /> : null}</Form>
              </AppRuntimeProvider>
            </ToastProvider>
          </ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
  );
}

function testGeometry(nodes: Record<string, import("./workflow-definition-state").DefinitionNode>): GraphViewGeometry<WorkflowGraphNodeKind> {
  const size = workflowNodeStyles.HANDLER;
  const bounds = (identity: string) => {
    const position = nodes[identity]?.position as { x?: number; y?: number } | undefined;
    return typeof position?.x === "number" && typeof position.y === "number" ? { x: position.x, y: position.y, width: size.width, height: size.height } : undefined;
  };
  const intersects = (rect: { x: number; y: number; width: number; height: number }, exclude: ReadonlySet<string> = new Set<string>()) => Object.keys(nodes).some((identity) => {
    if (exclude.has(identity)) return false;
    const current = bounds(identity);
    return current ? current.x < rect.x + rect.width && current.x + current.width > rect.x && current.y < rect.y + rect.height && current.y + current.height > rect.y : false;
  });
  return {
    nodeBounds: bounds,
    nodeSize: () => ({ width: size.width, height: size.height }),
    layout: () => ({ rankdir: "TB", nodesep: 34, ranksep: 76, edgesep: 18, marginx: 24, marginy: 24 }),
    intersects,
    firstFreePosition: (_kind, preferred, _axis, exclude) => {
      for (let lane = 0; ; lane += 1) {
        const position = { x: preferred.x + lane * (size.width + 34), y: preferred.y };
        if (!intersects({ ...position, width: size.width, height: size.height }, exclude)) return position;
      }
    },
  };
}

describe("WorkflowCanvas native narrow inspector", () => {
  test("uses declared presentation and navigates saved issues to their exact fields", async () => {
    renderCanvas({ settings: true, nodes: {
      step_1: { ...mocks.record, position: { x: 0, y: 0 }, clientKey: undefined },
      step_2: { ...mocks.record, id: "step_2", key: "finish", name: "Finish", is_entry: false, position: { x: 300, y: 0 }, clientKey: undefined },
    }, edges: { edge_1: { id: "edge_1", source: "step_1", target: "step_2", condition: "", clientKey: undefined } }, readiness: [
      { code: "missing_mode", message: "Field required", kind: "NODE", id: "step_1", client_key: null, field: "config.mode" },
      { code: "invalid", message: "Field required", kind: "NODE", id: "step_1", client_key: null, field: "key" },
      { code: "invalid", message: "Field required", kind: "NODE", id: "step_1", client_key: null, field: "join_rule" },
      { code: "invalid", message: "Field required", kind: "EDGE", id: "edge_1", client_key: null, field: "condition" },
      { code: "missing_name", message: "Field required", kind: "WORKFLOW", id: "workflow_1", client_key: null, field: "name" },
    ] });
    await screen.findByText("Import files");
    expect(screen.getAllByText("Activity")).toHaveLength(2);
    expect(screen.getByText("Run callable · Start · 3 saved issues")).toBeTruthy();
    expect(screen.queryByText("ALL_SUCCESS")).toBeNull();
    expect(screen.queryByText("import-files")).toBeNull();
    expect(screen.getByTestId("rf__node-step_1").getAttribute("aria-label")).toContain("Start");
    fireEvent.click(screen.getByRole("button", { name: "5 saved issues" }));
    expect(screen.getByText("Unsaved edits are checked when you save.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Settings · Name: Field required" }));
    const name = await screen.findByRole("textbox", { name: "Name" });
    await waitFor(() => expect(document.activeElement).toBe(name));
    expect(screen.getByRole("tab", { name: "Settings" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
    fireEvent.click(screen.getByRole("button", { name: "5 saved issues" }));
    fireEvent.click(screen.getByRole("button", { name: "Import files · Mode: Field required" }));
    const mode = await screen.findByLabelText("Mode");
    await waitFor(() => expect(document.activeElement).toBe(mode));

    fireEvent.click(screen.getByRole("button", { name: "5 saved issues" }));
    fireEvent.click(screen.getByRole("button", { name: "Import files · Key: Field required" }));
    const key = await screen.findByLabelText("Key");
    await waitFor(() => expect(document.activeElement).toBe(key));
    expect(screen.getByRole("button", { name: "Advanced" }).getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "5 saved issues" }));
    fireEvent.click(screen.getByRole("button", { name: "Import files · Join Rule: Field required" }));
    const join = await screen.findByRole("combobox", { name: /Join rule/i });
    await waitFor(() => expect(document.activeElement).toBe(join));

    fireEvent.click(screen.getByRole("button", { name: "5 saved issues" }));
    fireEvent.click(screen.getByRole("button", { name: "Import files → Finish · Outcome: Field required" }));
    const outcome = await screen.findByLabelText("Outcome");
    await waitFor(() => expect(document.activeElement).toBe(outcome));
  });

  test("keeps an issue for an unavailable unsaved target without selecting another row", async () => {
    renderCanvas({ readiness: [{ code: "missing", message: "Repair removed step", kind: "NODE", id: null, client_key: "removed", field: "config.mode" }] });
    await screen.findByText("Import files");
    fireEvent.click(screen.getByRole("button", { name: "1 saved issue" }));
    const unavailableIssue = screen.getByRole("button", { name: "Unavailable step (removed) · Mode: Repair removed step" });
    fireEvent.click(unavailableIssue);
    expect(screen.queryByLabelText("Mode")).toBeNull();
    expect(screen.getByText("Select a step on the canvas.")).toBeTruthy();
  });

  test("insertion replaces one route with two and preserves the source outcome", () => {
    const node = { id: "", clientKey: "new", name: "Middle", key: "middle", step_class: "gate", config: {}, config_errors: {}, join_rule: "ALL_SUCCESS", is_entry: false, position: {} } as never;
    const result = graphWithOperation(node, { kind: "insert", identity: "route" }, {} as never, { route: { id: "edge_1", clientKey: undefined, source: "first", target: "last", condition: "completed" } } as never)!;
    expect(result.edges.route).toBeUndefined();
    expect(Object.values(result.edges)).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "first", target: "new", condition: "completed" }),
      expect.objectContaining({ source: "new", target: "last", condition: "" }),
    ]));
  });

  test("insertion moves only its downstream suffix and uses a free lane for a cycle", () => {
    const nodes = {
      source: { ...mocks.record, id: "source", position: { x: 0, y: 0 } },
      target: { ...mocks.record, id: "target", position: { x: 0, y: 100 } },
      downstream: { ...mocks.record, id: "downstream", position: { x: 0, y: 300 } },
      manual: { ...mocks.record, id: "manual", position: { x: 600, y: 40 } },
    } as unknown as Record<string, import("./workflow-definition-state").DefinitionNode>;
    const edges = {
      route: { id: "route", source: "source", target: "target", condition: "" },
      continuation: { id: "continuation", source: "target", target: "downstream", condition: "" },
    } as unknown as Record<string, import("./workflow-definition-state").DefinitionEdge>;
    const geometry = testGeometry(nodes);
    const inserted = { ...mocks.record, id: "", clientKey: "inserted", position: {} } as unknown as import("./workflow-definition-state").DefinitionNode;
    const result = graphWithOperation(inserted, { kind: "insert", identity: "route" }, nodes, edges, geometry)!;
    expect(result.nodes.source!.position).toEqual({ x: 0, y: 0 });
    expect((result.nodes.target!.position as { y: number }).y).toBeGreaterThan(100);
    expect((result.nodes.downstream!.position as { y: number }).y).toBeGreaterThan(300);
    expect(result.nodes.manual!.position).toEqual({ x: 600, y: 40 });

    const cycle = graphWithOperation(inserted, { kind: "insert", identity: "route" }, nodes, {
      ...edges,
      back: { id: "back", source: "target", target: "source", condition: "" } as never,
    }, geometry)!;
    expect(cycle.nodes.source!.position).toEqual({ x: 0, y: 0 });
    expect(cycle.nodes.target!.position).toEqual({ x: 0, y: 100 });
    expect((cycle.nodes.inserted!.position as { x: number }).x).not.toBe(0);
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
    expect(Object.values(canvasSurface!.form.getValues("definition.nodes") as unknown as Record<string, { join_rule: string }>).some((node) => node.join_rule === "ONE_SUCCESS")).toBe(true);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  test("places an addition below a legacy-positioned source without moving occupied or manual nodes", async () => {
    renderCanvas({ nodes: {
      step_1: { ...mocks.record, position: {}, clientKey: undefined },
      occupied: { ...mocks.record, id: "step_2", key: "occupied", name: "Occupied", is_entry: false, position: { x: 24, y: 176 }, clientKey: undefined },
      manual: { ...mocks.record, id: "step_3", key: "manual", name: "Manual", is_entry: false, position: { x: 620, y: 37 }, clientKey: undefined },
    } });
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));
    fireEvent.click(await screen.findByRole("button", { name: "Add after" }));
    const search = await screen.findByPlaceholderText("Search operations…");
    fireEvent.change(search, { target: { value: "Run callable" } });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    const values = canvasSurface!.form.getValues("definition.nodes") as unknown as Record<string, { id: string; position: { x?: number; y?: number } }>;
    const created = Object.values(values).find((node) => node.id === "")!;
    expect(created.position.y).toBeGreaterThan(0);
    expect(created.position.x).not.toBe(24);
    expect(values.step_1!.position).toEqual({ x: 24, y: 24 });
    expect(values.occupied!.position).toEqual({ x: 24, y: 176 });
    expect(values.manual!.position).toEqual({ x: 620, y: 37 });
  });

  test("materializes a legacy fallback before placing a duplicate without moving manual nodes", async () => {
    renderCanvas({ nodes: {
      step_1: { ...mocks.record, position: {}, clientKey: undefined },
      manual: { ...mocks.record, id: "step_3", key: "manual", name: "Manual", is_entry: false, position: { x: 700, y: 400 }, clientKey: undefined },
    } });
    await screen.findByText("Import files");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await screen.findByText("Import files copy");

    const values = canvasSurface!.form.getValues("definition.nodes") as unknown as Record<string, { id: string; name: string; position: { x?: number; y?: number } }>;
    const copy = Object.values(values).find((node) => node.name === "Import files copy")!;
    expect(typeof values.step_1!.position.x).toBe("number");
    expect(typeof values.step_1!.position.y).toBe("number");
    expect(copy.position.y).toBe(values.step_1!.position.y);
    expect(copy.position.x).not.toBe(values.step_1!.position.x);
    expect(values.manual!.position).toEqual({ x: 700, y: 400 });
  });

  test("inserts by shifting only the downstream suffix and undoes positions with the route", async () => {
    const originalNodes = {
      step_1: { ...mocks.record, position: { x: 20, y: 20 }, clientKey: undefined },
      target: { ...mocks.record, id: "step_2", key: "target", name: "Target", is_entry: false, position: { x: 20, y: 120 }, clientKey: undefined },
      downstream: { ...mocks.record, id: "step_3", key: "downstream", name: "Downstream", is_entry: false, position: { x: 20, y: 40 }, clientKey: undefined },
      manual: { ...mocks.record, id: "step_4", key: "manual", name: "Manual", is_entry: false, position: { x: 600, y: 45 }, clientKey: undefined },
    };
    const originalEdges = {
      route: { id: "edge_1", source: "step_1", target: "target", condition: "completed", clientKey: undefined },
      continuation: { id: "edge_2", source: "target", target: "downstream", condition: "", clientKey: undefined },
    };
    renderCanvas({ nodes: originalNodes, edges: originalEdges, history: true, readiness: [
      { code: "route", message: "Review route", kind: "EDGE", id: "edge_1", client_key: null, field: "condition" },
    ] });
    await screen.findByText("Target");
    fireEvent.click(screen.getByRole("button", { name: "1 saved issue" }));
    fireEvent.click(screen.getByRole("button", { name: "Import files → Target · Outcome: Review route" }));
    fireEvent.click(await screen.findByRole("button", { name: "Insert step" }));
    const search = await screen.findByPlaceholderText("Search operations…");
    fireEvent.change(search, { target: { value: "Run callable" } });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    const changed = canvasSurface!.form.getValues("definition.nodes") as unknown as Record<string, { id: string; position: { x: number; y: number } }>;
    const inserted = Object.values(changed).find((node) => node.id === "")!;
    expect(inserted.position.y).toBeGreaterThan(20);
    expect(inserted.position.x).not.toBe(20);
    expect(changed.target!.position).toEqual({ x: 20, y: 120 });
    expect(changed.downstream!.position).toEqual({ x: 20, y: 40 });
    expect(changed.manual!.position).toEqual({ x: 600, y: 45 });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(canvasSurface!.form.getValues("definition.nodes.target.position")).toEqual({ x: 20, y: 120 }));
    expect(canvasSurface!.form.getValues("definition.nodes.downstream.position")).toEqual({ x: 20, y: 40 });
    expect(canvasSurface!.form.getValues("definition.nodes.manual.position")).toEqual({ x: 600, y: 45 });
    expect(Object.values(canvasSurface!.form.getValues("definition.nodes") as unknown as Record<string, { id: string }>).some((node) => node.id === "")).toBe(false);
    expect(canvasSurface!.form.getValues("definition.edges")).toEqual(originalEdges);
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

  test("isolates bound config widgets when switching and adding the same operation", async () => {
    renderCanvas({ nodes: {
      step_1: { ...mocks.record, name: "First wait", step_class: "wait", config: { until: "2026-09-10T08:00" }, position: { x: 0, y: 0 }, clientKey: undefined },
      step_2: { ...mocks.record, id: "step_2", key: "second-wait", name: "Second wait", step_class: "wait", config: { until: "2026-09-12T09:30" }, is_entry: false, position: { x: 0, y: 180 }, clientKey: undefined },
    } });
    await screen.findByText("First wait");
    fireEvent.click(screen.getByTestId("rf__node-step_1"));
    expect((await screen.findByLabelText("Until") as HTMLInputElement).value).toContain("2026-09-10");

    fireEvent.click(screen.getByTestId("rf__node-step_2"));
    expect((await screen.findByLabelText("Until") as HTMLInputElement).value).toContain("2026-09-12");
    expect(canvasSurface!.form.getValues("definition.nodes.step_1.config.until")).toBe("2026-09-10T08:00");

    fireEvent.click(screen.getByRole("button", { name: "Add after" }));
    const search = await screen.findByPlaceholderText("Search operations…");
    fireEvent.change(search, { target: { value: "Wait until" } });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect((await screen.findByLabelText("Until") as HTMLInputElement).value).toBe("");
    const created = Object.values(canvasSurface!.form.getValues("definition.nodes") as unknown as Record<string, { id: string; config: Record<string, unknown> }>).find((node) => node.id === "");
    expect(created?.config).toEqual({});
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
