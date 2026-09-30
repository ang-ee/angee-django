// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { StageStatusbar } from "./stage-statusbar";

vi.mock("./i18n", () => ({ useWorkT: () => (key: string) => key }));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  useRelationOptions: () => ({
    options: [{ value: "first", label: "First" }, { value: "removed", label: "Removed" }],
    rows: [{ id: "first", on_path: true }, { id: "removed", on_path: false }],
    list: { fetching: false },
  }),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("the stage owner hides side stages from the path and names the state without a verb", () => {
  render(<StageStatusbar value="removed" row={{
    queue: { id: "queue-1" }, stage: { id: "removed", name: "Removed" },
    dropped_at: "2026-09-24",
  }} />);
  expect(screen.getByRole("list").textContent).toContain("First");
  expect(screen.getByRole("list").textContent).not.toContain("Removed");
  expect(screen.getByText("Removed · Sep 24, 2026")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "task.action.reopen" })).toBeNull();
});
