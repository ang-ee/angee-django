// @vitest-environment happy-dom

import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, ModalsHost, ToastProvider, createRouteHref, defaultWidgets, type RecordPanelContext } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/react-router")>(),
  useSearch: () => ({}),
}));

const state = vi.hoisted(() => ({ snapshot: null as Record<string, unknown> | null, save: vi.fn(), publish: vi.fn(), test: vi.fn(), refetch: vi.fn(), planQueries: 0 }));
vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: (document: unknown, _variables?: unknown, options?: { enabled?: boolean }) => {
      const name = String(document);
      if (name.includes("TestPlan") && options?.enabled === false) return { data: undefined, isFetching: false, error: null, refetch: state.refetch };
      if (name.includes("TestPlan")) state.planQueries += 1;
      const data = name.includes("Operations") ? { workflow_step_operations: [] }
        : name.includes("TestPlan") ? { workflow_test_plan: { operations: [], required_fixtures: [], diagnostics: [], freshness: [] } }
        : name.includes("RepairContext") ? { workflow_test_repair_context: null }
        : name.includes("FixtureSources") ? { workflow_test_fixture_sources: { items: [], next_after: null } }
        : name.includes("FixtureSource") ? { workflow_test_fixture_source: null }
        : { workflow_definition: state.snapshot };
      return { data, isFetching: false, error: null, refetch: state.refetch };
    },
    useAuthoredMutation: (document: unknown) => [String(document).includes("Publish") ? state.publish : String(document).includes("Test") ? state.test : state.save, { fetching: false }],
  };
});
vi.mock("../documents.console", () => ({
  WorkflowDefinitionDocument: "WorkflowDefinition",
  SaveWorkflowDefinitionDocument: "SaveWorkflowDefinition",
  PublishWorkflowDefinitionDocument: "PublishWorkflowDefinition",
  TestWorkflowDefinitionDocument: "TestWorkflowDefinition",
  WorkflowStepOperationsDocument: "WorkflowStepOperations",
  WorkflowTestRepairContextDocument: "WorkflowTestRepairContext",
  WorkflowTestPlanDocument: "WorkflowTestPlan",
  WorkflowTestFixtureSourcesDocument: "WorkflowTestFixtureSources",
  WorkflowTestFixtureSourceDocument: "WorkflowTestFixtureSource",
  WorkflowDefinitionComparisonDocument: "WorkflowDefinitionComparison",
  WorkflowLaunchDocument: "WorkflowLaunch",
  RestoreWorkflowDefinitionDocument: "RestoreWorkflowDefinition",
}));

import { WorkflowDefinitionForm } from "./WorkflowDefinitionForm";
import { useDefinitionHistoryContext } from "./workflow-definition-history";

const field = (name: string, scalar = "String") => ({
  name, kind: "scalar" as const, scalar, values: [], readable: true, filterable: true,
  sortable: true, aggregatable: false, groupable: false, creatable: true, updatable: true, requiredOnCreate: false,
});
const resource = testDataResource("workflows.Workflow", {
  modelName: "Workflow", typeNames: { node: "WorkflowType" },
  fields: [field("id", "ID"), field("name"), field("description"), field("status"), field("version", "Int"), field("lineage_id"), field("subject_declaration"), field("error_workflow", "ID"), field("max_steps", "Int"), field("budget", "JSON")],
});
const subjectResource = testDataResource("notes.Note", {
  modelName: "note", typeNames: { node: "NoteType" }, recordRepresentation: "title",
  roots: { detail: "notes_by_pk" }, fields: [field("id", "ID"), field("title")],
});
const folderResource = testDataResource("documents.Folder", { modelName: "folder", typeNames: { node: "DocumentFolderType" }, recordRepresentation: "name", roots: { detail: "document_folder_by_pk" } });
const storageFolderResource = testDataResource("storage.Folder", { modelName: "folder", typeNames: { node: "StorageFolderType" }, recordRepresentation: "name", roots: { detail: "storage_folder_by_pk" } });
const resources = [resource, subjectResource, folderResource, storageFolderResource];
function workflowSnapshot(revision: number, name: string, nodeName = "First") {
  return {
    revision,
    workflow: { id: "workflow_1", key: "flow", name, description: "", purpose: "AUTOMATION", subject_declaration: "", status: "DRAFT", version: 1, lineage_id: "workflow_1", error_workflow: null, max_steps: 10, budget: {}, current_published_version: null, publication_status: "unpublished" },
    nodes: [{ id: "node_1", key: "first", name: nodeName, step_class: "gate", config: {}, config_errors: {}, input_binding: null, join_rule: "ALL_SUCCESS", is_entry: true, position: {} }], edges: [], readiness: [],
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const complexBinding = { kind: "object", fields: { "a.b[]/kind": { kind: "array", items: [{ kind: "constant", value: null }] } } };
const laterBinding = { kind: "object", fields: { "a.b[]/kind": { kind: "array", items: [{ kind: "constant", value: null }, { kind: "constant", value: "later" }] } } };
let surface: RecordPanelContext["form"] | null = null;
function SurfaceProbe({ context }: { context: RecordPanelContext; }) {
  surface = context.form;
  const history = useDefinitionHistoryContext();
  return <><button type="button" onClick={() => history?.perform(() => context.form.form.setValue("definition.nodes.node_1.input_binding", complexBinding as never, { shouldDirty: true }))}>Set complex binding</button><button type="button" onClick={() => history?.perform(() => context.form.form.setValue("definition.nodes.node_1.input_binding", laterBinding as never, { shouldDirty: true }))}>Set later binding</button><button type="button" onClick={() => history?.perform(() => {
    const nodes = context.form.form.getValues("definition.nodes") as unknown as Record<string, unknown>;
    const edges = context.form.form.getValues("definition.edges") as unknown as Record<string, { source: string; target: string; }>;
    const { node_1: _removed, ...remaining } = nodes;
    context.form.form.setValue("definition.nodes", remaining as never, { shouldDirty: true });
    context.form.form.setValue("definition.edges", Object.fromEntries(Object.entries(edges).filter(([, edge]) => edge.source !== "node_1" && edge.target !== "node_1")) as never, { shouldDirty: true });
  })}>Delete fixture node</button></>;
}
beforeEach(() => { cleanup(); surface = null; state.publish.mockReset(); state.test.mockReset(); state.refetch.mockReset(); });

test("test launch shows business failures and reserves retries only for ambiguous transport", async () => {
  state.snapshot = workflowSnapshot(4, "Original");
  state.test
    .mockResolvedValueOnce({ start_workflow_test: { ok: false, message: "Test workflow failed.", validation_errors: { __all__: ["Resolve the saved graph issue."], subject: ["Choose an accessible record."] } } })
    .mockResolvedValueOnce({})
    .mockResolvedValueOnce({ start_workflow_test: { ok: false, message: "Still unavailable" } });
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
    <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" />
    </AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider>
  </Refine>);

  fireEvent.click(await screen.findByRole("button", { name: "Test…" }));
  const start = await screen.findByRole("button", { name: "Start test" });
  await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(start);
  expect(await screen.findByText("Test workflow failed. Resolve the saved graph issue. Choose an accessible record.")).toBeTruthy();
  const firstKey = state.test.mock.calls[0]?.[0]?.requestKey;

  fireEvent.click(start);
  expect(await screen.findByText("Workflow test failed.")).toBeTruthy();
  const secondKey = state.test.mock.calls[1]?.[0]?.requestKey;
  expect(secondKey).not.toBe(firstKey);
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Test saved workflow" })).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  const retry = await screen.findByRole("button", { name: "Retry test request" });
  expect(screen.getByText("Not set")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Set" })).toBeNull();
  fireEvent.click(retry);
  expect(await screen.findByText("Still unavailable")).toBeTruthy();
  expect(state.test.mock.calls[2]?.[0]?.requestKey).toBe(secondKey);
  expect(state.test.mock.calls[2]?.[0]).toEqual(state.test.mock.calls[1]?.[0]);
});

test("Save and test launches the acknowledged revision after reconciliation and keeps later edits", async () => {
  state.snapshot = workflowSnapshot(4, "Original");
  const save = deferred<Record<string, unknown>>();
  state.save.mockReset().mockReturnValueOnce(save.promise).mockResolvedValueOnce({
    save_workflow_definition: { status: "INVALID", revision: null, current_revision: 5, nodes: [], edges: [], diagnostics: [] },
  });
  state.test.mockRejectedValueOnce(new Error("Delivery unknown")).mockResolvedValueOnce({ start_workflow_test: { ok: true, message: "Started", id: "run_1" } });
  const onSaved = vi.fn();
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
    <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets, routeHref: createRouteHref([{ name: "workflows.run", path: "/runs/$id" }]) }}>
      <WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" onSaved={onSaved} defaultRecordTab="editor" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider>
  </Refine>);
  fireEvent.click(await screen.findByRole("button", { name: "Set complex binding" }));
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  const start = await screen.findByRole("button", { name: "Save and test" });
  await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(start);
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  expect(state.test).not.toHaveBeenCalled();
  surface!.form.setValue("definition.nodes.node_1.input_binding", laterBinding as never, { shouldDirty: true });
  save.resolve({ save_workflow_definition: { status: "SUCCESS", revision: 5, current_revision: 5, nodes: [], edges: [], diagnostics: [] } });
  await waitFor(() => expect(state.test).toHaveBeenCalledTimes(1));
  expect(state.test.mock.calls[0]?.[0]).toMatchObject({ expectedRevision: 5 });
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toEqual(laterBinding);
  expect(await screen.findByText("Delivery unknown")).toBeTruthy();
  expect(screen.getByText(/saved revision 5/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  const retry = await screen.findByRole("button", { name: "Retry test request" });
  fireEvent.click(retry);
  await waitFor(() => expect(state.test).toHaveBeenCalledTimes(2));
  expect(state.test.mock.calls[1]?.[0]).toEqual(state.test.mock.calls[0]?.[0]);
  expect(await screen.findByText(/Test started for saved revision 5/)).toBeTruthy();
  expect(onSaved).toHaveBeenCalledTimes(1);
});

test("a failed prelaunch Save releases its test request and blocks a second test during Save", async () => {
  state.snapshot = workflowSnapshot(4, "Original");
  const firstSave = deferred<Record<string, unknown>>();
  state.save.mockReset().mockReturnValueOnce(firstSave.promise).mockResolvedValueOnce({
    save_workflow_definition: { status: "SUCCESS", revision: 5, current_revision: 5, nodes: [], edges: [], diagnostics: [] },
  });
  state.test.mockResolvedValue({ start_workflow_test: { ok: true, message: "Started", id: "run-2" } });
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
    <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" defaultRecordTab="editor" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider>
  </Refine>);
  fireEvent.click(await screen.findByRole("button", { name: "Set complex binding" }));
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save and test" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  expect((screen.getByRole("button", { name: "Test…", hidden: true }) as HTMLButtonElement).disabled).toBe(true);
  firstSave.reject(new Error("Save delivery failed"));
  expect(await screen.findByText("Save delivery failed")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save and test" }));
  await waitFor(() => expect(state.test).toHaveBeenCalledTimes(1));
  expect(state.save).toHaveBeenCalledTimes(2);
});

test("the registered form exposes parsed settings and saves through the definition command", async () => {
  state.snapshot = workflowSnapshot(4, "Original");
  (state.snapshot.nodes as Record<string, unknown>[])[0]!.config_errors = { "config.mode": ["Kept server error"] };
  state.snapshot.readiness = [{ code: "kept", message: "Kept readiness", kind: "NODE", id: "node_1", client_key: null, requested_id: null, field: "config" }];
  state.save.mockReset();
  state.save.mockResolvedValue({ save_workflow_definition: { status: "SUCCESS", revision: 5, current_revision: 5, nodes: [], edges: [], diagnostics: [] } });
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
    <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider>
  </Refine>);
  const name = await screen.findByRole("textbox", { name: "Name" });
  expect(screen.getByText("Definition")).toBeTruthy();
  expect(screen.getByLabelText(/Max steps/i)).toBeTruthy();
  expect(screen.getByText("Resolve 1 saved issue before publishing.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Publish" }).hasAttribute("disabled")).toBe(true);
  fireEvent.change(name, { target: { value: "Changed" } });
  fireEvent.blur(name);
  fireEvent.click(screen.getByRole("tab", { name: "Editor" }));
  fireEvent.click(screen.getByRole("button", { name: "Set complex binding" }));
  const complex = complexBinding;
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toEqual(complex);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toEqual(complex);
  fireEvent.click(screen.getByRole("button", { name: "Discard" }));
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toBeNull();
  fireEvent.change(name, { target: { value: "Changed" } });
  fireEvent.blur(name);
  fireEvent.click(screen.getByRole("button", { name: "Set complex binding" }));
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect((name as HTMLInputElement).value).toBe("Original");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).toBeNull());
  expect(surface!.form.getValues("definition.revision")).toBe(4);
  expect(surface!.form.getValues("definition.readiness")).toEqual(expect.arrayContaining([expect.objectContaining({ code: "kept" })]));
  expect(surface!.form.getValues("definition.nodes.node_1.config_errors")).toEqual({ "config.mode": ["Kept server error"] });
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  expect((name as HTMLInputElement).value).toBe("Changed");
  expect(await screen.findByRole("button", { name: "Save" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Discard" }));
  expect((name as HTMLInputElement).value).toBe("Original");
  expect(screen.getByRole("button", { name: "Undo" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect((name as HTMLInputElement).value).toBe("Original");
  fireEvent.change(name, { target: { value: "Changed" } });
  fireEvent.blur(name);
  const maxSteps = screen.getByLabelText(/Max steps/i) as HTMLInputElement;
  fireEvent.input(maxSteps, { target: { value: "" } });
  expect(maxSteps.value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Set complex binding" }));
  state.save.mockResolvedValueOnce({ save_workflow_definition: {
    status: "STRUCTURAL", revision: null, current_revision: 4, nodes: [], edges: [],
    diagnostics: [{ kind: "WORKFLOW", field: "max_steps", code: "field_invalid", message: "This field cannot be null." }],
  } });
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  expect(state.save.mock.calls[0]?.[0]).toMatchObject({ expectedRevision: 4, edit: { workflow: { name: "Changed", max_steps: null } } });
  expect(state.save.mock.calls[0]?.[0]).toMatchObject({ edit: { node_patches: [{ id: "node_1", fields: { input_binding: complex } }] } });
  expect(await screen.findByText("This field cannot be null.")).toBeTruthy();
});

test("a save acknowledgement rebases a later complex binding edit without losing history", async () => {
  state.snapshot = workflowSnapshot(4, "Original");
  const firstSave = deferred<unknown>();
  state.save.mockReset();
  state.save
    .mockImplementationOnce(() => firstSave.promise)
    .mockResolvedValueOnce({ save_workflow_definition: { status: "SUCCESS", revision: 6, current_revision: 6, nodes: [], edges: [], diagnostics: [] } });
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}>
    <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" defaultRecordTab="editor" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} />
    </AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider>
  </Refine>);

  await screen.findByRole("button", { name: "Set complex binding" });
  fireEvent.click(screen.getByRole("button", { name: "Set complex binding" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  expect(state.save.mock.calls[0]?.[0]).toMatchObject({ expectedRevision: 4, edit: { node_patches: [{ id: "node_1", fields: { input_binding: complexBinding } }] } });
  fireEvent.click(screen.getByRole("button", { name: "Set later binding" }));
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toEqual(laterBinding);

  firstSave.resolve({ save_workflow_definition: { status: "SUCCESS", revision: 5, current_revision: 5, nodes: [], edges: [], diagnostics: [] } });
  await waitFor(() => expect(surface!.form.getValues("definition.revision")).toBe(5));
  expect((surface!.form.formState.defaultValues?.definition as { nodes?: { node_1?: { input_binding?: unknown; }; }; } | undefined)?.nodes?.node_1?.input_binding).toEqual(complexBinding);
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toEqual(laterBinding);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toEqual(complexBinding);
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  expect(surface!.form.getValues("definition.nodes.node_1.input_binding")).toEqual(laterBinding);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2));
  expect(state.save.mock.calls[1]?.[0]).toMatchObject({ expectedRevision: 5, edit: { node_patches: [{ id: "node_1", fields: { input_binding: laterBinding } }] } });
});

test("the registered create branch uses the native resource create mutation", async () => {
  state.save.mockReset();
  const create = vi.fn(async ({ variables }: { variables: Record<string, unknown>; }) => ({ data: { id: "workflow_new", ...variables } }));
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create, update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}><RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowDefinitionForm resource="workflows.Workflow" id={null} defaultValues={{ status: "DRAFT" }} /></AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider></Refine>);
  fireEvent.change(await screen.findByRole("textbox", { name: "Name" }), { target: { value: "Created workflow" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[0]).toMatchObject({ resource: "Workflows", variables: { name: "Created workflow" } });
  expect(create.mock.calls[0]?.[0].variables).toEqual({ name: "Created workflow" });
  expect(state.save).not.toHaveBeenCalled();
});

test("a saved subject type reopens the Test setup with the native record picker", async () => {
  state.planQueries = 0;
  state.snapshot = workflowSnapshot(1, "Subject workflow");
  state.save.mockResolvedValue({ save_workflow_definition: {
    status: "SUCCESS", revision: 2, current_revision: 2, nodes: [], edges: [], diagnostics: [],
  } });
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const registered = refineResourcesFromDataResources([resource, subjectResource]).map((entry) => entry.meta.modelLabel === "notes.Note" ? { ...entry, meta: { ...entry.meta, label: "Notes" } } : entry);
  render(<Refine resources={[...registered]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}><RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" /></AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider></Refine>);

  fireEvent.click(await screen.findByRole("combobox", { name: "Works with" }));
  const noteOption = await screen.findByRole("option", { name: "Notes" });
  fireEvent.pointerDown(noteOption, { pointerType: "mouse" });
  fireEvent.click(noteOption);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(expect.objectContaining({
    expectedRevision: 1,
    edit: expect.objectContaining({ workflow: { subject_declaration: "notes.note" } }),
  })));
  fireEvent.click(screen.getByRole("button", { name: "Test…" }));
  expect(await screen.findByLabelText("Record")).toBeTruthy();
  expect(screen.getByText("Choose a record to prepare the test.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Start test" }).hasAttribute("disabled")).toBe(true);
  expect(state.planQueries).toBe(0);
});

test("subject types use canonical model labels and qualify duplicate names by app", async () => {
  state.snapshot = workflowSnapshot(1, "Subject labels");
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}><RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" /></AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider></Refine>);
  fireEvent.click(await screen.findByRole("combobox", { name: "Works with" }));
  expect(screen.getByRole("option", { name: "Note" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "Folder · documents" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "Folder · storage" })).toBeTruthy();
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
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}><RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" defaultRecordTab="editor" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} /></AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider></Refine>);
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
  expect(await screen.findByText("Step Later local node · Name")).toBeTruthy();
  fireEvent.click(await screen.findByRole("button", { name: "Discard my edits and reload" }));
  await waitFor(() => expect(surface!.form.getValues("name")).toBe("Remote"));
  expect(surface!.form.getValues("definition.nodes.node_1.name")).toBe("Remote node");
  expect(surface!.form.formState.defaultValues?.definition).toMatchObject({ revision: 3 });
  expect(surface!.formIsDirty).toBe(false);
  expect(screen.getByRole("button", { name: "Undo" }).hasAttribute("disabled")).toBe(true);
});

test("publishing never admits a newer draft revision over edits made while the request is pending", async () => {
  state.snapshot = workflowSnapshot(2, "Original");
  let finishPublish!: (value: unknown) => void;
  state.publish.mockImplementation(() => new Promise((resolve) => { finishPublish = resolve; }));
  state.save.mockReset();
  state.save.mockResolvedValue({ save_workflow_definition: { status: "STALE", revision: null, current_revision: 4, nodes: [], edges: [], diagnostics: [] } });
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const element = <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}><RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} /></AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider></Refine>;
  const view = render(element);
  state.refetch.mockImplementation(async () => { view.rerender(element); return { data: { workflow_definition: state.snapshot } }; });
  fireEvent.click(await screen.findByRole("button", { name: "Publish" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "Edited while publishing" } });
  state.snapshot = workflowSnapshot(3, "Concurrent remote edit");
  (state.snapshot.workflow as Record<string, unknown>).current_published_version = 1;
  (state.snapshot.workflow as Record<string, unknown>).publication_status = "published";
  finishPublish({ publish_workflow_definition: { status: "SUCCESS" } });
  await waitFor(() => expect(state.refetch).toHaveBeenCalled());
  expect(surface!.form.getValues("name")).toBe("Edited while publishing");
  expect(surface!.form.formState.defaultValues?.definition).toMatchObject({ revision: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  expect((state.save.mock.calls[0]?.[0] as Record<string, unknown>).expectedRevision).toBe(2);
});

test("undo during a pending deletion rekeys the restored node and edge after acknowledgement", async () => {
  const initial = workflowSnapshot(2, "Original") as Record<string, unknown>;
  const nodes = (initial.nodes as Record<string, unknown>[]);
  nodes.push({ ...nodes[0] as object, id: "node_2", key: "second", name: "Second", is_entry: false });
  initial.edges = [{ id: "edge_1", source: "node_1", target: "node_2", condition: "completed" }];
  state.snapshot = initial;
  let acknowledge!: (value: unknown) => void;
  state.save.mockReset();
  state.save.mockImplementationOnce(() => new Promise((resolve) => { acknowledge = resolve; })).mockResolvedValueOnce({ save_workflow_definition: { status: "SUCCESS", revision: 4, current_revision: 4, nodes: [], edges: [], diagnostics: [] } });
  const provider = { getApiUrl: () => "test://workflows", getOne: vi.fn(), getList: vi.fn(async () => ({ data: [], total: 0 })), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true }}><RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}><ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" defaultRecordTab="editor" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true, render: (context) => <SurfaceProbe context={context} /> }]} /></AppRuntimeProvider></ToastProvider></ModalsHost></ModelMetadataProvider></RouterContextProvider></Refine>);
  fireEvent.click(await screen.findByRole("button", { name: "Delete fixture node" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(surface!.form.getValues("definition.nodes.node_1.id")).toBe("node_1");
  acknowledge({ save_workflow_definition: { status: "SUCCESS", revision: 3, current_revision: 3, nodes: [], edges: [], diagnostics: [] } });
  await waitFor(() => expect(Object.keys(surface!.form.getValues("definition.nodes") as unknown as object).some((key) => key.startsWith("node-") && key !== "node_1")).toBe(true));
  const restoredKey = Object.keys(surface!.form.getValues("definition.nodes") as unknown as object).find((key) => key.startsWith("node-"))!;
  expect(surface!.form.getValues(`definition.nodes.${restoredKey}.id`)).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2));
  const edit = (state.save.mock.calls[1]?.[0] as { edit: Record<string, unknown>; }).edit;
  expect(edit.node_creates).toEqual([expect.objectContaining({ client_key: restoredKey })]);
  expect(edit.edge_creates).toEqual([expect.objectContaining({ source: { client_key: restoredKey }, target: { id: "node_2" } })]);
});
