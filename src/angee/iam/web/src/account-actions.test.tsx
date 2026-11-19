// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  offersAccountAction,
  useChangePasswordAction,
  useIssuePasswordAction,
  useRenameAction,
  useResetPasswordAction,
  useSetActiveActions,
  useSetPasswordAction,
} from "./account-actions";

const mocks = vi.hoisted(() => ({
  authored: vi.fn(), prompt: vi.fn(), reveal: vi.fn(), options: vi.fn(), setActive: vi.fn(), rename: vi.fn(),
  setPassword: vi.fn(), changePassword: vi.fn(), actionOptions: vi.fn(),
}));
vi.mock("@angee/refine", async (original) => ({
  ...await original<object>(),
  useAuthoredMutation: (_document: unknown, options: unknown) => {
    mocks.options(options); return [mocks.authored, {}];
  },
}));
vi.mock("@angee/ui", async (original) => ({
  ...await original<object>(),
  useSecretReveal: () => mocks.reveal,
  useActionResultMutation: () => [mocks.setActive, {}],
  useActionOutcomeMutation: (field: string, options: unknown) => {
    mocks.actionOptions(field, options);
    return [field === "change_password" ? mocks.changePassword : field === "set_user_password" ? mocks.setPassword : mocks.rename, {}];
  },
}));
beforeEach(() => vi.clearAllMocks());

const record = { id: "user-1", revision: 4, is_active: true, account_actions: ["SET_ACTIVE", "RENAME", "RESET_PASSWORD"] };
const context = (row: Record<string, unknown> = record) => ({
  record: row, values: {}, refresh: vi.fn(), update: vi.fn(), prompt: mocks.prompt,
});

describe("chosen passwords are write-only action inputs", () => {
  test("self-service masks all inputs and refuses a mismatched confirmation locally", async () => {
    const { result } = renderHook(useChangePasswordAction);
    const args = result.current.args;
    expect(Array.isArray(args) ? args.map(({ name, widget, autoComplete }) => [name, widget, autoComplete]) : []).toEqual([
      ["current_password", "password", "current-password"], ["new_password", "password", "new-password"],
      ["confirmation", "password", "new-password"],
    ]);
    expect(await result.current.submit?.({ current_password: "current", new_password: "new", confirmation: "other" },
      { record: null, selectedIds: [] })).toEqual({
      status: "invalid", issues: { fieldErrors: { confirmation: ["The passwords do not match."] }, formErrors: [] },
    });
    expect(mocks.changePassword).not.toHaveBeenCalled();
  });

  test("self-service sends only the two passwords through the transient public mutation", async () => {
    mocks.changePassword.mockResolvedValue({ ok: true, message: "Password changed." });
    const { result } = renderHook(useChangePasswordAction);
    expect(mocks.actionOptions).toHaveBeenCalledWith("change_password", {
      dataProviderName: "public", idArgument: null, transient: true, errorNotification: false,
    });
    expect(await result.current.submit?.({ current_password: "current", new_password: "new", confirmation: "new", id: "ignored" },
      { record: null, selectedIds: [] })).toEqual({ ok: true, message: "Password changed." });
    expect(mocks.changePassword).toHaveBeenCalledExactlyOnceWith("", { current_password: "current", new_password: "new" });
  });

  test("self-service preserves server field errors in the shared action form", async () => {
    mocks.changePassword.mockResolvedValue({
      ok: false, message: "Incorrect current password.", validationErrors: { current_password: ["Try again."] },
    });
    const { result } = renderHook(useChangePasswordAction);
    expect(await result.current.submit?.({ current_password: "wrong", new_password: "new", confirmation: "new" },
      { record: null, selectedIds: [] })).toMatchObject({ ok: false, validationErrors: { current_password: ["Try again."] } });
  });

  test("set-password confirms matching inputs and calls the projected verb with the observed revision", async () => {
    mocks.setPassword.mockResolvedValue({ ok: true, message: "Password set." });
    const { result } = renderHook(useSetPasswordAction);
    expect(result.current.visibleWhen?.(record)).toBe(false);
    expect(result.current.visibleWhen?.({ ...record, account_actions: ["SET_PASSWORD"] })).toBe(true);
    expect(result.current.visibleWhen?.({ id: "protected", account_actions: [] })).toBe(false);
    expect(result.current.danger).toBe(true);
    const args = result.current.args;
    expect(Array.isArray(args) ? args.map(({ name, widget, autoComplete }) => [name, widget, autoComplete]) : []).toEqual([
      ["password", "password", "new-password"], ["confirmation", "password", "new-password"],
    ]);
    expect(mocks.actionOptions).toHaveBeenCalledWith("set_user_password", {
      transient: true, errorNotification: false, invalidateModels: ["iam.User"],
    });
    expect(await result.current.submit?.({ password: "chosen-password", confirmation: "different" },
      { record, selectedIds: [record.id] })).toMatchObject({
      status: "invalid", issues: { fieldErrors: { confirmation: ["The passwords do not match."] } },
    });
    expect(mocks.setPassword).not.toHaveBeenCalled();
    expect(await result.current.submit?.({ password: "chosen-password", confirmation: "chosen-password", username: "ignored" },
      { record, selectedIds: [record.id] })).toMatchObject({ ok: true });
    expect(mocks.setPassword).toHaveBeenCalledExactlyOnceWith("user-1", { password: "chosen-password", expected_revision: 4 });
    mocks.setPassword.mockResolvedValue({ ok: false, validationErrors: { password: ["Too short."] } });
    expect(await result.current.submit?.({ password: "weak", confirmation: "weak" },
      { record, selectedIds: [record.id] })).toMatchObject({ ok: false, validationErrors: { password: ["Too short."] } });
  });
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
    mocks.reveal.mockResolvedValue(undefined);
    const { result } = renderHook(useResetPasswordAction);
    expect(result.current.visibleWhen?.(record)).toBe(true);
    expect(result.current.confirm).toBeTruthy();
    expect(mocks.options).toHaveBeenCalledWith(expect.objectContaining({ transient: true }));
    await act(async () => { await result.current.run?.(context()); });
    expect(mocks.authored).toHaveBeenCalledExactlyOnceWith({ id: "user-1", confirmed: true, expectedRevision: 4 });
    const reveal = mocks.reveal.mock.calls[0]?.[0];
    expect(reveal.secrets).toEqual([
      expect.objectContaining({ name: "username", value: "ada" }),
      expect.objectContaining({ name: "password", value: "once-only" }),
    ]);
    expect(reveal.copy.value).toContain("ada");
    expect(reveal.copy.value).toContain("once-only");
  });

  test("give access reveals the issued password with the username", async () => {
    mocks.authored.mockResolvedValue({ issue_user_password: { username: "grace", password: "first-secret" } });
    mocks.reveal.mockResolvedValue(undefined);
    const { result } = renderHook(useIssuePasswordAction);
    expect(result.current.visibleWhen?.(record)).toBe(false);
    expect(result.current.visibleWhen?.({ ...record, account_actions: ["ISSUE_PASSWORD"] })).toBe(true);
    await act(async () => { await result.current.run?.(context()); });
    expect(mocks.authored).toHaveBeenCalledExactlyOnceWith({ id: "user-1" });
    expect(mocks.reveal).toHaveBeenCalledWith(expect.objectContaining({ secrets: [
      expect.objectContaining({ value: "grace" }),
      expect.objectContaining({ value: "first-secret" }),
    ] }));
  });

  test("an empty result never opens a reveal prompt", async () => {
    mocks.authored.mockResolvedValue(null);
    const { result } = renderHook(useResetPasswordAction);
    await expect(result.current.run?.(context())).rejects.toThrow();
    expect(mocks.reveal).not.toHaveBeenCalled();
  });
});
