import { describe, expect, test } from "vitest";

import { closeOutcome, openingPolicyMessageKey } from "./round-actions";

describe("round ceremony presentation", () => {
  test.each([
    ["FACILITATOR_ONLY", "round.action.open.facilitatorOnly"],
    ["answers", "round.action.open.answers"],
    ["ANSWERS_AND_TRACKS", "round.action.open.answersAndTracks"],
    [null, "round.action.open.unknown"],
  ])("maps opening policy %s to its confirmation copy", (policy, key) => {
    expect(openingPolicyMessageKey(policy)).toBe(key);
  });
});

describe("round close outcome validation", () => {
  const options = [{ value: "DEFERRED", label: "Deferred" }];

  test("accepts a newly authored dialog option without a second member list", () => {
    expect(closeOutcome(options, "DEFERRED", "invalid")).toBe("DEFERRED");
  });

  test.each([undefined, "UNKNOWN"])("rejects absent or undeclared value %s", (value) => {
    expect(() => closeOutcome(options, value, "invalid")).toThrow("invalid");
  });
});
