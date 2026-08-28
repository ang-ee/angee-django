import { describe, expect, test } from "vitest";

import { openingPolicyMessageKey } from "./round-actions";

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
