// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { useSendSignInLinkAction } from "./account-actions";

const mocks = vi.hoisted(() => ({ issue: vi.fn(), options: vi.fn() }));
vi.mock("@angee/ui", async (original) => ({
  ...await original<object>(),
  useActionResultMutation: (field: string, options: unknown) => {
    mocks.options(field, options);
    return [mocks.issue, {}];
  },
}));
beforeEach(() => vi.clearAllMocks());

const record = { id: "user-1", revision: 4, account_actions: ["rename", "issue_sign_in_link"] };

describe("Send sign-in link follows the row's server projection", () => {
  test("is offered only where the projection names the verb, and confirms first", () => {
    const { result } = renderHook(useSendSignInLinkAction);
    expect(result.current.visibleWhen?.(record)).toBe(true);
    expect(result.current.visibleWhen?.({ ...record, account_actions: ["rename"] })).toBe(false);
    expect(result.current.visibleWhen?.({ id: "user-2" })).toBe(false);
    expect(result.current.confirm?.title).toBe("Send a sign-in link?");
  });

  test("emails the open account through the console verb, never returning the link", async () => {
    mocks.issue.mockResolvedValue({ ok: true, message: "Sign-in link sent to the account's email." });
    const { result } = renderHook(useSendSignInLinkAction);
    expect(mocks.options).toHaveBeenCalledWith("issue_user_sign_in_link", { invalidateModels: ["iam.User"] });
    await act(async () => {
      await result.current.run?.({ record, values: {}, refresh: vi.fn(), update: vi.fn(), prompt: vi.fn() });
    });
    expect(mocks.issue).toHaveBeenCalledExactlyOnceWith("user-1");
  });
});
