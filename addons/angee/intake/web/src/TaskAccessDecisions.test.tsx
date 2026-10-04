// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { TaskAccessDecisions, type CurrentAccessRow } from "./TaskAccessDecisions";

afterEach(cleanup);

const mocks = vi.hoisted(() => ({
  decide: vi.fn(), reset: vi.fn(),
  actions: [] as Array<{ id: string; permission?: string; confirm?: unknown }>,
}));
vi.mock("@angee/metadata", () => ({
  holdsPermission: (row: { permissions?: readonly string[] } | null, permission: string) =>
    row?.permissions?.includes(permission) === true,
}));
vi.mock("@angee/ui", async () => {
  const React = await import("react");
  const box = (tag: "article" | "div" | "span") => ({ children, ...props }: {
    children?: ReactNode; className?: string; tone?: string; shape?: string;
  }) => React.createElement(tag, { className: props.className, "data-tone": props.tone }, children);
  return {
    Avatar: ({ initials }: { initials: string }) => React.createElement("span", {}, initials),
    InlineEmpty: ({ label }: { label: string }) => React.createElement("p", {}, label),
    Badge: box("span"), Card: box("article"), CardContent: box("div"),
    CardFooter: box("div"), CardHeader: box("div"),
    Skeleton: box("div"), SkeletonText: box("div"),
    TextLink: ({ href, children }: { href: string; children: ReactNode }) =>
      React.createElement("a", { href }, children),
    avatarInitials: (name: string) => name.slice(0, 1),
    createNamespaceT: (_namespace: string, messages: Record<string, string>) => () =>
      (key: string, vars?: Record<string, string>) => (messages[key] ?? key).replace(
        /{(\w+)}/g, (_match, name: string) => vars?.[name] ?? "",
      ),
    useRouteHref: () => (route: string, values: { id: string }) => `/${route}/${values.id}`,
    useStatusTone: () => (value: string | null, override?: Record<string, string>) =>
      (value && override?.[value]) || (
        ({ PENDING: "warning", COMPLETED: "success" } as Record<string, string>)[value ?? ""] ?? "neutral"
      ),
    useRecordChromeActionOutcome: (action: string | { resultField: string }) => [
      action === "reset_need_access" ? mocks.reset : mocks.decide,
    ],
    useActionResultRun: () => (run: () => Promise<unknown>) => run(),
    RecordActionBar: ({ actions }: { actions: Array<{
      id: string; label: string; permission?: string; confirm?: unknown; run?: () => Promise<void>;
    }> }) => {
      mocks.actions.push(...actions);
      return React.createElement(React.Fragment, {}, ...actions.map((action) => React.createElement(
        "button", { key: action.id, onClick: () => void action.run?.() }, action.label,
      )));
    },
  };
});

function need(verdict: "PENDING" | "COMPLETED" | "REJECTED", permissions = ["write"]): CurrentAccessRow {
  return {
    id: "need-1", revision: 3, permissions,
    requester_access_granted: verdict === "COMPLETED",
    claimed_name: "Alex Example", claimed_email: "alex@example.net", access_verdict: verdict === "PENDING" ? null : [verdict === "COMPLETED" ? "intake.approve" : "intake.deny"],
    party: { id: "party-1", display_name: "Alex Example" },
    access_decision: { id: "decision-1", verdict: verdict === "PENDING" ? null : [verdict === "COMPLETED" ? "intake.approve" : "intake.deny"], is_open: verdict === "PENDING" },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.actions.length = 0;
});

describe("requester access cards", () => {
  test("pending access shows its identity, explanation, Give access and Deny", async () => {
    render(<TaskAccessDecisions needs={[need("PENDING")]} canManage />);
    expect(screen.getByText("Alex Example")).toBeTruthy();
    expect(screen.getByText("alex@example.net")).toBeTruthy();
    expect(screen.getByText("Waiting for a decision").getAttribute("data-tone")).toBe("warning");
    expect(screen.getByText(/No password is created/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "View decision history" }).getAttribute("href"))
      .toBe("/decisions.inbox.record/decision-1");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Give access" })); });
    expect(mocks.decide).toHaveBeenCalledExactlyOnceWith("need-1", {
      action: "INTAKE_APPROVE", expected_revision: 3,
    });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Deny" })); });
    expect(mocks.decide).toHaveBeenLastCalledWith("need-1", { action: "INTAKE_DENY", expected_revision: 3 });
    expect(mocks.actions.map((action) => action.permission)).toEqual(["write", "write"]);
  });

  test("approved access names the sign-in identity and resets through the Need descriptor", async () => {
    render(<TaskAccessDecisions needs={[need("COMPLETED")]} canManage />);
    expect(screen.getByText("Already has access").getAttribute("data-tone")).toBe("success");
    expect(screen.getByText(/alex@example.net is their sign-in identity/)).toBeTruthy();
    expect(screen.getByText(/Reset access removes that access/)).toBeTruthy();
    expect(mocks.actions[0]?.confirm).toEqual({
      title: "Reset requester access",
      body: "Reopen the access decision and remove this request's access? The account and its password will not change.",
    });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Reset access" })); });
    expect(mocks.reset).toHaveBeenCalledExactlyOnceWith("need-1", {
      confirmed: true, expected_revision: 3,
    });
  });

  test("a recorded approval grants no role before owner application and can be applied again", async () => {
    const row = { ...need("COMPLETED"), requester_access_granted: false };
    render(<TaskAccessDecisions needs={[row]} canManage />);
    expect(screen.getByText("Approval recorded")).toBeTruthy();
    expect(screen.getByText(/Access has not been granted yet/)).toBeTruthy();
    expect(screen.queryByText("Already has access")).toBeNull();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Grant approved access" })); });
    expect(mocks.decide).toHaveBeenLastCalledWith("need-1", { action: "INTAKE_APPROVE", expected_revision: 3 });
  });

  test("denied access has a danger state and no approve or deny verb", () => {
    render(<TaskAccessDecisions needs={[need("REJECTED")]} canManage />);
    expect(screen.getByText("Access denied").getAttribute("data-tone")).toBe("danger");
    expect(screen.getByText(/Access for alex@example.net was denied/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reset access" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Give access" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deny" })).toBeNull();
  });

  test("action buttons are absent without both task management and Need write", () => {
    const { rerender } = render(<TaskAccessDecisions needs={[need("PENDING")]} />);
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<TaskAccessDecisions needs={[need("PENDING", [])]} canManage />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
