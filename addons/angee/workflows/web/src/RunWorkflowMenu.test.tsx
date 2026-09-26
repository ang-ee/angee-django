// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  AppRuntimeProvider,
  type RecordChromeContext,
} from "@angee/ui";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  chrome: { resource: "documents.Document", canonicalResource: "documents.Document", dataProviderName: "console", recordId: "doc_1", record: { id: "doc_1" }, formReadOnly: false } as RecordChromeContext,
  workflows: [] as { id: string; key: string; name: string; subject_declaration: string }[],
  launch: null as Record<string, unknown> | null,
  queries: [] as { document: unknown; variables: unknown; options: Record<string, unknown> }[],
  dialogProps: null as Record<string, unknown> | null,
  start: vi.fn(async () => ({ start_workflow_run: { ok: true, message: "Started.", id: "wfr_1" } })),
  settle: vi.fn(async (fire: () => Promise<unknown>) => fire()),
}));

vi.mock("./documents.console", () => ({ RunWorkflowDocument: "Run", WorkflowLaunchDocument: "Launch", WorkflowsForSubjectDeclarationDocument: "Catalogue" }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (document: unknown, variables: unknown, options: Record<string, unknown>) => {
    mocks.queries.push({ document, variables, options });
    return document === "Launch" ? { data: { workflows_by_pk: mocks.launch }, isFetching: false } : { data: { workflows_for_subject_declaration: mocks.workflows }, isFetching: false };
  },
  useAuthoredMutation: () => [mocks.start, { fetching: false }],
  extractActionOutcome: (data: Record<string, unknown>, root: string) => data[root],
}));
vi.mock("@angee/ui", async (importOriginal) => {
  const { createMutationDialogTestDouble, createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, {
    useRecordChromeContext: () => mocks.chrome,
    useActionResultRun: () => mocks.settle,
    Button: ({ children, loading: _loading, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => <button type="button" {...props}>{children}</button>,
    Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
    MutationDialog: createMutationDialogTestDouble({ capture: (props) => { mocks.dialogProps = props; }, values: { subject: "note_7" }, submitLabel: "Confirm launch" }),
    DropdownMenu: { Root: ({ children }: { children: React.ReactNode }) => <>{children}</>, Trigger: ({ render }: { render: React.ReactNode }) => <>{render}</>, Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>, Positioner: ({ children }: { children: React.ReactNode }) => <>{children}</>, Content: ({ children }: { children: React.ReactNode }) => <div>{children}</div>, Group: ({ children }: { children: React.ReactNode }) => <div data-menu-group>{children}</div>, Label: ({ children }: { children: React.ReactNode }) => <div>{children}</div>, Item: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button type="button" {...props}>{children}</button> },
  });
});

import { RunWorkflowMenu } from "./RunWorkflowMenu";

function renderRunWorkflowMenu() {
  return render(
    <AppRuntimeProvider runtime={{}}>
      <RunWorkflowMenu />
    </AppRuntimeProvider>,
  );
}

describe("RunWorkflowMenu", () => {
  afterEach(cleanup);
  beforeEach(() => { mocks.chrome.resource = "documents.Document"; mocks.chrome.recordId = "doc_1"; mocks.workflows = []; mocks.launch = null; mocks.queries = []; mocks.dialogProps = null; mocks.start.mockClear(); mocks.settle.mockClear(); });

  test("applies the chosen published automation to the exact saved record", async () => {
    mocks.workflows = [{ id: "wfl_1", key: "archive_document", name: "Archive document", subject_declaration: "documents.Document" }];
    renderRunWorkflowMenu();
    expect(screen.getByRole("button", { name: /Automations/ })).toBeTruthy();
    const label = screen.getByText("Apply a published automation to this saved record");
    expect(label.closest("[data-menu-group]")).toBeTruthy();
    expect(mocks.queries[0]).toMatchObject({
      document: "Catalogue",
      variables: { subjectDeclaration: "documents.Document" },
      options: { dataProviderName: "console", enabled: true, models: ["workflows.Workflow"] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Archive document" }));
    await waitFor(() => expect(mocks.start).toHaveBeenCalledWith({ workflow: "wfl_1", subject: { subject_declaration: "documents.Document", id: "doc_1" } }));
  });

  test.each(["workflows.Step", "workflows.Edge", "workflows.Trigger", "workflows.WorkflowRun", "workflows.StepRun", "workflows.Decision"])("does not offer unrelated automations on %s editor chrome", (resource) => {
    mocks.chrome.resource = resource; mocks.workflows = [{ id: "wfl_1", key: "hidden", name: "Hidden", subject_declaration: "" }];
    const { container } = renderRunWorkflowMenu();
    expect(container.innerHTML).toBe("");
    expect(mocks.queries[0]?.options).toMatchObject({ enabled: false });
  });

  test("collects a metadata-backed subject before launching the current publication", async () => {
    mocks.chrome.resource = "workflows.Workflow"; mocks.chrome.recordId = "wfl_draft";
    mocks.launch = { id: "wfl_draft", purpose: "AUTOMATION", status: "DRAFT", version: 0, published_from: null, current_published_id: "wfl_pub_3", current_published_version: 3, current_published_subject_declaration: "notes.Note" };
    renderRunWorkflowMenu();
    fireEvent.click(screen.getByRole("button", { name: "Run published…" }));
    expect(mocks.dialogProps).toMatchObject({ title: "Run published automation", description: "Runs published version 3. Draft changes are not included.", fields: [{ name: "subject", required: true, relation: { resource: "notes.Note" } }], closeOnSubmit: false });
    fireEvent.submit(screen.getByRole("form", { name: "Run published automation" }));
    await waitFor(() => expect(mocks.start).toHaveBeenCalledWith({ workflow: "wfl_pub_3", subject: { subject_declaration: "notes.Note", id: "note_7" } }));
  });

  test("disables launch with readable context when no current publication exists", () => {
    mocks.chrome.resource = "workflows.Workflow";
    mocks.launch = { id: "wfl_draft", purpose: "AUTOMATION", status: "DRAFT", version: 0, published_from: null, current_published_id: null, current_published_version: null, current_published_subject_declaration: null };
    renderRunWorkflowMenu();
    expect((screen.getByRole("button", { name: "Run published…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Publish this workflow before running it.")).toBeTruthy();
  });

  test("reviews a subjectless publication in the setup dialog before launching", async () => {
    mocks.chrome.resource = "workflows.Workflow";
    mocks.launch = { id: "wfl_pub", purpose: "AUTOMATION", status: "PUBLISHED", version: 2, published_from: { id: "wfl_draft" }, current_published_id: "wfl_pub", current_published_version: 2, current_published_subject_declaration: "" };
    renderRunWorkflowMenu();
    fireEvent.click(screen.getByRole("button", { name: "Run published…" }));
    expect(mocks.dialogProps).toMatchObject({ description: "Runs current published version 2.", fields: [] });
    expect(mocks.start).not.toHaveBeenCalled();
    fireEvent.submit(screen.getByRole("form", { name: "Run published automation" }));
    await waitFor(() => expect(mocks.start).toHaveBeenCalledWith({ workflow: "wfl_pub" }));
  });

  test("does not expose agent-session workflow definitions as manual automations", () => {
    mocks.chrome.resource = "workflows.Workflow";
    mocks.launch = { id: "wfl_session", purpose: "AGENT_SESSION", status: "PUBLISHED", version: 1, published_from: null, current_published_id: "wfl_session", current_published_version: 1, current_published_subject_declaration: "agents.AgentSession" };
    expect(renderRunWorkflowMenu().container.innerHTML).toBe("");
  });
});
