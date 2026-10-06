import { describe, expect, test } from "vitest";

import { dropReason, offersTaskAction } from "./task-actions";

describe("task verb admission", () => {
  test("reads only the row's server projection", () => {
    expect(offersTaskAction({ id: "t", task_actions: ["drop", "reopen"] }, "drop")).toBe(true);
    expect(offersTaskAction({ id: "t", task_actions: ["reopen"] }, "drop")).toBe(false);
    expect(offersTaskAction({ id: "t", status: "OPEN", permissions: ["write"] }, "drop")).toBe(false);
  });
});

describe("task drop reason validation", () => {
  const options = [{ value: "DEFERRED", label: "Deferred" }];

  test("accepts a newly declared metadata option without a second member list", () => {
    expect(dropReason(options, "DEFERRED")).toBe("DEFERRED");
  });

  test.each([undefined, "UNKNOWN"])("rejects absent or undeclared value %s", (value) => {
    expect(() => dropReason(options, value)).toThrow(/invalid enum value/);
  });
});
