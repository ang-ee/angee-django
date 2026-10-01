// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { useIssuePasswordAction } from "./password-actions";

const mocks = vi.hoisted(() => ({ issue: vi.fn(), prompt: vi.fn(), options: vi.fn() }));
vi.mock("@angee/refine", async (original) => ({
  ...await original<object>(),
  useAuthoredMutation: (_document: unknown, options: unknown) => {
    mocks.options(options); return [mocks.issue, {}];
  },
}));
vi.mock("@angee/ui", async (original) => ({ ...await original<object>(), usePrompt: () => mocks.prompt }));
beforeEach(() => vi.clearAllMocks());

test("IAM reveals a transient issued password only through the prompt", async () => {
  mocks.issue.mockResolvedValue({ issue_user_password: { password: "once-only" } });
  mocks.prompt.mockResolvedValue(null);
  const { result } = renderHook(useIssuePasswordAction);
  expect(result.current.visibleWhen?.({ can_issue_password: false })).toBe(false);
  expect(result.current.visibleWhen?.({ can_issue_password: true })).toBe(true);
  expect(mocks.options).toHaveBeenCalledWith(expect.objectContaining({ transient: true }));
  await act(async () => result.current.run?.({
    record: { id: "user-1" }, values: {}, refresh: vi.fn(), update: vi.fn(), prompt: mocks.prompt,
  }));
  expect(mocks.issue).toHaveBeenCalledExactlyOnceWith({ id: "user-1" });
  expect(mocks.prompt).toHaveBeenCalledWith(expect.objectContaining({ fields: [expect.objectContaining({
    defaultValue: "once-only", readOnly: true, copyable: true,
  })] }));
});

test("an empty issuance result never opens a reveal prompt", async () => {
  mocks.issue.mockResolvedValue(null);
  const { result } = renderHook(useIssuePasswordAction);
  await expect(result.current.run?.({ record: { id: "user-1" }, values: {}, refresh: vi.fn(),
    update: vi.fn(), prompt: mocks.prompt })).rejects.toThrow();
  expect(mocks.prompt).not.toHaveBeenCalled();
});
