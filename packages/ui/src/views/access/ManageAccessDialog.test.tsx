// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("../../fragments/DialogForm", () => ({
  DialogForm: ({ children, title, trigger }: {
    children: React.ReactNode;
    title: string;
    trigger: React.ReactNode;
  }) => <div>{trigger}<h1>{title}</h1>{children}</div>,
}));

vi.mock("./SubjectPicker", () => ({
  SubjectPicker: ({ resource, onChange }: { resource: string; onChange: (value: string) => void }) => (
    <div>Recipient picker: {resource}<button type="button" onClick={() => onChange("auth/user:person-2")}>Choose person</button></div>
  ),
}));

vi.mock("../resource/RowsListView", () => ({
  RowsListView: ({ rows, rowActions }: { rows: readonly { label: string; relation?: string }[];
    rowActions?: readonly { onSelect: (row: { label: string }) => Promise<void> }[] }) => (
    <div>{rows.map((row) => <div key={row.label}>{row.label}{row.relation ? <span>{row.relation}</span> : null}{rowActions?.[0] ?
      <button type="button" onClick={() => void rowActions[0]!.onSelect(row)}>Revoke {row.label}</button> : null}</div>)}</div>
  ),
}));

import { ManageAccessDialog } from "./ManageAccessDialog";

afterEach(cleanup);

test("provides the canonical Share trigger and selection label", () => {
  render(<ManageAccessDialog
    open
    onOpenChange={vi.fn()}
    targetIds={["1", "2"]}
    grantable={[]}
    entries={[]}
    fetching={false}
    error={null}
    onRetry={vi.fn()}
    onGrant={vi.fn()}
    onRevoke={vi.fn()}
  />);

  const trigger = screen.getByRole("button", { name: "Share" });
  expect(trigger.querySelector("svg")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Share 2 selected records" })).toBeTruthy();
});

test("compact People has its own name and shows the mounted reader roster", () => {
  const base = { open: false, onOpenChange: vi.fn(), targetIds: ["one"], grantable: [], entries: [],
    fetching: false, error: null, onRetry: vi.fn(), onGrant: vi.fn(async () => true),
    onRevoke: vi.fn(async () => undefined), compact: true,
    people: [{ subject: "auth/user:ada", label: "Ada" }] };
  const { rerender } = render(<ManageAccessDialog {...base} peopleLoaded={false} />);
  const trigger = screen.getByRole("button", { name: "People", exact: true });
  expect(trigger.textContent).toBe("PeopleA");
  expect(screen.queryByRole("button", { name: "Share", exact: true })).toBeNull();
  rerender(<ManageAccessDialog {...base} peopleLoaded />);
  expect(trigger.textContent).toBe("People · 1A");
});

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

test("omits the Share trigger when an existing action opens the dialog", () => {
  render(<ManageAccessDialog
    open onOpenChange={vi.fn()} trigger={null} label="Review folder"
    targetIds={["1"]} grantable={[]} entries={[]} fetching={false} error={null}
    onRetry={vi.fn()} onGrant={vi.fn()} onRevoke={vi.fn()}
  />);

  expect(screen.queryByRole("button", { name: "Share" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Share Review folder" })).toBeTruthy();
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

test("keeps recipient resources distinct when they share a REBAC subject type", async () => {
  render(<ManageAccessDialog
    open
    onOpenChange={vi.fn()}
    trigger={<button type="button">Share</button>}
    label="record"
    targetIds={["1"]}
    grantable={[{
      relation: "viewer",
      permission: "view",
      subjects: [
        { type: "auth/user", relation: null, resource: "iam.User" },
        { type: "auth/user", relation: null, resource: "agents.Agent" },
      ],
    }]}
    entries={[]}
    fetching={false}
    error={null}
    onRetry={vi.fn()}
    onGrant={vi.fn()}
    onRevoke={vi.fn()}
  />);

  expect(screen.getByText("Recipient picker: iam.User")).toBeTruthy();
  fireEvent.click(screen.getByRole("combobox", { name: "Recipient type" }));
  const agent = screen.getByRole("option", { name: "Agent" });
  fireEvent.pointerDown(agent, { pointerType: "mouse" });
  fireEvent.click(agent);
  await waitFor(() => {
    expect(screen.getByText("Recipient picker: agents.Agent")).toBeTruthy();
  });
});

test("shows reader roles and adds a person through the offered role", async () => {
  const onAddRole = vi.fn(async () => true);
  render(<ManageAccessDialog open onOpenChange={vi.fn()} targetIds={["one"]}
    grantable={[]} entries={[]} fetching={false} error={null} onRetry={vi.fn()}
    onGrant={vi.fn()} onRevoke={vi.fn()}
    people={[{ subject: "auth/user:person-1", label: "Avery", roleLabel: "Coordinator", you: true, following: true }]}
    roles={[{ id: "contributor", label: "Contributor", subjectResource: "iam.User", offered: true }]}
    onAddRole={onAddRole}
  />);
  expect(screen.getByText("Coordinator")).toBeTruthy();
  expect(screen.getByText("you")).toBeTruthy();
  expect(screen.getByLabelText("Following")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Choose person" }));
  fireEvent.click(screen.getByRole("button", { name: "Add access" }));
  await waitFor(() => expect(onAddRole).toHaveBeenCalledExactlyOnceWith("contributor", "auth/user:person-2"));
});

test("an app that admits no direct share offers no direct add verb", () => {
  render(<ManageAccessDialog open onOpenChange={vi.fn()} targetIds={["one"]}
    grantable={[{ relation: "reader", permission: "share", subjects: [{ type: "auth/user", relation: null, resource: "iam.User" }] }]}
    directShare={false} entries={[]} fetching={false} error={null} onRetry={vi.fn()}
    onGrant={vi.fn()} onRevoke={vi.fn()} people={[]}
  />);
  expect(screen.queryByRole("button", { name: "Add access" })).toBeNull();
  expect(screen.queryByText("Shared directly")).toBeNull();
});

test("a direct-only grant revokes through the direct share owner", async () => {
  const onRevoke = vi.fn(async () => undefined);
  const onRemovePerson = vi.fn(async () => undefined);
  const direct = { id: "grant", targetId: "one", relation: "editor", subject: "auth/group:team",
    subjectType: "auth/group", label: "Team" };
  render(<ManageAccessDialog open onOpenChange={vi.fn()} targetIds={["one"]}
    grantable={[]} entries={[direct]} fetching={false} error={null} onRetry={vi.fn()}
    onGrant={vi.fn()} onRevoke={onRevoke} onRemovePerson={onRemovePerson} people={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "Revoke Team" }));
  await waitFor(() => expect(onRevoke).toHaveBeenCalledExactlyOnceWith(direct));
  expect(onRemovePerson).not.toHaveBeenCalled();
  expect(screen.getByText("editor")).toBeTruthy();
});

test("the People add choice uses a declared relation label and retains the relation id", async () => {
  const onGrant = vi.fn(async () => true);
  render(<ManageAccessDialog open onOpenChange={vi.fn()} targetIds={["one"]}
    grantable={[{ relation: "reader", label: "Can read", permission: "share",
      subjects: [{ type: "auth/user", relation: null, resource: "iam.User" }] }]}
    entries={[]} fetching={false} error={null} onRetry={vi.fn()}
    onGrant={onGrant} onRevoke={vi.fn()} people={[]} />);
  expect(screen.getByText("Shared directly · Can read · User")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Choose person" }));
  fireEvent.click(screen.getByRole("button", { name: "Add access" }));
  await waitFor(() => expect(onGrant).toHaveBeenCalledExactlyOnceWith("reader", "auth/user:person-2"));
});
