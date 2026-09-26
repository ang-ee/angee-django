// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({ navigate: vi.fn(), settle: vi.fn() }));

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, { useActionResultRun: () => mocks.settle });
});
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => mocks.navigate }));

import { useWorkflowSubjectActionResult } from "./useWorkflowSubjectActionResult";

beforeEach(() => {
  mocks.navigate.mockClear();
  mocks.settle.mockReset();
});

test("follows a successful returned Run while preserving unrelated subject search", async () => {
  mocks.settle.mockResolvedValue({ ok: true, message: "Started", id: "run-new" });
  const { result } = renderHook(() => useWorkflowSubjectActionResult());

  await act(() => result.current(async () => undefined));

  const navigation = mocks.navigate.mock.calls[0]![0];
  expect(navigation.to).toBe(".");
  expect(navigation.replace).toBe(true);
  expect(navigation.search({ decision: "old", filter: "mine" })).toEqual({
    chatterTab: "workflows", filter: "mine", workflowRun: "run-new",
  });
});

test.each([
  ["domain failure", { ok: false, message: "Denied", id: "run-failed" }],
  ["empty result", undefined],
])("leaves subject selection untouched after %s", async (_label, outcome) => {
  mocks.settle.mockResolvedValue(outcome);
  const { result } = renderHook(() => useWorkflowSubjectActionResult());

  await act(() => result.current(async () => undefined));

  expect(mocks.navigate).not.toHaveBeenCalled();
});

test("leaves subject selection untouched when the settlement owner absorbs a transport failure", async () => {
  mocks.settle.mockResolvedValue(undefined);
  const { result } = renderHook(() => useWorkflowSubjectActionResult());

  await act(() => result.current(async () => { throw new Error("offline"); }));

  expect(mocks.navigate).not.toHaveBeenCalled();
});
