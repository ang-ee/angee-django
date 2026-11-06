// @vitest-environment happy-dom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createUiTestProviders } from "@angee/ui/testing";
import { ToastProvider } from "@angee/ui";
import type { ReactNode } from "react";

import {
  offersAccountAction,
  useIssuePasswordAction,
  useRenameAction,
  useResetPasswordAction,
  useSetActiveActions,
} from "./account-actions";

const mocks = vi.hoisted(() => ({
  authored: vi.fn(), prompt: vi.fn(), reveal: vi.fn(), options: vi.fn(), setActive: vi.fn(), rename: vi.fn(),
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
  useActionOutcomeMutation: () => [mocks.rename, {}],
}));
const { Provider, clearClients } = createUiTestProviders();
const wrapper = ({ children }: { children: ReactNode }) => <Provider><ToastProvider>{children}</ToastProvider></Provider>;
afterEach(() => { cleanup(); clearClients(); });
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
    const { result } = renderHook(useSetActiveActions, { wrapper });
    const [deactivate, reactivate] = result.current;
    expect(deactivate?.visibleWhen?.(record)).toBe(true);
    expect(reactivate?.visibleWhen?.(record)).toBe(false);
    expect(deactivate?.visibleWhen?.({ ...record, account_actions: [] })).toBe(false);
    expect(reactivate?.visibleWhen?.({ ...record, is_active: false })).toBe(true);
  });

  test("deactivation is confirmed and sends the record's expected revision", async () => {
    const { result } = renderHook(useSetActiveActions, { wrapper });
    const deactivate = result.current[0]!;
    expect(deactivate.confirm).toBeTruthy();
    expect(deactivate.danger).toBe(true);
    await act(async () => { await deactivate.run?.(context()); });
    expect(mocks.setActive).toHaveBeenCalledExactlyOnceWith("user-1", {
      active: false, confirmed: true, expected_revision: 4,
    }, { reconciliation: undefined });
  });

  test("rename collects only name fields and sends the expected revision", async () => {
    mocks.rename.mockResolvedValue({ ok: true, message: "Account renamed." });
    const { result } = renderHook(useRenameAction, { wrapper });
    const args = result.current.args;
    expect(Array.isArray(args) ? args.map((arg) => arg.name) : []).toEqual(["first_name", "last_name"]);
    const outcome = await result.current.submit?.(
      { first_name: "Ada", last_name: "Lovelace", username: "ignored" },
      { record, selectedIds: ["user-1"] },
    );
    expect(outcome).toEqual({ ok: true, message: "Account renamed." });
    expect(mocks.rename).toHaveBeenCalledExactlyOnceWith("user-1", {
      first_name: "Ada", last_name: "Lovelace", confirmed: true, expected_revision: 4,
    }, { reconciliation: undefined });
  });
});

describe("one-time sign-in reveal", () => {
  test("reset reveals the username and new password once, with a combined copy control", async () => {
    mocks.authored.mockResolvedValue({ reset_user_password: { username: "ada", password: "once-only" } });
    mocks.reveal.mockResolvedValue(undefined);
    const { result } = renderHook(useResetPasswordAction, { wrapper });
    expect(result.current.visibleWhen?.(record)).toBe(true);
    expect(result.current.confirm).toBeTruthy();
    expect(mocks.options).toHaveBeenCalledWith(expect.objectContaining({ transient: true }));
    await act(async () => { await result.current.run?.(context()); });
    expect(mocks.authored).toHaveBeenCalledExactlyOnceWith({ id: "user-1", confirmed: true, expectedRevision: 4 }, { reconciliation: undefined });
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
    const { result } = renderHook(useIssuePasswordAction, { wrapper });
    expect(result.current.visibleWhen?.(record)).toBe(false);
    expect(result.current.visibleWhen?.({ ...record, account_actions: ["ISSUE_PASSWORD"] })).toBe(true);
    await act(async () => { await result.current.run?.(context()); });
    expect(mocks.authored).toHaveBeenCalledExactlyOnceWith({ id: "user-1" }, { reconciliation: undefined });
    expect(mocks.reveal).toHaveBeenCalledWith(expect.objectContaining({ secrets: [
      expect.objectContaining({ value: "grace" }),
      expect.objectContaining({ value: "first-secret" }),
    ] }));
  });

  test("an empty result never opens a reveal prompt", async () => {
    mocks.authored.mockResolvedValue(null);
    const { result } = renderHook(useResetPasswordAction, { wrapper });
    await expect(result.current.run?.(context())).rejects.toThrow();
    expect(mocks.reveal).not.toHaveBeenCalled();
  });
});
