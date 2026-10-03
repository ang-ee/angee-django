// @vitest-environment happy-dom

import type { DeletePreview } from "@angee/refine";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DeletePreviewDialog } from "./DeletePreviewDialog";

afterEach(cleanup);

const preview: DeletePreview = {
  totalDeletedCount: 1,
  deleted: [{ label: "notes", count: 1 }],
  updated: [],
  blocked: [],
  refusals: [],
  hasBlockers: false,
  root: { label: "note", objectLabel: "Draft", objectId: "note-1", children: [] },
};

test("shows refusals as messages alongside FK groups and prevents confirmation", () => {
  const confirm = vi.fn();
  render(<DeletePreviewDialog
    preview={{ ...preview, hasBlockers: true, refusals: ["Release the note first."], blocked: [{ label: "reviews", count: 2 }] }}
    recordCount={1}
    blockedRecordCount={1}
    isPending={false}
    onConfirm={confirm}
    onCancel={vi.fn()}
  />);

  expect(screen.getByText("Release the note first.")).toBeTruthy();
  expect(screen.queryByText("1 Release the note first.")).toBeNull();
  expect(screen.getByText("2 reviews")).toBeTruthy();
  const button = screen.getByRole<HTMLButtonElement>("button", { name: "Delete" });
  expect(button.disabled).toBe(true);
  fireEvent.click(button);
  expect(confirm).not.toHaveBeenCalled();
});

test("allows bulk confirmation when only part of the selection is refused", () => {
  const confirm = vi.fn();
  render(<DeletePreviewDialog
    preview={{ ...preview, hasBlockers: true, refusals: ["Release the note first."] }}
    recordCount={2}
    blockedRecordCount={1}
    isPending={false}
    onConfirm={confirm}
    onCancel={vi.fn()}
  />);

  expect(screen.getByText("Release the note first.")).toBeTruthy();
  expect(screen.getByText("1 selected records have deletion blockers.")).toBeTruthy();
  const button = screen.getByRole<HTMLButtonElement>("button", { name: "Delete" });
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  expect(confirm).toHaveBeenCalledOnce();
});

test("allows confirmation when the preview has no refusals or blockers", () => {
  const confirm = vi.fn();
  render(<DeletePreviewDialog
    preview={preview}
    recordCount={1}
    isPending={false}
    onConfirm={confirm}
    onCancel={vi.fn()}
  />);

  const button = screen.getByRole<HTMLButtonElement>("button", { name: "Delete" });
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  expect(confirm).toHaveBeenCalledOnce();
});
