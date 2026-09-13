// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import type * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("../../fragments/DialogForm", () => ({
  DialogForm: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("./SubjectPicker", () => ({
  SubjectPicker: () => <div>Recipient picker</div>,
}));

vi.mock("../resource/RowsListView", () => ({
  RowsListView: ({ rows }: { rows: readonly { label: string }[] }) => (
    <div>{rows.map((row) => <div key={row.label}>{row.label}</div>)}</div>
  ),
}));

import { ManageAccessDialog } from "./ManageAccessDialog";

afterEach(cleanup);

test("explains an empty grantable intersection while retaining existing access entries", () => {
  render(<ManageAccessDialog
    open
    onOpenChange={vi.fn()}
    trigger={<button type="button">Share</button>}
    label="selected records"
    targetIds={["1", "2"]}
    grantable={[]}
    entries={[{
      id: "grant-1",
      targetId: "1",
      relation: "viewer",
      subject: "auth/user:1",
      subjectType: "auth/user",
      label: "Existing recipient",
    }]}
    fetching={false}
    error={null}
    onRetry={vi.fn()}
    onGrant={vi.fn()}
    onRevoke={vi.fn()}
  />);

  expect(screen.getByText("You do not have permission to grant access to all selected records.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add access" })).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.getByText("Existing recipient")).toBeTruthy();
});

test("explains a relation without selectable subject types while keeping relation selection available", () => {
  render(<ManageAccessDialog
    open
    onOpenChange={vi.fn()}
    trigger={<button type="button">Share</button>}
    label="record"
    targetIds={["1"]}
    grantable={[{
      relation: "viewer",
      permission: "view",
      subjects: [{ type: "auth/user", relation: null, resource: null }],
    }]}
    entries={[]}
    fetching={false}
    error={null}
    onRetry={vi.fn()}
    onGrant={vi.fn()}
    onRevoke={vi.fn()}
  />);

  expect(screen.getByText("This recipient type has no selectable resource.")).toBeTruthy();
  const relationSelect = screen.getByRole("combobox", { name: "Access" }) as HTMLButtonElement;
  expect(relationSelect.disabled).toBe(false);
  expect((screen.getByRole("button", { name: "Add access" }) as HTMLButtonElement).disabled).toBe(true);
});
