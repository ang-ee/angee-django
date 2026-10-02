import { describe, expect, test } from "vitest";

import { closeOutcome, openingPolicyLabelKey, openingPolicyMessageKey } from "./round-actions";

describe("round ceremony presentation", () => {
  test.each([
    ["FACILITATOR_ONLY", "round.action.open.facilitatorOnly"],
    ["answers", "round.action.open.answers"],
    ["ANSWERS_AND_TRACKS", "round.action.open.answersAndTracks"],
    ["DRAFTS_AND_TRACKS", "round.action.open.draftsAndTracks"],
    [null, "round.action.open.unknown"],
  ])("maps opening policy %s to its confirmation copy", (policy, key) => {
    expect(openingPolicyMessageKey(policy)).toBe(key);
  });

  test("shares the policy label with the widening action and Share visibility", () => {
    expect(openingPolicyLabelKey("answers_and_tracks")).toBe("round.policy.answersAndTracks");
    expect(openingPolicyLabelKey("FACILITATOR_ONLY")).toBe("round.policy.facilitatorOnly");
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
