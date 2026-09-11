import { describe, expect, test } from "vitest";

import { userDisplayName } from "./identity-labels";

describe("userDisplayName", () => {
  test.each([
    [{ display_name: "Ada", username: "ada", email: "ada@example.com" }, "Ada"],
    [{ username: "ada", email: "ada@example.com" }, "ada"],
    [{ email: "ada@example.com" }, "ada@example.com"],
    [{}, "Unknown user"],
  ])("uses the IAM display fallback order", (user, expected) => {
    expect(userDisplayName(user, "Unknown user")).toBe(expected);
  });
});
