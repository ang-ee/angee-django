import { describe, expect, test } from "vitest";

import { avatarInitials } from "./avatar";

describe("avatarInitials", () => {
  test("uses the first letter of the first two words", () => {
    expect(avatarInitials("Ada Lovelace")).toBe("AL");
    expect(avatarInitials("  grace   hopper  ")).toBe("GH");
  });

  test("falls back for blank labels", () => {
    expect(avatarInitials("")).toBe("?");
    expect(avatarInitials("   ")).toBe("?");
  });
});
