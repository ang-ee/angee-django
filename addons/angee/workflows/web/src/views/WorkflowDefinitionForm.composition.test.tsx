// @vitest-environment happy-dom

import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets, type RecordPanelContext } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { beforeEach, expect, test, vi } from "vitest";

const state = vi.hoisted(() => ({ snapshot: null as Record<string, unknown> | null, save: vi.fn(), refetch: vi.fn() }));
vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: () => ({ data: { workflow_definition: state.snapshot }, isFetching: false, error: null, refetch: state.refetch }),
    useAuthoredMutation: (document: unknown) => [String(document).includes("Publish") ? vi.fn() : state.save, { fetching: false }],
  };
});
vi.mock("../documents.console", () => ({
  WorkflowDefinitionDocument: "WorkflowDefinition",
  SaveWorkflowDefinitionDocument: "SaveWorkflowDefinition",
  PublishWorkflowDefinitionDocument: "PublishWorkflowDefinition",
}));

import { WorkflowDefinitionForm } from "./WorkflowDefinitionForm";

const field = (name: string, scalar = "String") => ({
  name, kind: "scalar" as const, scalar, values: [], readable: true, filterable: true,
  sortable: true, aggregatable: false, groupable: false, creatable: true, updatable: true, requiredOnCreate: false,
});
const resource = testDataResource("workflows.Workflow", {
  modelName: "Workflow", typeNames: { node: "WorkflowType" },
  fields: [field("id", "ID"), field("name"), field("description"), field("status"), field("version", "Int"), field("lineage_id"), field("error_workflow", "ID"), field("max_steps", "Int"), field("budget", "JSON")],
});
function workflowSnapshot(revision: number, name: string, nodeName = "First") { return {
  revision,
  workflow: { id: "workflow_1", key: "flow", name, description: "", purpose: "AUTOMATION", subject_declaration: "", status: "DRAFT", version: 1, lineage_id: "workflow_1", error_workflow: null, max_steps: 10, budget: {}, current_published_version: null, publication_status: "unpublished" },
  nodes: [{ id: "node_1", key: "first", name: nodeName, step_class: "gate", config: {}, config_errors: {}, join_rule: "ALL_SUCCESS", is_entry: true, position: {} }], edges: [], readiness: [],
}; }
let surface: RecordPanelContext["form"] | null = null;
function SurfaceProbe({ context }: { context: RecordPanelContext }) { surface = context.form; return null; }
beforeEach(() => { cleanup(); surface = null; });

test("the registered form exposes parsed settings and saves through the definition command", async () => {
  state.snapshot = workflowSnapshot(4, "Original");
  state.save.mockReset();
  state.save.mockResolvedValue({ save_workflow_definition: { status: "SUCCESS", revision: 5, current_revision: 5, nodes: [], edges: [], diagnostics: [] } });
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
    <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider>
  </Refine>);
  const name = await screen.findByRole("textbox", { name: "Name" });
  expect(screen.getByText("Definition")).toBeTruthy();
  expect(screen.getByLabelText(/Max steps/i)).toBeTruthy();
  fireEvent.change(name, { target: { value: "Changed" } });
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  expect(state.save.mock.calls[0]?.[0]).toMatchObject({ expectedRevision: 4, edit: { workflow: { name: "Changed" } } });
});

test("the registered create branch uses the native resource create mutation", async () => {
  state.save.mockReset();
  const create = vi.fn(async ({ variables }: { variables: Record<string, unknown> }) => ({ data: { id: "workflow_new", ...variables } }));
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create, update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}><RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowDefinitionForm resource="workflows.Workflow" id={null} defaultValues={{ status: "DRAFT" }} /></AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider></Refine>);
  fireEvent.change(await screen.findByRole("textbox", { name: "Name" }), { target: { value: "Created workflow" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[0]).toMatchObject({ resource: "Workflows", variables: { name: "Created workflow" } });
  expect(create.mock.calls[0]?.[0].variables).toEqual({ name: "Created workflow" });
  expect(state.save).not.toHaveBeenCalled();
});

test("real Form keeps stale defaults until reviewed discard adopts the fetched definition", async () => {
  state.snapshot = workflowSnapshot(2, "Original", "First");
  state.save.mockReset();
  state.save.mockResolvedValue({ save_workflow_definition: { status: "STALE", revision: null, current_revision: 3, nodes: [], edges: [], diagnostics: [] } });
  state.refetch.mockReset();
  state.refetch.mockImplementation(async () => ({ data: { workflow_definition: workflowSnapshot(3, "Remote", "Remote node") } }));
  state.refetch.mockRejectedValueOnce(new Error("Offline"));
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}><RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} /></AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider></Refine>);
  fireEvent.change(await screen.findByRole("textbox", { name: "Name" }), { target: { value: "Local" } });
  surface!.form.setValue("definition.nodes.node_1.name", "Local node" as never, { shouldDirty: true });
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await screen.findByRole("button", { name: "Review changes" });
  expect(surface!.form.formState.defaultValues?.definition).toMatchObject({ revision: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  await screen.findByText("Offline");
  expect(surface!.form.getValues("name")).toBe("Local");
  expect(surface!.form.formState.defaultValues?.definition).toMatchObject({ revision: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  fireEvent.click(await screen.findByRole("button", { name: "Keep my edits" }));
  surface!.form.setValue("definition.nodes.node_1.name", "Later local node" as never, { shouldDirty: true });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2));
  expect((state.save.mock.calls[1]?.[0] as Record<string, unknown>).expectedRevision).toBe(2);
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  expect(await screen.findByText("Step Later local node · name")).toBeTruthy();
  fireEvent.click(await screen.findByRole("button", { name: "Discard my edits and reload" }));
  await waitFor(() => expect(surface!.form.getValues("name")).toBe("Remote"));
  expect(surface!.form.getValues("definition.nodes.node_1.name")).toBe("Remote node");
  expect(surface!.form.formState.defaultValues?.definition).toMatchObject({ revision: 3 });
  expect(surface!.formIsDirty).toBe(false);
});
