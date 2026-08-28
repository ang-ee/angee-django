// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { RecordChromeContext } from "@angee/ui";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  chrome: {
    resource: "documents.Document",
    canonicalResource: "documents.Document",
    dataProviderName: "console",
    recordId: "doc_1",
    record: { id: "doc_1" },
  } as RecordChromeContext,
  workflows: [] as { id: string; name: string; subject_declaration: string }[],
  queryVariables: null as Record<string, unknown> | null,
  queryOptions: null as Record<string, unknown> | null,
  start: vi.fn(async () => ({
    start_workflow_run: { ok: true, message: "Started.", id: "wfr_1" },
  })),
  settle: vi.fn(async (fire: () => Promise<unknown>) => fire()),
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: (
    _document: unknown,
    variables: Record<string, unknown>,
    options: Record<string, unknown>,
  ) => {
    mocks.queryVariables = variables;
    mocks.queryOptions = options;
    return {
      data: { workflows_for_subject_declaration: mocks.workflows },
      fetching: false,
      error: null,
      refetch: () => undefined,
    };
  },
  useAuthoredMutation: () => [mocks.start, { fetching: false, error: null }],
  extractActionOutcome: (data: unknown, root: string) => {
    const outcome = (data as Record<string, unknown> | null | undefined)?.[root];
    return outcome && typeof (outcome as { ok?: unknown }).ok === "boolean" ? outcome : null;
  },
}));

vi.mock("@angee/ui", () => ({
  createNamespaceT:
    (_namespace: string, messages: Record<string, string>) => () =>
      (key: string) => messages[key] ?? key,
  useRecordChromeContext: () => mocks.chrome,
  useActionResultRun: () => mocks.settle,
  Button: ({ children, loading: _loading, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => (
    <button type="button" {...props}>{children}</button>
  ),
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  DropdownMenu: {
    Root: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Trigger: ({ render }: { render: React.ReactNode }) => <>{render}</>,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Positioner: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Content: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Item: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
      <button type="button" {...props}>{children}</button>
    ),
  },
}));

import { RunWorkflowMenu } from "./RunWorkflowMenu";

describe("RunWorkflowMenu", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.workflows = [];
    mocks.queryVariables = null;
    mocks.queryOptions = null;
    mocks.start.mockClear();
    mocks.settle.mockClear();
  });

  test("queries the current resource and renders nothing without workflows", () => {
    const { container } = render(<RunWorkflowMenu />);

    expect(container.innerHTML).toBe("");
    expect(mocks.queryVariables).toEqual({
      subjectDeclaration: "documents.Document",
    });
    expect(mocks.queryOptions).toEqual({
      dataProviderName: "console",
      models: ["workflows.Workflow"],
    });
  });

  test("runs the selected workflow on the current record through the shared outcome owner", async () => {
    mocks.workflows = [
      {
        id: "wfl_1",
        name: "Archive document",
        subject_declaration: "documents.Document",
      },
    ];

    render(<RunWorkflowMenu />);
    expect(screen.getByRole("button", { name: /Run workflow/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Archive document" }));

    await waitFor(() => expect(mocks.settle).toHaveBeenCalledOnce());
    expect(mocks.start).toHaveBeenCalledWith({
      workflow: "wfl_1",
      subjectDeclaration: "documents.Document",
      subjectId: "doc_1",
    });
  });
});
