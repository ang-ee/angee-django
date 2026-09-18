// @vitest-environment happy-dom

import * as React from "react";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  loading: true,
  wide: false,
  noAttempts: false,
  missingCurrent: false,
  executionStatus: "FAILED",
  runStatus: "RUNNING",
  runError: null as string | null,
  recoveryMapIndex: null as number | null,
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
          workflow_recovery_plan: { available: true, mode: "reconcile", unavailable_reason: "", map_index: mocks.recoveryMapIndex },
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
          workflow_step_runs: variables.run !== "run-2" && variables.execution && variables.execution !== "foreign-execution" ? [{ id: variables.execution, step: { id: "step-1", key: "first", name: "First" }, system_kind: "", map_index: 3, status: mocks.executionStatus, outcome: "failed", current_attempt: mocks.noAttempts || mocks.missingCurrent ? null : { id: "attempt-current" } }] : [],
          workflow_step_attempts: variables.attempt && variables.attempt !== "foreign-attempt" ? [{ id: variables.attempt }] : [],
          workflow_step_attempts_aggregate: { aggregate: { count: mocks.noAttempts ? 0 : 1 } },
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
          workflow_runs_by_pk: { id: "run-1", origin: "TEST", occurrence_id: "occurrence-1", status: mocks.runStatus, waiting_kind: null, next_wake_at: null, error: mocks.runError ?? (mocks.runStatus === "FAILED" ? "run fallback" : null), workflow: { id: "workflow-1", name: "Flow", status: "TEST", version: 0, draft_revision: 4 } },
          failed_step_runs: mocks.runStatus === "FAILED" ? [{
            id: "execution-failed", system_kind: "", map_index: -1, error: "execution fallback",
            step: { id: "step-1", key: "resolve", name: "Resolve source" },
            current_attempt: { id: "attempt-failed", error: "Missing workflow actor" },
          }] : [],
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
    RelationFieldWidget: ({
      value,
      onChange,
      readOnly,
      "aria-label": ariaLabel,
    }: {
      value?: string | null;
      onChange?: (value: string) => void;
      readOnly?: boolean;
      "aria-label"?: string;
    }) => <select
      aria-label={ariaLabel}
      value={value ?? ""}
      disabled={readOnly}
      onChange={(event) => onChange?.(event.target.value)}
    >
      <option value="" />
      <option value="wfr_exact_prior">Recovery: exact retained basis</option>
    </select>,
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
  mocks.noAttempts = false;
  mocks.missingCurrent = false;
  mocks.executionStatus = "FAILED";
  mocks.runStatus = "RUNNING";
  mocks.runError = null;
  mocks.recoveryMapIndex = null;
  mocks.payloadVariables.length = 0;
  mocks.resources.length = 0;
  mocks.mutation.mockReset();
  mocks.routeAvailable = true;
  mocks.listeners.clear();
});

test("a failed run leads with the failed step, retained error, inspection, and native recovery", async () => {
  mocks.loading = false;
  mocks.runStatus = "FAILED";
  const reprocess = vi.fn().mockResolvedValue("Reprocess started");
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" onReprocess={reprocess} /> }), history: createMemoryHistory({ initialEntries: ["/"] }) });
  await router.load();
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole("heading", { name: "Run failed in Resolve source" })).toBeTruthy();
  expect(screen.getByText("Missing workflow actor")).toBeTruthy();
  expect(screen.getByText("Recovery options")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Reprocess run" }));
  await waitFor(() => expect(reprocess).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Inspect failed execution" }));
  await waitFor(() => expect(router.state.location.search).toMatchObject({
    step: "step-1", execution: "execution-failed", attempt: "attempt-failed",
  }));
  await waitFor(() => expect(mocks.resources.some((props) =>
    props.resource === "workflows.StepAttempt" && props.defaultRecordTab === "failure",
  )).toBe(true));
});

test("an active run exposes a durable advancement error without failed-run actions", async () => {
  mocks.loading = false;
  mocks.runStatus = "RUNNING";
  mocks.runError = "Workflow advancement wfd_problem could not continue: Map evidence is invalid.";
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/"] }) });
  await router.load();
  const view = render(<RouterProvider router={router} />);

  expect(await screen.findByRole("heading", { name: "Run advancement could not continue" })).toBeTruthy();
  expect(screen.getByText(mocks.runError)).toBeTruthy();
  expect(screen.getByText("The queued work will retry automatically. If this continues, ask the workflow owner to resolve the error.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Reprocess run" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Inspect failed execution" })).toBeNull();

  view.unmount();
  mocks.runStatus = "CANCELED";
  const canceledRouter = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/"] }) });
  await canceledRouter.load();
  render(<RouterProvider router={canceledRouter} />);
  expect(screen.queryByRole("heading", { name: "Run advancement could not continue" })).toBeNull();
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

test("a Map recovery can name the exact retained prior recovery basis", async () => {
  mocks.loading = false;
  mocks.recoveryMapIndex = 2;
  mocks.mutation.mockResolvedValue({ start_workflow_recovery: { ok: true, id: "run-map-recovery" } });
  render(<AttemptRecoveryPanel attemptId="attempt-map-failure" />);
  fireEvent.change(await screen.findByLabelText("Prior Map recovery run"), {
    target: { value: "wfr_exact_prior" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Recover from this attempt" }));
  await waitFor(() => expect(mocks.mutation).toHaveBeenCalledWith(expect.objectContaining({
    sourceAttempt: "attempt-map-failure",
    priorRecovery: "wfr_exact_prior",
  })));
});

test("a terminal execution without retained attempts reports missing history without querying payloads", async () => {
  mocks.loading = false;
  mocks.noAttempts = true;
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?step=step-1&execution=execution-1"] }) });
  await router.load();
  const view = render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><RouterProvider router={router} /></AppRuntimeProvider>);

  expect(await screen.findByText("No attempt history was retained for this execution.")).toBeTruthy();
  expect(screen.queryByText("Awaiting first attempt")).toBeNull();
  expect(mocks.resources.some((props) => props.resource === "workflows.StepAttempt")).toBe(false);
  expect(mocks.payloadVariables).toEqual([]);

  mocks.wide = true;
  view.rerender(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><RouterProvider router={router} /></AppRuntimeProvider>);
  expect(await screen.findByText("No attempt history was retained for this execution.")).toBeTruthy();
  expect(router.state.location.search).toMatchObject({
    step: "step-1", execution: "execution-1",
  });
});

test("a new queued execution awaits its first attempt", async () => {
  mocks.loading = false;
  mocks.noAttempts = true;
  mocks.executionStatus = "SCHEDULED";
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?step=step-1&execution=execution-1"] }) });
  await router.load();
  render(<RouterProvider router={router} />);

  expect(await screen.findByText("Awaiting first attempt")).toBeTruthy();
  expect(screen.queryByText("No attempt history was retained for this execution.")).toBeNull();
  expect(mocks.payloadVariables).toEqual([]);
});

test("retained attempts remain available when an old current pointer is absent", async () => {
  mocks.loading = false;
  mocks.missingCurrent = true;
  const router = createRouter({ routeTree: createRootRoute({ component: () => <RunTimelinePanel runId="run-1" /> }), history: createMemoryHistory({ initialEntries: ["/?step=step-1&execution=execution-1"] }) });
  await router.load();
  render(<RouterProvider router={router} />);

  expect(await screen.findByTestId("resource-workflows.StepAttempt")).toBeTruthy();
  expect(screen.queryByText("No attempt history was retained for this execution.")).toBeNull();
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
