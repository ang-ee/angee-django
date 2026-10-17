import { describe, expect, test } from "vitest";

import { entryTone, historyValue, statementVerb } from "./history-entries";

describe("historyValue", () => {
  test("renders missing and blank values as empty", () => {
    expect(historyValue(null)).toEqual({ kind: "empty" });
    expect(historyValue(undefined)).toEqual({ kind: "empty" });
    expect(historyValue("")).toEqual({ kind: "empty" });
  });

  test("renders scalars as text and structures as JSON", () => {
    expect(historyValue("Final title")).toEqual({ kind: "text", text: "Final title" });
    expect(historyValue(3)).toEqual({ kind: "text", text: "3" });
    expect(historyValue(false)).toEqual({ kind: "text", text: "false" });
    expect(historyValue({ a: [1] })).toEqual({ kind: "json", text: JSON.stringify({ a: [1] }, null, 2) });
  });
});

describe("statementVerb", () => {
  test("a first version asserts, a later applied version applies", () => {
    expect(statementVerb({ history_type: "CREATED", status: "ASSERTED", applied: false })).toBe("asserted");
    expect(statementVerb({ history_type: "CHANGED", status: "ASSERTED", applied: true })).toBe("applied");
    expect(statementVerb({ history_type: "CHANGED", status: "ASSERTED", applied: false })).toBe("asserted");
  });

  test("a terminal status speaks for itself, whatever the version", () => {
    expect(statementVerb({ history_type: "CHANGED", status: "WITHDRAWN", applied: false })).toBe("withdrawn");
    expect(statementVerb({ history_type: "CREATED", status: "QUARANTINED", applied: false })).toBe("quarantined");
  });
});

describe("entryTone", () => {
  test("failures, reverts and merges stand apart from ordinary edits", () => {
    expect(entryTone({ activity: "MANUAL", outcome: "FAILURE" })).toBe("danger");
    expect(entryTone({ activity: "REVERT", outcome: "SUCCESS" })).toBe("warning");
    expect(entryTone({ activity: "MERGE", outcome: "SUCCESS" })).toBe("info");
    expect(entryTone({ activity: "PULL", outcome: null })).toBe("success");
  });
});
