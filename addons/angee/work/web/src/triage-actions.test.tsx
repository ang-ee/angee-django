// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("@angee/ui", () => ({
  ActionFormDialog: () => null,
  Button: () => null,
  Glyph: () => null,
  defineRowAction: (declaration: unknown) => declaration,
  relationValueId: () => null,
  useAuthoredResourceMutation: () => [vi.fn()],
  useRecordChromeContext: () => ({ record: null, recordId: "" }),
}));

vi.mock("./i18n", () => ({
  useWorkT: () => (key: string) => key,
}));

vi.mock("./task-work", () => ({
  isTaskInTriage: () => true,
}));

import { useTriageActions } from "./triage-actions";

describe("triage action relation scopes", () => {
  test("offers only custom queue stages and live canonical tasks", () => {
    const { result } = renderHook(() => useTriageActions("que_eng"));
    const accept = result.current.find((action) => action.id === "work-accept-task");
    const duplicate = result.current.find(
      (action) => action.id === "work-duplicate-task",
    );

    expect(accept?.args?.[0]).toMatchObject({
      filters: [
        { field: "queue", operator: "eq", value: "que_eng" },
        { field: "category", operator: "ne", value: "triage" },
        { field: "category", operator: "ne", value: "duplicate" },
      ],
    });
    expect(duplicate?.args?.[0]).toMatchObject({
      filters: [
        { field: "queue", operator: "eq", value: "que_eng" },
        { field: "status", operator: "ne", value: "DROPPED" },
      ],
    });
  });
});
