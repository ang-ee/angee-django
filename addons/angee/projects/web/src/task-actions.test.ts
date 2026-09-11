import { describe, expect, test } from "vitest";

import { dropReason } from "./task-actions";

describe("task drop reason validation", () => {
  const options = [{ value: "DEFERRED", label: "Deferred" }];

  test("accepts a newly declared metadata option without a second member list", () => {
    expect(dropReason(options, "DEFERRED")).toBe("DEFERRED");
  });

  test.each([undefined, "UNKNOWN"])("rejects absent or undeclared value %s", (value) => {
    expect(() => dropReason(options, value)).toThrow(/invalid enum value/);
  });
});
