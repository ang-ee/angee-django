// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  workflows: [] as { id: string; key: string; name: string }[],
  start: vi.fn(async () => ({
    start_workflow_run: { ok: true, message: "Started", id: "workflow-run-1" },
  })),
  settle: vi.fn(async (fire: () => Promise<unknown>) => fire()),
  settleOptions: null as Record<string, unknown> | null,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({
    data: { workflows_for_subject_declaration: mocks.workflows },
  }),
  useAuthoredMutation: () => [mocks.start, { fetching: false }],
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  useActionResultRun: (options: Record<string, unknown>) => {
    mocks.settleOptions = options;
    return mocks.settle;
  },
}));

import { RunDedupeAction } from "./RunDedupeAction";

beforeEach(() => {
  mocks.workflows = [
    { id: "workflow-1", key: "dedupe_parties", name: "Renamed display label" },
  ];
  mocks.start.mockClear();
  mocks.settle.mockClear();
  mocks.settleOptions = null;
});

afterEach(cleanup);

describe("RunDedupeAction", () => {
  test("selects the seeded lineage by stable key and delegates outcome handling", async () => {
    render(<RunDedupeAction />);

    expect(mocks.settleOptions).toEqual({
      linkTo: "workflows.WorkflowRun",
      noResultTitle: "Could not start the dedupe run.",
    });
    fireEvent.click(screen.getByRole("button", { name: "Run dedupe" }));

    await waitFor(() => expect(mocks.settle).toHaveBeenCalledOnce());
    expect(mocks.start).toHaveBeenCalledWith({ id: "workflow-1" });
  });

  test("does not select a workflow that only retains the old display name", () => {
    mocks.workflows = [
      { id: "workflow-1", key: "another_lineage", name: "Deduplicate contacts" },
    ];

    const { container } = render(<RunDedupeAction />);

    expect(container.innerHTML).toBe("");
    expect(mocks.start).not.toHaveBeenCalled();
  });
});
