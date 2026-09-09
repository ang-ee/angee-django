// @vitest-environment happy-dom

import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { WorkflowDefinitionDocument } from "../documents.console";

const restore = vi.hoisted(() => vi.fn());
const refetchComparison = vi.hoisted(() => vi.fn());
const refetchDraft = vi.hoisted(() => vi.fn());
const setQueryData = vi.hoisted(() => vi.fn());
const queryState = vi.hoisted(() => ({ draftRevision: 4 }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useSetAuthoredQueryData: () => setQueryData,
  useAuthoredQuery: (_document: unknown, variables: Record<string, unknown>) => {
    if ("source" in variables) return {
      data: { workflow_definition_comparison: comparison },
      isFetching: false, error: null,
      refetch: refetchComparison,
    };
    return { data: { workflows_by_pk: { draft_revision: queryState.draftRevision } }, isFetching: false, error: null, refetch: refetchDraft };
  },
  useAuthoredMutation: () => [restore, { fetching: false, error: null }],
}));
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return { ...actual, GraphView: ({ nodes }: { nodes: Array<{ id: string; selected?: boolean }> }) =>
    <div aria-label="comparison graph">{nodes.map((node) => <span key={node.id} data-selected={node.selected}>{node.id}</span>)}</div> };
});

import { WorkflowVersionReview } from "./WorkflowVersionReview";

const comparison = {
  source_id: "version-2", source_version: 2, source_status: "PUBLISHED",
  draft_id: "draft-1", draft_revision: 4,
  counts: { steps_added: 0, steps_removed: 0, steps_changed: 1, connections_added: 0, connections_removed: 0, settings_changed: 0 },
  changes: [{ kind: "step", change: "changed", key: "send", field: "config", before: { channel: "email" }, after: { channel: "chat" }, presentation_only: false }],
  source_nodes: [{ key: "send", name: "Send", step_class: "handler", is_entry: true }], source_edges: [],
  draft_nodes: [{ key: "send", name: "Send", step_class: "handler", is_entry: true }], draft_edges: [],
};

afterEach(() => { cleanup(); restore.mockReset(); refetchComparison.mockReset(); refetchDraft.mockReset(); setQueryData.mockReset(); queryState.draftRevision = 4; });

test("saved comparison names exact revisions, highlights a change, and restores with its captured CAS", async () => {
  restore.mockResolvedValue({ restore_workflow_definition: {
    status: "SUCCESS", current_revision: 5,
    snapshot: { workflow: { id: "draft-1" }, revision: 5, nodes: [], edges: [], readiness: [] },
  } });
  const restored = vi.fn();
  render(<ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <WorkflowVersionReview draftId="draft-1" sourceId="version-2" sourceVersion={2} onRestored={restored} />
  </AppRuntimeProvider></ToastProvider></ModalsHost>);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByText("Version 2")).toBeTruthy();
  expect(screen.getByText("Draft revision 4")).toBeTruthy();
  expect(screen.getByLabelText("comparison graph").querySelector("[data-selected='true']")?.textContent).toBe("send");
  fireEvent.click(screen.getByRole("button", { name: "Restore to draft" }));
  await waitFor(() => expect(restore).toHaveBeenCalledWith({ workflow: "draft-1", source: "version-2", expectedRevision: 4 }));
  expect(setQueryData).toHaveBeenCalledWith(
    WorkflowDefinitionDocument,
    { workflow: "draft-1" },
    { workflow_definition: expect.objectContaining({ revision: 5 }) },
  );
  expect(restored).toHaveBeenCalledWith("draft-1");
});

test("a newer saved draft marks the retained comparison stale and failed refresh keeps it visible", async () => {
  queryState.draftRevision = 5;
  refetchComparison.mockRejectedValue(new Error("Refresh unavailable"));
  refetchDraft.mockResolvedValue({ data: { workflows_by_pk: { draft_revision: 5 } } });
  render(<ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <WorkflowVersionReview draftId="draft-1" sourceId="version-2" sourceVersion={2} onRestored={() => undefined} />
  </AppRuntimeProvider></ToastProvider></ModalsHost>);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByText("The saved draft changed. Refresh before restoring.")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Restore to draft" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Refresh comparison" }));
  expect(await screen.findByText("Refresh unavailable")).toBeTruthy();
  expect(screen.getByText("Draft revision 4")).toBeTruthy();
});

test("refresh compares the returned snapshot with the revision from the same refresh", async () => {
  queryState.draftRevision = 5;
  refetchComparison.mockResolvedValue({ data: { workflow_definition_comparison: { ...comparison, draft_revision: 6 } } });
  refetchDraft.mockResolvedValue({ data: { workflows_by_pk: { draft_revision: 7 } } });
  render(<ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <WorkflowVersionReview draftId="draft-1" sourceId="version-2" sourceVersion={2} onRestored={() => undefined} />
  </AppRuntimeProvider></ToastProvider></ModalsHost>);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  fireEvent.click(await screen.findByRole("button", { name: "Refresh comparison" }));
  expect(await screen.findByText("Draft revision 6")).toBeTruthy();
  expect(screen.getAllByText("The saved draft changed. Refresh before restoring.").length).toBeGreaterThan(0);
  expect((screen.getByRole("button", { name: "Restore to draft" }) as HTMLButtonElement).disabled).toBe(true);
});
