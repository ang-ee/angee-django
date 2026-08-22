import { describe, expect, test } from "vitest";

import { canCloseCycle } from "./cycle-actions";

describe("canCloseCycle", () => {
  test("keeps completed and future cycles out of the facilitator close action", () => {
    expect(canCloseCycle({ id: "done", completed_at: "2026-08-20T10:00:00Z" })).toBe(
      false,
    );
    expect(canCloseCycle({ id: "future", starts_on: "2999-01-01" })).toBe(false);
    expect(canCloseCycle({ id: "current", starts_on: "2000-01-01" })).toBe(true);
  });
});
