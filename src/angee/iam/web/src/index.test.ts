import { AUTH_LOGIN_METHOD_SLOT } from "@angee/base";
import { describe, expect, test } from "vitest";

import iam from "./index";

describe("iam addon manifest", () => {
  test("registers the public login callback route", () => {
    const route = iam.routes?.[0];
    expect(iam.routes).toHaveLength(1);
    expect(route?.name).toBe("iam.login.callback");
    expect(route?.path).toBe("/login/callback");
    expect(route?.shell).toBe("public");
    expect(route?.component).toBeTypeOf("function");
  });

  test("contributes OAuth methods to the login method slot", () => {
    const slot = iam.slots?.[0];
    expect(iam.slots).toHaveLength(1);
    expect(slot?.slot).toBe(AUTH_LOGIN_METHOD_SLOT);
    expect(slot?.id).toBe("iam.oauth-login");
    expect(slot?.content).toBeDefined();
  });
});
