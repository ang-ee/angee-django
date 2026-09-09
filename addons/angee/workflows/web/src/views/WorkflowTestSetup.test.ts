import { describe, expect, test } from "vitest";

import { fixtureInput, parseWorkflowTestValues } from "./WorkflowTestSetup";

describe("workflow test setup", () => {
  test("keeps omitted input distinct from explicit null", () => {
    expect(parseWorkflowTestValues({}, false)).toEqual({ inputPresent: false, input: undefined, fixtures: [] });
    expect(parseWorkflowTestValues({ input: null }, false)).toEqual({ inputPresent: true, input: null, fixtures: [] });
  });

  test("requires the declared subject without inventing one for unbound workflows", () => {
    expect(parseWorkflowTestValues({ input: { enabled: true } }, false)).toEqual({
      inputPresent: true,
      input: { enabled: true },
      fixtures: [],
    });
    expect(() => parseWorkflowTestValues({ subject: "" }, true)).toThrow();
  });

  test("keeps manual fixture presence and captured evidence mutually exclusive", () => {
    expect(fixtureInput({
      stepKey: "lookup", role: "OUTPUT", mode: "manual", valuePresent: true,
      value: null, outcome: "missing",
    })).toEqual({ step_key: "lookup", role: "OUTPUT", value: null, outcome: "missing" });
    expect(fixtureInput({
      stepKey: "lookup", role: "OUTPUT", mode: "captured", valuePresent: false,
      capturedAttempt: "wsa_prior", outcome: "ignored",
    })).toEqual({ step_key: "lookup", role: "OUTPUT", captured_attempt: "wsa_prior" });
  });
});
