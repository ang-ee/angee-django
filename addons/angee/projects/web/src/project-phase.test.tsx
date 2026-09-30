// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ProjectPhaseControl } from "./project-phase";

const mocks = vi.hoisted(() => ({
  confirm: vi.fn<() => Promise<boolean>>(),
  select: vi.fn(),
  update: vi.fn(),
}));

vi.mock("./documents", () => ({ SetProjectCurrentMilestoneDocument: {} }));
vi.mock("@refinedev/core", async (importOriginal) => ({
  ...await importOriginal<typeof import("@refinedev/core")>(),
  useUpdate: () => ({ mutateAsync: mocks.update, mutation: { isPending: false } }),
}));
vi.mock("@angee/metadata", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/metadata")>(),
  refineResourceName: () => "project_milestones",
  useModelMetadata: () => ({ resource: { schemaName: "console" } }),
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  extractActionOutcome: () => ({ ok: true }),
}));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  useConfirm: () => mocks.confirm,
  useActionResultRun: () => async (run: () => Promise<unknown>) => run(),
  useAuthoredResourceMutation: () => [mocks.select, { fetching: false }],
  useRuntimeViewAs: () => ({ viewAs: false, pending: false }),
  useEnumOptions: () => [{ value: "paused", label: "Paused" }, { value: "done", label: "Completed" }, { value: "dropped", label: "Dropped" }],
  useRelationOptions: () => ({
    options: [{ value: "one", label: "First" }, { value: "two", label: "Second" }],
    rows: [{ id: "one", name: "First", revision: 2, permissions: ["write"] }, { id: "two", name: "Second", permissions: [] }],
    list: { fetching: false, refetch: vi.fn() },
  }),
  DatePopover: ({ ariaLabel, onSelectDate }: { ariaLabel: string; onSelectDate: (date: Date) => void }) =>
    <button type="button" onClick={() => onSelectDate(new Date(2026, 9, 24))}>{ariaLabel}</button>,
}));
vi.mock("./i18n", () => ({
  useProjectsT: () => (key: string, values?: Record<string, string>) =>
    key === "project.phase.confirm" ? `Change from ${values?.previous} to ${values?.selected}?` : key,
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });

test("only selectable milestones ask for confirmation, and a declined confirmation never writes", async () => {
  mocks.confirm.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  mocks.select.mockResolvedValue({ set_project_current_milestone: { ok: true } });
  render(<ProjectPhaseControl value="one" row={{
    id: "project-1", revision: 4, permissions: ["write"], status: "open",
    current_milestone: { id: "one", name: "First" },
    selectable_milestones: [{ id: "two" }],
  }} />);
  const next = screen.getByRole("button", { name: "Second" });
  fireEvent.click(next);
  await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({
    body: "Change from First to Second?",
  })));
  expect(mocks.select).not.toHaveBeenCalled();
  fireEvent.click(next);
  await waitFor(() => expect(mocks.select).toHaveBeenCalledWith({
    id: "project-1", milestone: "two", expected_revision: 4,
  }));
  expect(screen.getByRole("button", { name: "First" }).hasAttribute("disabled")).toBe(true);
});

test("only a milestone writer can edit dates through the milestone resource", async () => {
  mocks.update.mockResolvedValue({ data: { id: "one" } });
  render(<ProjectPhaseControl value="one" readOnly row={{
    id: "project-1", permissions: [], status: "open",
    current_milestone: { id: "one", name: "First" }, selectable_milestones: [],
  }} />);
  expect(screen.queryByRole("button", { name: "Edit dates for Second" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Edit dates for First" }));
  fireEvent.click(screen.getByRole("button", { name: "project.phase.starts" }));
  await waitFor(() => expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({
    id: "one", values: { start_date: "2026-10-24" },
    meta: expect.objectContaining({ gqlVariables: { expected_revision: 2 } }),
  })));
});

test("the project lifecycle projection mutes the path and replaces the title badge", () => {
  vi.setSystemTime(new Date(2026, 8, 30));
  render(<ProjectPhaseControl value="one" row={{
    id: "project-1", permissions: ["write"], status: "dropped", on_path: false,
    status_changed_at: "2026-09-24", current_milestone: { id: "one", name: "First" },
    selectable_milestones: [],
  }} />);
  expect(screen.getByText("Dropped · Sep 24")).toBeTruthy();
  expect(screen.getByText("First").closest("[role='listitem']")?.className).toContain("bg-border-strong");
  expect(screen.queryByRole("button", { name: "project.action.resume" })).toBeNull();
});

test.each([["paused", "Paused"], ["done", "Completed"]])("%s projects show their state as an off-path chip", (status, label) => {
  render(<ProjectPhaseControl value="one" row={{
    id: "project-1", permissions: ["write"], status, on_path: false,
    current_milestone: { id: "one", name: "First" }, selectable_milestones: [],
  }} />);
  expect(screen.getByText(label)).toBeTruthy();
});
