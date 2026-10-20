import { describe, expect, test } from "vitest";

import { subjectUserId, userSubject } from "./subjects";

describe("user subjects", () => {
  test("round-trips an account id through its subject", () => {
    expect(userSubject("usr_1")).toBe("auth/user:usr_1");
    expect(subjectUserId("auth/user:usr_1")).toBe("usr_1");
  });

  test("reads no account from another subject kind", () => {
    expect(subjectUserId("auth/group:grp_1")).toBeNull();
  });
});
