// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

const state = vi.hoisted(() => ({
  snapshot: {} as Record<string, unknown>,
  save: vi.fn(),
  publish: vi.fn(),
  refetch: vi.fn(),
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAuthoredQuery: () => ({ data: { workflow_definition: state.snapshot }, isFetching: false, refetch: state.refetch }),
    useAuthoredMutation: (document: unknown) => [String(document).includes("Publish") ? state.publish : state.save, { fetching: false }],
  };
});

vi.mock("../documents.console", () => ({
  WorkflowDefinitionDocument: "WorkflowDefinition",
  SaveWorkflowDefinitionDocument: "SaveWorkflowDefinition",
  PublishWorkflowDefinitionDocument: "PublishWorkflowDefinition",
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    Form: (props: Record<string, unknown>) => <FormProbe {...props} />,
    registerForm: (_resource: string, component: unknown) => component,
    useRouteHref: () => (_route: string, parameters: { id: string }) => `/workflows/${parameters.id}`,
  };
});
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));

function FormProbe(props: Record<string, unknown>): React.ReactElement {
  const source = props.acknowledgedSource as { record: Record<string, unknown> | null; values: Values | null; reload: () => void } | undefined;
  const [baseline, setBaseline] = React.useState<Values | null>(null);
  const [current, setCurrent] = React.useState<Values | null>(null);
  React.useEffect(() => {
    if (source?.values && baseline === null) {
      setBaseline(source.values);
      setCurrent(source.values);
    }
  }, [baseline, source?.values]);
  if (!baseline || !current) return <span>Loading</span>;
  const node = current.definition.nodes.node_1!;
  const toolbar = props.toolbarStart as ((context: Record<string, unknown>) => React.ReactNode) | undefined;
  return <>
    <span data-testid="publication-record">{JSON.stringify(source?.record ?? null)}</span>
    <input aria-label="Workflow name" value={current.name} onChange={(event) => setCurrent({ ...current, name: event.target.value })} />
    <input aria-label="Node name" value={node.name} onChange={(event) => setCurrent({
      ...current,
      definition: { ...current.definition, nodes: { ...current.definition.nodes, node_1: { ...node, name: event.target.value } } },
    })} />
    <button onClick={() => {
      const submit = props.submit as (data: Record<string, unknown>, context: Record<string, unknown>) => Promise<unknown>;
      void submit({}, { values: current, baselineValues: baseline }).catch(() => undefined);
    }}>Save</button>
    {toolbar?.({ recordId: "workflow_1", record: source?.record ?? null, reload: source?.reload, form: { form: { getValues: () => current, setError: vi.fn() }, formIsDirty: false } })}
  </>;
}

import { WorkflowDefinitionForm } from "./WorkflowDefinitionForm";

interface Values {
  name: string;
  definition: { revision: number; nodes: Record<string, { id: string; name: string; key: string; step_class: string; config: object; join_rule: string; is_entry: boolean; position: object }>; edges: object; readiness: unknown[] };
}

function snapshot(revision: number, name: string, id = "workflow_1"): Record<string, unknown> {
  return {
    revision,
    workflow: {
      id, key: "flow", name, description: "", purpose: "AUTOMATION",
      subject_declaration: "", status: "DRAFT", version: 1, lineage_id: "workflow_1", error_workflow: null,
      max_steps: 10, budget: {}, current_published_version: null, publication_status: "unpublished",
    },
    nodes: [{ id: "node_1", key: "first", name: "First", step_class: "gate", config: {}, config_errors: {}, join_rule: "ALL_SUCCESS", is_entry: true, position: {} }],
    edges: [], readiness: [],
  };
}

beforeEach(() => {
  cleanup();
  state.snapshot = snapshot(2, "Original");
  state.save.mockReset();
  state.publish.mockReset();
  state.refetch.mockReset();
  state.refetch.mockImplementation(async () => ({ data: { workflow_definition: state.snapshot } }));
});

test("keeps the admitted revision during a dirty background refresh and submits one atomic edit", async () => {
  state.save.mockResolvedValue({ save_workflow_definition: { status: "STALE", current_revision: 3, revision: null, nodes: [], edges: [], diagnostics: [] } });
  const view = render(<WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" />);
  const workflow = await screen.findByLabelText("Workflow name");
  fireEvent.change(workflow, { target: { value: "Local title" } });
  fireEvent.change(screen.getByLabelText("Node name"), { target: { value: "Local node" } });

  state.snapshot = snapshot(3, "Remote title");
  view.rerender(<WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" />);
  expect((screen.getByLabelText("Workflow name") as HTMLInputElement).value).toBe("Local title");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  const variables = state.save.mock.calls[0]?.[0] as Record<string, unknown>;
  expect(variables.expectedRevision).toBe(2);
  expect(variables.edit).toMatchObject({
    workflow: { name: "Local title" },
    node_patches: [{ id: "node_1", fields: { name: "Local node" } }],
  });
  expect((screen.getByLabelText("Node name") as HTMLInputElement).value).toBe("Local node");
  fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
  expect(await screen.findByText("Workflow name")).toBeTruthy();
  expect(screen.getByText("Step Local node · name")).toBeTruthy();
  expect(screen.getAllByText("Your edits").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Latest saved").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Keep my edits" }));
  expect((screen.getByLabelText("Workflow name") as HTMLInputElement).value).toBe("Local title");
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  fireEvent.click(await screen.findByRole("button", { name: "Discard my edits and reload" }));
  await waitFor(() => expect((screen.getByLabelText("Workflow name") as HTMLInputElement).value).toBe("Remote title"));
  expect((screen.getByLabelText("Node name") as HTMLInputElement).value).toBe("First");
});

test("a failed explicit stale reload keeps the local draft and old revision", async () => {
  state.save.mockResolvedValue({ save_workflow_definition: { status: "STALE", current_revision: 3, revision: null, nodes: [], edges: [], diagnostics: [] } });
  render(<WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" />);
  fireEvent.change(await screen.findByLabelText("Workflow name"), { target: { value: "Local title" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("button", { name: "Review changes" });
  state.refetch.mockRejectedValueOnce(new Error("Offline"));
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  await screen.findByText("Offline");
  expect((screen.getByLabelText("Workflow name") as HTMLInputElement).value).toBe("Local title");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(2));
  expect((state.save.mock.calls[1]?.[0] as Record<string, unknown>).expectedRevision).toBe(2);
});

test("does not admit or submit a retained snapshot for the previously requested workflow", async () => {
  const view = render(<WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" />);
  await screen.findByLabelText("Workflow name");
  view.rerender(<WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_2" />);
  await waitFor(() => expect(screen.queryByLabelText("Workflow name")).toBeNull());
  expect(state.save).not.toHaveBeenCalled();
  state.snapshot = snapshot(1, "Second workflow", "workflow_2");
  view.rerender(<WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_2" />);
  expect((await screen.findByLabelText("Workflow name") as HTMLInputElement).value).toBe("Second workflow");
});

test("publish refreshes publication metadata without admitting a newer draft revision", async () => {
  state.publish.mockResolvedValue({ publish_workflow_definition: { status: "SUCCESS" } });
  const view = render(<WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Publish" }));
  state.snapshot = snapshot(3, "Remote");
  (state.snapshot.workflow as Record<string, unknown>).current_published_version = 1;
  (state.snapshot.workflow as Record<string, unknown>).publication_status = "published";
  await waitFor(() => expect(state.refetch).toHaveBeenCalled());
  // The query rerender supplies publication projections while the admitted values stay at revision 2.
  view.rerender(<WorkflowDefinitionForm resource="workflows.Workflow" id="workflow_1" />);
  await waitFor(() => expect(screen.getByTestId("publication-record").textContent).toContain('"publication_status":"published"'));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(state.save).toHaveBeenCalled());
  expect((state.save.mock.calls.at(-1)?.[0] as Record<string, unknown>).expectedRevision).toBe(2);
});
