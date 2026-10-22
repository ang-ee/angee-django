import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import oidc from "./index";

describe("iam-integrate-oidc addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(oidc)).not.toThrow();
    expect(() => composeAddons([oidc], {
      canonicalModelLabel: (label) => label,
    })).not.toThrow();
  });

  test("preserves the public sign-in callback route", () => {
    const route = oidc.routes?.find((item) => item.name === "iam-integrate-oidc.login.callback");
    expect(route?.path).toBe("/sso/callback");
    expect(route?.layout).toBe("public");
    expect(route?.component).toBeTypeOf("function");
  });

  test("contributes OIDC sign-in methods and OAuth client policy", () => {
    expect(oidc.containers?.["auth.login#method"]).toMatchObject({
      "iam-integrate-oidc.oauth-login": { content: expect.anything() },
    });
    expect(oidc.containers?.["integrate.OAuthClient#sections"]).toMatchObject({
      "iam-integrate-oidc.oidc-login": { sequence: 10, content: expect.anything() },
    });
  });
});
