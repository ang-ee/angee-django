// @vitest-environment happy-dom

import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, test, vi } from "vitest";

const exactVariables = vi.hoisted(() => [] as Array<Record<string, unknown>>);
const authoredMode = vi.hoisted(() => ({ current: "success" as "success" | "error" | "empty" }));
const authoredVerdict = vi.hoisted(() => ({ current: "PENDING" }));
const decisionSchema = {
  type: "object", required: ["action"], properties: {
    action: { type: "string", enum: ["complete"], options: [
      { value: "complete", label: "Complete", verdict: "COMPLETE" },
    ] },
    note: { type: "string", label: "Review note" },
  },
  oneOf: [{ type: "object", required: ["action"], properties: {
    action: { const: "complete" }, note: { type: "string" },
  }, additionalProperties: false }],
};
vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: (_document: unknown, variables: Record<string, unknown>) => {
      exactVariables.push(variables);
      if (authoredMode.current === "error") return { data: undefined, isFetching: false, error: new Error("Decision query failed"), refetch: vi.fn() };
      if (authoredMode.current === "empty") return { data: { workflow_decisions: [] }, isFetching: false, error: null, refetch: vi.fn() };
      const data = { workflow_decisions: [{
        id: String(variables.id ?? "decision-1"), action: "review", priority: 1, payload: {}, verdict: authoredVerdict.current,
        resolution: {}, resolved_by: "", attempts: 0, max_attempts: 3, expires_at: null, escalate_at: null,
        decision_schema: decisionSchema, workflow_name: "Session", step_name: "Approve tool",
        created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z",
      }] };
      return {
        data,
        isFetching: false,
        error: null,
        refetch: vi.fn(async () => ({ data })),
      };
    },
    useAuthoredMutation: () => [vi.fn(async ({ decision, verdict }: { decision: string; verdict: string }) => ({
      decide: {
        decision: { id: decision, verdict: verdict === "COMPLETE" ? "COMPLETED" : verdict === "REJECT" ? "REJECTED" : "ESCALATED" },
        validation_errors: null,
      },
    })), { fetching: false, error: null }],
  };
});

import { RoutedDecisionTask, WorkflowApprovals } from "./WorkflowApprovals";

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

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://workflows", resources: [resource], providerNames: ["public"] });
afterEach(() => {
  cleanup();
  clearClients();
  exactVariables.length = 0;
  authoredMode.current = "success";
  authoredVerdict.current = "PENDING";
});

test("the native scoped collection opens only the selected Run decision task", async () => {
  const row = { id: "decision-1", action: "review", verdict: "PENDING", priority: 1, updated_at: "2026-09-09T00:00:00Z" };
  const provider = {
    getList: vi.fn(async () => ({ data: [row], total: 1 })),
    getOne: vi.fn(async () => ({ data: row })),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Provider dataProvider={provider}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals runId="run-1" />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>,
  );

  await waitFor(() => expect(provider.getList).toHaveBeenCalledWith(expect.objectContaining({
    pagination: expect.objectContaining({ pageSize: 20 }),
    meta: expect.objectContaining({ gqlVariables: expect.objectContaining({ where: {
      _and: expect.arrayContaining([
        { step_run__run: { _eq: "run-1" } },
        { verdict: { _eq: "PENDING" } },
      ]),
    } }) }),
  })));
  fireEvent.click(await screen.findByText("review"));
  expect(await screen.findByText("Approve tool")).toBeTruthy();
  expect(exactVariables.at(-1)).toEqual({ id: "decision-1", run: "run-1" });
});

test("the global inbox uses native paging and an exact historical decision read", async () => {
  const row = { id: "decision-1", action: "review", verdict: "PENDING", priority: 1, updated_at: "2026-09-09T00:00:00Z" };
  const provider = {
    getList: vi.fn(async () => ({ data: [row], total: 41 })),
    getOne: vi.fn(async () => ({ data: row })),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Provider dataProvider={provider}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>,
  );

  await waitFor(() => expect(provider.getList).toHaveBeenCalledWith(expect.objectContaining({ pagination: expect.objectContaining({ pageSize: 20 }) })));
  fireEvent.click(await screen.findByText("review"));
  expect(await screen.findByText("Approve tool")).toBeTruthy();
  expect(exactVariables.at(-1)).toEqual({ id: "decision-1" });
});

test("a record overlay renders one exact target task without mounting a nested Decision collection", async () => {
  const provider = {
    getList: vi.fn(),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Provider dataProvider={provider}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals target={{ model: "parties.Party", id: "party-7", tab: "accounting" }} decisionId="decision-1" selectedTaskOnly />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>,
  );

  expect(await screen.findByText("Approve tool")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Back to approvals" })).toBeNull();
  expect(provider.getList).not.toHaveBeenCalled();
  expect(exactVariables.at(-1)).toEqual({ id: "decision-1", targetModel: "parties.Party", targetId: "party-7", targetTab: "accounting" });
});

test("a run history link renders one completed Decision outside the pending collection", async () => {
  authoredVerdict.current = "COMPLETED";
  const onDecisionChange = vi.fn();
  const provider = {
    getList: vi.fn(),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Provider dataProvider={provider}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals runId="run-1" decisionId="decision-1" selectedTaskOnly onDecisionChange={onDecisionChange} />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>,
  );

  expect(await screen.findByText("This approval is no longer pending.")).toBeTruthy();
  expect(provider.getList).not.toHaveBeenCalled();
  expect(exactVariables.at(-1)).toEqual({ id: "decision-1", run: "run-1" });
  fireEvent.click(screen.getByRole("button", { name: "Back to approvals" }));
  await waitFor(() => expect(onDecisionChange).toHaveBeenCalledWith(null));
});

test("a selected target distinguishes query failure from a permission-masked unavailable result", async () => {
  const onDecisionChange = vi.fn();
  const provider = {
    getList: vi.fn(),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = (mode: "success" | "error" | "empty", decisionId = "decision-1") => {
    authoredMode.current = mode;
    return <Provider dataProvider={provider}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <WorkflowApprovals target={{ model: "parties.Party", id: "party-7", tab: "accounting" }} decisionId={decisionId} selectedTaskOnly onDecisionChange={onDecisionChange} />
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>;
  };

  const rendered = render(view("success"));
  expect(await screen.findByText("Approve tool")).toBeTruthy();
  rendered.rerender(view("error", "decision-2"));
  expect(await screen.findByText("Decision query failed")).toBeTruthy();
  expect(screen.queryByText("Approve tool")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Back to approvals" }));
  await waitFor(() => expect(onDecisionChange).toHaveBeenCalledWith(null));
  onDecisionChange.mockClear();
  rendered.unmount();
  render(view("empty"));
  expect(await screen.findByText("This approval is unavailable or you no longer have access.")).toBeTruthy();
  expect(screen.queryByText(/does not exist/i)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Back to approvals" }));
  await waitFor(() => expect(onDecisionChange).toHaveBeenCalledWith(null));
});

test("dirty approval values use the shared leave guard before changing selection", async () => {
  const rows = [
    { id: "decision-1", action: "review", verdict: "PENDING", priority: 1, updated_at: "2026-09-09T00:00:00Z" },
    { id: "decision-2", action: "approve", verdict: "PENDING", priority: 2, updated_at: "2026-09-09T00:01:00Z" },
  ];
  const provider = {
    getList: vi.fn(async () => ({ data: rows, total: 2 })),
    getOne: vi.fn(async ({ id }: { id: string }) => ({ data: rows.find((row) => row.id === id)! })),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Provider dataProvider={provider}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><WorkflowApprovals /></AppRuntimeProvider></ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>,
  );
  fireEvent.click(await screen.findByText("review"));
  fireEvent.click(await screen.findByRole("tab", { name: "Your decision" }));
  fireEvent.click(await screen.findByRole("button", { name: "Complete" }));
  const resolution = await screen.findByLabelText("Review note");
  fireEvent.change(resolution, { target: { value: "Keep this review note" } });
  fireEvent.click(screen.getByRole("button", { name: "Next record" }));
  expect(await screen.findByText("Unsaved changes - leave without saving?")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stay" }));
  expect((screen.getByLabelText("Review note") as HTMLInputElement).value).toBe("Keep this review note");
  fireEvent.click(screen.getByRole("button", { name: "Next record" }));
  fireEvent.click(await screen.findByRole("button", { name: "Leave" }));
  await waitFor(() => expect(exactVariables.at(-1)).toEqual({ id: "decision-2" }));
  expect(screen.queryByText("Unsaved changes - leave without saving?")).toBeNull();
});

test("resolution awaits the live collection successor instead of the captured pager callback", async () => {
  const earlier = vi.fn();
  const later = vi.fn();
  const resolved = vi.fn(async () => "advanced" as const);
  const close = vi.fn();
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <RoutedDecisionTask
          recordId="decision-2"
          navigation={{ current: 2, total: 3, onPrev: earlier, onNext: later }}
          onClose={close}
          onResolved={resolved}
          onDirtyChange={vi.fn()}
          requestLeave={async () => true}
        />
      </AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Complete" }));
  fireEvent.click((await screen.findAllByRole("button", { name: "Complete" }))[1]!);
  await waitFor(() => expect(resolved).toHaveBeenCalledOnce());
  expect(later).not.toHaveBeenCalled();
  expect(earlier).not.toHaveBeenCalled();
  expect(close).not.toHaveBeenCalled();
});

test("resolution at the end reports remaining earlier work without wrapping", async () => {
  const earlier = vi.fn();
  const close = vi.fn();
  const resolved = vi.fn(async () => "end" as const);
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <RoutedDecisionTask
          recordId="decision-3"
          navigation={{ current: 3, total: 3, onPrev: earlier }}
          onClose={close}
          onResolved={resolved}
          onDirtyChange={vi.fn()}
          requestLeave={async () => true}
        />
      </AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Complete" }));
  fireEvent.click((await screen.findAllByRole("button", { name: "Complete" }))[1]!);
  expect(await screen.findByText("End of this review queue")).toBeTruthy();
  expect(resolved).toHaveBeenCalledOnce();
  expect(close).not.toHaveBeenCalled();
  expect(earlier).not.toHaveBeenCalled();
});

test("resolution shows queue completion only when the refreshed query is empty", async () => {
  const close = vi.fn();
  const resolved = vi.fn(async () => "empty" as const);
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <RoutedDecisionTask
          recordId="decision-1"
          navigation={{ current: 1, total: 1 }}
          onClose={close}
          onResolved={resolved}
          onDirtyChange={vi.fn()}
          requestLeave={async () => true}
        />
      </AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Complete" }));
  fireEvent.click((await screen.findAllByRole("button", { name: "Complete" }))[1]!);
  expect(await screen.findByText("Queue complete")).toBeTruthy();
  expect(screen.getByText("There are no pending approvals in this view.")).toBeTruthy();
  expect(resolved).toHaveBeenCalledOnce();
  expect(close).not.toHaveBeenCalled();
});
