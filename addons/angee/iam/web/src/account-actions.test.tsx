// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  offersAccountAction,
  useIssuePasswordAction,
  useRenameAction,
  useResetPasswordAction,
  useSetActiveActions,
} from "./account-actions";

const mocks = vi.hoisted(() => ({
  authored: vi.fn(), prompt: vi.fn(), options: vi.fn(), setActive: vi.fn(), rename: vi.fn(),
}));
vi.mock("@angee/refine", async (original) => ({
  ...await original<object>(),
  useAuthoredMutation: (_document: unknown, options: unknown) => {
    mocks.options(options); return [mocks.authored, {}];
  },
}));
vi.mock("@angee/ui", async (original) => ({
  ...await original<object>(),
  usePrompt: () => mocks.prompt,
  useActionResultMutation: () => [mocks.setActive, {}],
  useActionOutcomeMutation: () => [mocks.rename, {}],
}));
beforeEach(() => vi.clearAllMocks());

const record = { id: "user-1", revision: 4, is_active: true, account_actions: ["SET_ACTIVE", "RENAME", "RESET_PASSWORD"] };
const context = (row: Record<string, unknown> = record) => ({
  record: row, values: {}, refresh: vi.fn(), update: vi.fn(), prompt: mocks.prompt,
});

describe("account verbs follow the row's server projection", () => {
  test("a verb is offered only when the projection names it", () => {
    expect(offersAccountAction(record, "rename")).toBe(true);
    expect(offersAccountAction(record, "issue_password")).toBe(false);
    expect(offersAccountAction({ id: "user-2" }, "rename")).toBe(false);
    const { result } = renderHook(useSetActiveActions);
    const [deactivate, reactivate] = result.current;
    expect(deactivate?.visibleWhen?.(record)).toBe(true);
    expect(reactivate?.visibleWhen?.(record)).toBe(false);
    expect(deactivate?.visibleWhen?.({ ...record, account_actions: [] })).toBe(false);
    expect(reactivate?.visibleWhen?.({ ...record, is_active: false })).toBe(true);
  });

  test("deactivation is confirmed and sends the record's expected revision", async () => {
    const { result } = renderHook(useSetActiveActions);
    const deactivate = result.current[0]!;
    expect(deactivate.confirm).toBeTruthy();
    expect(deactivate.danger).toBe(true);
    await act(async () => { await deactivate.run?.(context()); });
    expect(mocks.setActive).toHaveBeenCalledExactlyOnceWith("user-1", {
      active: false, confirmed: true, expected_revision: 4,
    });
  });

  test("rename collects only name fields and sends the expected revision", async () => {
    mocks.rename.mockResolvedValue({ ok: true, message: "Account renamed." });
    const { result } = renderHook(useRenameAction);
    const args = result.current.args;
    expect(Array.isArray(args) ? args.map((arg) => arg.name) : []).toEqual(["first_name", "last_name"]);
    const outcome = await result.current.submit?.(
      { first_name: "Ada", last_name: "Lovelace", username: "ignored" },
      { record, selectedIds: ["user-1"] },
    );
    expect(outcome).toEqual({ ok: true, message: "Account renamed." });
    expect(mocks.rename).toHaveBeenCalledExactlyOnceWith("user-1", {
      first_name: "Ada", last_name: "Lovelace", confirmed: true, expected_revision: 4,
    });
  });
});

describe("one-time sign-in reveal", () => {
  test("reset reveals the username and new password once, with a combined copy control", async () => {
    mocks.authored.mockResolvedValue({ reset_user_password: { username: "ada", password: "once-only" } });
    mocks.prompt.mockResolvedValue(null);
    const { result } = renderHook(useResetPasswordAction);
    expect(result.current.visibleWhen?.(record)).toBe(true);
    expect(result.current.confirm).toBeTruthy();
    expect(mocks.options).toHaveBeenCalledWith(expect.objectContaining({ transient: true }));
    await act(async () => { await result.current.run?.(context()); });
    expect(mocks.authored).toHaveBeenCalledExactlyOnceWith({ id: "user-1", confirmed: true, expectedRevision: 4 });
    const reveal = mocks.prompt.mock.calls[0]?.[0];
    expect(reveal.fields).toEqual([
      expect.objectContaining({ name: "username", defaultValue: "ada", readOnly: true, copyable: true }),
      expect.objectContaining({ name: "password", defaultValue: "once-only", readOnly: true, copyable: true }),
    ]);
    expect(reveal.copy.value).toContain("ada");
    expect(reveal.copy.value).toContain("once-only");
  });

  test("give access reveals the issued password with the username", async () => {
    mocks.authored.mockResolvedValue({ issue_user_password: { username: "grace", password: "first-secret" } });
    mocks.prompt.mockResolvedValue(null);
    const { result } = renderHook(useIssuePasswordAction);
    expect(result.current.visibleWhen?.(record)).toBe(false);
    expect(result.current.visibleWhen?.({ ...record, account_actions: ["ISSUE_PASSWORD"] })).toBe(true);
    await act(async () => { await result.current.run?.(context()); });
    expect(mocks.authored).toHaveBeenCalledExactlyOnceWith({ id: "user-1" });
    expect(mocks.prompt).toHaveBeenCalledWith(expect.objectContaining({ fields: [
      expect.objectContaining({ defaultValue: "grace" }),
      expect.objectContaining({ defaultValue: "first-secret" }),
    ] }));
  });

  test("an empty result never opens a reveal prompt", async () => {
    mocks.authored.mockResolvedValue(null);
    const { result } = renderHook(useResetPasswordAction);
    await expect(result.current.run?.(context())).rejects.toThrow();
    expect(mocks.prompt).not.toHaveBeenCalled();
  });
});
