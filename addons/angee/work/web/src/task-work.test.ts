import { describe, expect, test } from "vitest";

import { isTaskInTriage } from "./task-work";

describe("isTaskInTriage", () => {
  test("uses lifecycle timestamps rather than a translated stage label", () => {
    expect(
      isTaskInTriage({ id: "task-1", started_triage_at: "2026-08-22T08:00:00Z" }),
    ).toBe(true);
    expect(
      isTaskInTriage({
        id: "task-2",
        started_triage_at: "2026-08-22T08:00:00Z",
        triaged_at: "2026-08-22T09:00:00Z",
      }),
    ).toBe(false);
    expect(isTaskInTriage({ id: "task-3" })).toBe(false);
  });
});
