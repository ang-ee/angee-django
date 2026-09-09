// @vitest-environment happy-dom

import * as React from "react";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  loading: true,
  wide: false,
  legacy: false,
  missingCurrent: false,
  executionStatus: "FAILED",
  payloadVariables: [] as unknown[],
  resources: [] as Array<Record<string, unknown>>,
  mutation: vi.fn(),
  routeAvailable: true,
  listeners: new Set<() => void>(),
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  const ReactRuntime = await import("react");
  return {
    ...actual,
    useAuthoredQuery: (document: unknown, variables: Record<string, string | boolean>) => {
      ReactRuntime.useSyncExternalStore(
        (listener) => { mocks.listeners.add(listener); return () => mocks.listeners.delete(listener); },
        () => mocks.loading,
      );
      if (Object.hasOwn(variables, "sourceAttempt")) return {
        data: {
          workflow_recovery_plan: { available: true, mode: "reconcile", unavailable_reason: "" },
          workflow_test_repair_context: null,
        },
        isFetching: false, error: null,
      };
      const operation = (document as { definitions?: Array<{ name?: { value?: string } }> }).definitions?.[0]?.name?.value;
      if (operation === "WorkflowRecoveryPlan") return {
        data: { workflow_recovery_plan: { available: true, mode: "reconcile", unavailable_reason: "" } },
        isFetching: false, error: null,
      };
      if (operation === "WorkflowTestRepairContext") return {
        data: { workflow_test_repair_context: null }, isFetching: false, error: null,
      };
      if (Object.hasOwn(variables, "includeInput") && !Object.hasOwn(variables, "includeCheckpoint")) {
        mocks.payloadVariables.push(variables);
        return {
          data: { workflow_step_runs: [{
            id: variables.execution,
            input: { legacy: "input" },
            output: { legacy: "output" },
            error: "legacy failed",
            stacktrace: "legacy trace",
          }] },
          isFetching: false,
          error: null,
        };
      }
      if (Object.hasOwn(variables, "includeInput")) {
        mocks.payloadVariables.push(variables);
        return {
          data: { workflow_step_attempts: [{
            id: variables.attempt,
            input_present: true,
            input: null,
            output_present: false,
            checkpoint_present: false,
            error: "failed",
            stacktrace: "trace",
          }] },
          isFetching: false,
          error: null,
        };
      }
      if (Object.hasOwn(variables, "execution")) return {
        data: {
          workflow_step_runs: variables.run !== "run-2" && variables.execution && variables.execution !== "foreign-execution" ? [{ id: variables.execution, step: { id: "step-1", key: "first", name: "First" }, system_kind: "", map_index: 3, status: mocks.executionStatus, outcome: "failed", current_attempt: mocks.legacy || mocks.missingCurrent ? null : { id: "attempt-current" } }] : [],
          workflow_step_attempts: variables.attempt && variables.attempt !== "foreign-attempt" ? [{ id: variables.attempt }] : [],
          workflow_step_attempts_aggregate: { aggregate: { count: mocks.legacy ? 0 : 1 } },
        },
        isFetching: false,
        error: null,
      };
      if (Object.hasOwn(variables, "step")) return {
        data: { workflow_step_runs: variables.step ? [{ id: "execution-1" }] : [] },
        isFetching: false,
        error: null,
      };
      if (Object.hasOwn(variables, "workflow")) return {
        data: {
          workflow_steps: [{ id: "step-1", key: "first", name: "First", step_class: "call", join_rule: "ALL", is_entry: true, position: null }],
          workflow_edges: [],
        },
        isFetching: false,
        error: null,
      };
      return {
        data: mocks.loading ? undefined : {
          workflow_runs_by_pk: { id: "run-1", origin: "TEST", occurrence_id: "occurrence-1", status: "RUNNING", waiting_kind: null, next_wake_at: null, workflow: { id: "workflow-1", name: "Flow", status: "TEST", version: 0, draft_revision: 4 } },
          workflow_step_runs_groups: [
            { key: { step_id: "step-1", status: "FAILED" }, aggregate: { count: 2 } },
            { key: { step_id: "step-1", status: "SUCCEEDED" }, aggregate: { count: 1 } },
          ],
          workflow_step_runs_aggregate: { aggregate: { count: 3 } },
        },
        isFetching: mocks.loading,
        error: null,
      };
    },
    useAuthoredMutation: () => [mocks.mutation, { fetching: false, error: null }],
  };
});

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  const ReactRuntime = await import("react");
  return {
    ...actual,
    useRouteHref: () => (_route: string, parameters: { id: string }) => `/runs/${parameters.id}`,
    useResourceRecordHrefLookup: () => (_model: string, id: string) => mocks.routeAvailable ? `/records/${id}` : undefined,
    useContainerQuery: () => [{ current: null }, mocks.wide],
    GraphView: ({ nodes, onNodeSelect }: { nodes: Array<{ id: string; detail?: React.ReactNode; selected?: boolean }>; onNodeSelect: (node: { id: string } | null) => void }) => {
      const selected = nodes.find((node) => node.selected);
      ReactRuntime.useEffect(() => {
        onNodeSelect(selected ?? null);
      }, [onNodeSelect, selected]);
      return <button type="button" onClick={() => onNodeSelect(nodes[0]!)}>graph:{nodes[0]?.detail}</button>;
    },
    ResourceList: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
      mocks.resources.push(props);
      const select = props.onSelect as ((id: string) => void) | undefined;
      return <div data-testid={`resource-${String(props.resource)}`}><button type="button" onClick={() => select?.("execution-1")}>select record</button>{props.recordId ? `record:${String(props.recordId)}` : "list"}</div>;
    },
    List: () => null,
    Form: () => null,
    Column: () => null,
    Field: () => null,
    SplitPanes: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
    SplitPane: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
    SplitPaneHandle: () => null,
  };
});

import { AttemptPayloadPanel, AttemptRecoveryPanel, RunTimelinePanel } from "./RunsPage";

beforeEach(() => {
  cleanup();
  mocks.loading = true;
  mocks.wide = false;
  mocks.legacy = false;
  mocks.missingCurrent = false;
  mocks.executionStatus = "FAILED";
  mocks.payloadVariables.length = 0;
  mocks.resources.length = 0;
  mocks.mutation.mockReset();
  mocks.routeAvailable = true;
  mocks.listeners.clear();
});

test("loading can resolve into the bounded graph and current attempt flow without changing hooks", async () => {
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/"] }) });
  await router.load();
  render(<RouterProvider router={router} />);
  expect(screen.getByText("Loading run")).toBeTruthy();
  mocks.loading = false;
  mocks.listeners.forEach((listener) => listener());
  expect(await screen.findByRole("button", { name: "graph:2 failed · 1 succeeded" })).toBeTruthy();
  expect(screen.getByText(/Occurrence: occurrence-1/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "graph:2 failed · 1 succeeded" }));
  await waitFor(() => expect(screen.getByTestId("resource-workflows.StepRun")).toBeTruthy());
  await waitFor(() => expect((router.state.location.search as Record<string, unknown>).execution).toBe("execution-1"));
  await waitFor(() => expect((router.state.location.search as Record<string, unknown>).attempt).toBe("attempt-current"));
  expect(screen.getByText("First · Item 3 · failed")).toBeTruthy();
  expect(screen.getByText("record:attempt-current")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Back to attempt history" }));
  await waitFor(() => expect((router.state.location.search as Record<string, unknown>).history).toBe("attempts"));
  expect(screen.getByText("list")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Back to executions" }));
  await waitFor(() => expect((router.state.location.search as Record<string, unknown>).history).toBe("executions"));
  await act(async () => undefined);
  expect((router.state.location.search as Record<string, unknown>).execution).toBeUndefined();
  expect(mocks.resources.every((props) => props.pageSize === 20)).toBe(true);
});

test("each selected payload pane requests only its own retained value and preserves explicit null", async () => {
  const panel = (pane: "input" | "failure") => <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><AttemptPayloadPanel attemptId="attempt-1" stepRunId="execution-1" pane={pane} /></AppRuntimeProvider>;
  const { rerender } = render(panel("input"));
  expect(await screen.findByText("null")).toBeTruthy();
  rerender(panel("failure"));
  expect(screen.getByText("failed")).toBeTruthy();
  expect(mocks.payloadVariables).toEqual([
    expect.objectContaining({ includeInput: true, includeOutput: false, includeCheckpoint: false, includeFailure: false }),
    expect.objectContaining({ includeInput: false, includeOutput: false, includeCheckpoint: false, includeFailure: true }),
  ]);
});

test("a successful recovery without a route retains its acknowledged run and cannot launch twice", async () => {
  mocks.loading = false;
  mocks.routeAvailable = false;
  mocks.mutation.mockResolvedValue({ start_workflow_recovery: { ok: true, id: "run-recovery" } });
  render(<AttemptRecoveryPanel attemptId="attempt-1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Recover from this attempt" }));
  expect(await screen.findByText("Recovery run-recovery started")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Recover from this attempt" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Recover from this attempt" }));
  expect(mocks.mutation).toHaveBeenCalledTimes(1);
});

test("a legacy execution shows parent-scoped recorded data instead of an empty attempt list", async () => {
  mocks.loading = false;
  mocks.legacy = true;
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?step=step-1&execution=execution-1"] }) });
  await router.load();
  const view = render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><RouterProvider router={router} /></AppRuntimeProvider>);

  expect(await screen.findByText("Execution data")).toBeTruthy();
  expect(screen.getByText("No attempt history was retained for this execution.")).toBeTruthy();
  expect(screen.getByRole("tree").textContent).toContain("legacy");
  expect(screen.getByRole("tree").textContent).toContain("input");
  expect(mocks.resources.some((props) => props.resource === "workflows.StepAttempt")).toBe(false);
  expect(mocks.payloadVariables.at(-1)).toEqual(expect.objectContaining({
    run: "run-1", execution: "execution-1", includeInput: true,
    includeOutput: false, includeFailure: false,
  }));

  fireEvent.click(screen.getByRole("button", { name: "Failure" }));
  expect(await screen.findByText("legacy failed")).toBeTruthy();
  await waitFor(() => expect((router.state.location.search as Record<string, unknown>).payload).toBe("failure"));
  expect(mocks.payloadVariables.at(-1)).toEqual(expect.objectContaining({
    includeInput: false, includeOutput: false, includeFailure: true,
  }));

  mocks.wide = true;
  view.rerender(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><RouterProvider router={router} /></AppRuntimeProvider>);
  expect(await screen.findByText("legacy failed")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Failure" }).className).toContain("bg-inset");
  expect(router.state.location.search).toMatchObject({
    step: "step-1", execution: "execution-1", payload: "failure",
  });
});

test("a new queued execution without evidence is not labeled legacy", async () => {
  mocks.loading = false;
  mocks.legacy = true;
  mocks.executionStatus = "SCHEDULED";
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?step=step-1&execution=execution-1"] }) });
  await router.load();
  render(<RouterProvider router={router} />);

  expect(await screen.findByText("Awaiting first attempt")).toBeTruthy();
  expect(screen.queryByText("Execution data")).toBeNull();
  expect(mocks.payloadVariables).toEqual([]);
});

test("retained attempts remain available when an old current pointer is absent", async () => {
  mocks.loading = false;
  mocks.missingCurrent = true;
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?step=step-1&execution=execution-1"] }) });
  await router.load();
  render(<RouterProvider router={router} />);

  expect(await screen.findByTestId("resource-workflows.StepAttempt")).toBeTruthy();
  expect(screen.queryByText("Execution data")).toBeNull();
});

test("foreign execution and attempt URL identities never reach a record pane", async () => {
  mocks.loading = false;
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?execution=foreign-execution&attempt=foreign-attempt"] }) });
  await router.load();
  render(<RouterProvider router={router} />);
  expect(await screen.findByText("Run unavailable")).toBeTruthy();
  expect(mocks.resources).toEqual([]);
});

test("graph initialization does not clear a valid execution and attempt deep link", async () => {
  mocks.loading = false;
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?execution=execution-1&attempt=attempt-current"] }) });
  await router.load();
  render(<RouterProvider router={router} />);
  expect(await screen.findByText("record:attempt-current")).toBeTruthy();
  await act(async () => undefined);
  expect(router.state.location.search).toMatchObject({ execution: "execution-1", attempt: "attempt-current" });
});

test("an execution from another selected graph step is unavailable", async () => {
  mocks.loading = false;
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?step=step-other&execution=execution-1"] }) });
  await router.load();
  render(<RouterProvider router={router} />);
  expect(await screen.findByText("Run unavailable")).toBeTruthy();
  expect(mocks.resources).toEqual([]);
  expect(screen.getByRole("button", { name: "Back to executions" })).toBeTruthy();
});

test("an attempt from another execution is unavailable with a route back", async () => {
  mocks.loading = false;
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?step=step-1&execution=execution-1&attempt=foreign-attempt"] }) });
  await router.load();
  render(<RouterProvider router={router} />);
  expect(await screen.findByText("Run unavailable")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Back to attempt history" })).toBeTruthy();
  expect(mocks.resources).toEqual([]);
});

test("changing the run revalidates URL children before retaining a payload pane", async () => {
  mocks.loading = false;
  function Harness() {
    const [runId, setRunId] = React.useState("run-1");
    return <><button type="button" onClick={() => setRunId("run-2")}>Next run</button><RunTimelinePanel runId={runId} /></>;
  }
  const router = createRouter({ routeTree: createRootRoute({ component: Harness }), history: createMemoryHistory({ initialEntries: ["/?step=step-1&execution=execution-1&attempt=attempt-current"] }) });
  await router.load();
  render(<RouterProvider router={router} />);
  expect(await screen.findByText("record:attempt-current")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next run" }));
  expect(await screen.findByText("Run unavailable")).toBeTruthy();
  expect(screen.queryByText("record:attempt-current")).toBeNull();
});
