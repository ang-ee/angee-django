import { describe, expect, test } from "vitest";

import { stableKey, stableSerialize } from "./stable-deps";

describe("stable serialization", () => {
  test("preserves undefined where react-query's stable key coalesces it with null", () => {
    expect(stableKey(undefined)).toBe(stableKey(null));
    expect(stableSerialize(undefined)).not.toBe(stableSerialize(null));
  });

  test("ignores object insertion order", () => {
    expect(stableSerialize({ first: 1, second: 2 })).toBe(
      stableSerialize({ second: 2, first: 1 }),
    );
  });
});
