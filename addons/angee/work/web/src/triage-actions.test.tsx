// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("@angee/projects", () => ({
  TASK_MODEL: "projects.Task",
}));

vi.mock("@angee/ui", () => ({
  ActionFormDialog: () => null,
  RecordActionTrigger: () => null,
  canonicalOptionValue: (
    options: readonly { value: string }[],
    value: unknown,
  ) => options.find((option) => option.value === value)?.value,
  defineRowAction: (declaration: unknown) => declaration,
  relationValueId: () => null,
  useActionOutcomeMutation: () => [vi.fn()],
  useAuthoredResourceMutation: () => [vi.fn()],
  useRecordChromeContext: () => ({ record: null, recordId: "" }),
  useRecordChromeActionMutation: () => [vi.fn(), { fetching: false }],
}));

vi.mock("./context", () => ({
  useQueueContext: () => ({ data: null }),
  useTaskContext: () => ({ data: null }),
}));

vi.mock("./i18n", () => ({
  useWorkT: () => (key: string) => key,
}));

vi.mock("./task-work", () => ({
  isTaskInTriage: () => true,
}));

import { declineReason, useTriageActions } from "./triage-actions";

/** Triage verbs declare their argument list statically, never per form context. */
function staticArgs(args: ReturnType<typeof useTriageActions>[number]["args"]): readonly unknown[] | undefined {
  if (typeof args === "function" || (args && !Array.isArray(args))) throw new Error("Expected a static argument list");
  return args as readonly unknown[] | undefined;
}

describe("triage action relation scopes", () => {
  test("offers only custom queue stages and live canonical tasks", () => {
    const { result } = renderHook(() => useTriageActions("que_eng"));
    const accept = result.current.find((action) => action.id === "work-accept-task");
    const duplicate = result.current.find(
      (action) => action.id === "work-duplicate-task",
    );

    expect(staticArgs(accept?.args)?.[0]).toMatchObject({
      filters: [
        { field: "queue", operator: "eq", value: "que_eng" },
        { field: "category", operator: "ne", value: "triage" },
        { field: "category", operator: "ne", value: "duplicate" },
        { field: "rule_owned", operator: "eq", value: false },
        { field: "conceals", operator: "eq", value: false },
      ],
    });
    expect(staticArgs(duplicate?.args)?.[0]).toMatchObject({
      filters: [
        { field: "queue", operator: "eq", value: "que_eng" },
        { field: "status", operator: "ne", value: "DROPPED" },
      ],
    });
  });
});

describe("triage decline reason validation", () => {
  const options = [{ value: "DEFERRED", label: "Deferred" }];

  test("accepts a newly authored dialog option without a second member list", () => {
    expect(declineReason(options, "DEFERRED")).toBe("DEFERRED");
  });

  test.each([undefined, "UNKNOWN"])("rejects absent or undeclared value %s", (value) => {
    expect(() => declineReason(options, value)).toThrow(/required/);
  });
});
