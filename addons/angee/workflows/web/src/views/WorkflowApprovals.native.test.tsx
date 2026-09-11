// @vitest-environment happy-dom

import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, test, vi } from "vitest";

const exactVariables = vi.hoisted(() => [] as Array<Record<string, unknown>>);
const authoredMode = vi.hoisted(() => ({ current: "success" as "success" | "error" | "empty" }));
vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: (_document: unknown, variables: Record<string, unknown>) => {
      exactVariables.push(variables);
      if (authoredMode.current === "error") return { data: undefined, isFetching: false, error: new Error("Decision query failed"), refetch: vi.fn() };
      if (authoredMode.current === "empty") return { data: { workflow_decisions: [] }, isFetching: false, error: null, refetch: vi.fn() };
      return {
        data: { workflow_decisions: [{
          id: "decision-1", action: "review", priority: 1, payload: {}, verdict: "PENDING",
          resolution: {}, attempts: 0, max_attempts: 3, expires_at: null, escalate_at: null,
          decision_schema: null, workflow_name: "Session", step_name: "Approve tool",
          created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z",
        }] },
        isFetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useAuthoredMutation: () => [vi.fn(), { fetching: false, error: null }],
  };
});

import { WorkflowApprovals } from "./WorkflowApprovals";

const field = (name: string, scalar = "String") => ({
  name, kind: "scalar" as const, scalar, values: [], readable: true, filterable: true,
  sortable: true, aggregatable: false, groupable: name === "verdict", creatable: false,
  updatable: false, requiredOnCreate: false,
  filter: { field: name, scalar, values: [], operators: ["exact"] },
});
const resource = testDataResource("workflows.Decision", {
  schemaName: "public",
  modelName: "Decision",
  roots: { aggregate: "workflow_decisions_aggregate" },
  typeNames: { node: "DecisionType", filter: "DecisionBoolExp", order: "DecisionOrderBy" },
  fields: [field("id", "ID"), field("step_run", "ID"), field("action"), field("verdict"), field("priority", "Int"), field("updated_at", "DateTime")],
  query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID", filter: null }),
    "step_run.run": testQueryField("step_run.run", { kind: "relation", scalar: "ID", filter: { field: "step_run__run", scalar: "ID", values: [], operators: ["exact"] }, relation: { model: "workflows.WorkflowRun", identityPath: "step_run.run.id" }, row: { path: "step_run.run.id", paths: ["step_run.run.id"] } }),
    action: testQueryField("action", { scalar: "String", filter: null }),
    verdict: testQueryField("verdict", { scalar: "String", filter: { field: "verdict", scalar: "String", values: [], operators: ["exact"] } }),
    priority: testQueryField("priority", { scalar: "Int", filter: null, sort: { field: "priority" } }),
    updated_at: testQueryField("updated_at", { scalar: "DateTime", filter: null }),
  } }),
});

afterEach(() => { cleanup(); exactVariables.length = 0; authoredMode.current = "success"; });

test("the native scoped collection opens only the selected Run decision task", async () => {
  const row = { id: "decision-1", action: "review", verdict: "PENDING", priority: 1, updated_at: "2026-09-09T00:00:00Z" };
  const provider = {
    getApiUrl: () => "test://workflows",
    getList: vi.fn(async () => ({ data: [row], total: 1 })),
    getOne: vi.fn(async () => ({ data: row })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, public: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals runId="run-1" />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </ModelMetadataProvider></RouterContextProvider>
    </Refine>,
  );

  await waitFor(() => expect(provider.getList).toHaveBeenCalledWith(expect.objectContaining({
    pagination: expect.objectContaining({ pageSize: 20 }),
    meta: expect.objectContaining({ gqlVariables: expect.objectContaining({
      where: { step_run__run: { _eq: "run-1" }, verdict: { _eq: "PENDING" } },
    }) }),
  })));
  fireEvent.click(await screen.findByText("review"));
  expect(await screen.findByText("Approve tool")).toBeTruthy();
  expect(exactVariables.at(-1)).toEqual({ id: "decision-1", run: "run-1" });
});

test("the global inbox uses native paging and an exact historical decision read", async () => {
  const row = { id: "decision-1", action: "review", verdict: "PENDING", priority: 1, updated_at: "2026-09-09T00:00:00Z" };
  const provider = {
    getApiUrl: () => "test://workflows",
    getList: vi.fn(async () => ({ data: [row], total: 41 })),
    getOne: vi.fn(async () => ({ data: row })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, public: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </ModelMetadataProvider></RouterContextProvider>
    </Refine>,
  );

  await waitFor(() => expect(provider.getList).toHaveBeenCalledWith(expect.objectContaining({ pagination: expect.objectContaining({ pageSize: 20 }) })));
  fireEvent.click(await screen.findByText("review"));
  expect(await screen.findByText("Approve tool")).toBeTruthy();
  expect(exactVariables.at(-1)).toEqual({ id: "decision-1" });
});

test("a record overlay renders one exact target task without mounting a nested Decision collection", async () => {
  const provider = {
    getApiUrl: () => "test://workflows",
    getList: vi.fn(), getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, public: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals target={{ model: "parties.Party", id: "party-7", tab: "accounting" }} decisionId="decision-1" selectedTaskOnly />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </ModelMetadataProvider></RouterContextProvider>
    </Refine>,
  );

  expect(await screen.findByText("Approve tool")).toBeTruthy();
  expect(provider.getList).not.toHaveBeenCalled();
  expect(exactVariables.at(-1)).toEqual({ id: "decision-1", targetModel: "parties.Party", targetId: "party-7", targetTab: "accounting" });
});

test("a selected target distinguishes query failure from a permission-masked unavailable result", async () => {
  const provider = {
    getApiUrl: () => "test://workflows",
    getList: vi.fn(), getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = (mode: "success" | "error" | "empty", decisionId = "decision-1") => {
    authoredMode.current = mode;
    return <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, public: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals target={{ model: "parties.Party", id: "party-7", tab: "accounting" }} decisionId={decisionId} selectedTaskOnly />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </ModelMetadataProvider></RouterContextProvider>
    </Refine>;
  };

  const rendered = render(view("success"));
  expect(await screen.findByText("Approve tool")).toBeTruthy();
  rendered.rerender(view("error", "decision-2"));
  expect(await screen.findByText("Decision query failed")).toBeTruthy();
  expect(screen.queryByText("Approve tool")).toBeNull();
  rendered.unmount();
  render(view("empty"));
  expect(await screen.findByText("This approval is unavailable or you no longer have access.")).toBeTruthy();
  expect(screen.queryByText(/does not exist/i)).toBeNull();
});

test("dirty approval values use the shared leave guard before changing selection", async () => {
  const rows = [
    { id: "decision-1", action: "review", verdict: "PENDING", priority: 1, updated_at: "2026-09-09T00:00:00Z" },
    { id: "decision-2", action: "approve", verdict: "PENDING", priority: 2, updated_at: "2026-09-09T00:01:00Z" },
  ];
  const provider = {
    getApiUrl: () => "test://workflows",
    getList: vi.fn(async () => ({ data: rows, total: 2 })),
    getOne: vi.fn(async ({ id }: { id: string }) => ({ data: rows.find((row) => row.id === id)! })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Refine resources={[...refineResourcesFromDataResources([resource])]} dataProvider={{ default: provider, public: provider }} options={{ disableTelemetry: true }}>
      <RouterContextProvider router={router}><ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([resource])}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowApprovals /></AppRuntimeProvider></ToastProvider></ModalsHost>
      </ModelMetadataProvider></RouterContextProvider>
    </Refine>,
  );
  fireEvent.click(await screen.findByText("review"));
  fireEvent.click(await screen.findByRole("tab", { name: "Your decision" }));
  const resolution = await screen.findByLabelText("Resolution payload");
  fireEvent.change(resolution, { target: { value: "{\"kept\":true}" } });
  fireEvent.click(screen.getByRole("button", { name: "Next record" }));
  expect(await screen.findByText("Unsaved changes - leave without saving?")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stay" }));
  expect((screen.getByLabelText("Resolution payload") as HTMLTextAreaElement).value).toContain("kept");
});
