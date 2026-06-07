import {
  AUTH_LOGIN_METHOD_SLOT,
  type BaseMenuItem,
} from "@angee/base";
import { describe, expect, test } from "vitest";

import iam from "./index";

describe("iam addon manifest", () => {
  test("registers the public login callback route", () => {
    const route = iam.routes?.find((item) => item.name === "iam.login.callback");
    const legacyRoute = iam.routes?.find(
      (item) => item.name === "iam.login.callback.legacy",
    );
    expect(iam.routes).toHaveLength(9);
    expect(route?.name).toBe("iam.login.callback");
    expect(route?.path).toBe("/sso/callback");
    expect(route?.shell).toBe("public");
    expect(route?.component).toBeTypeOf("function");
    expect(legacyRoute?.path).toBe("/login/callback");
    expect(legacyRoute?.shell).toBe("public");
    expect(legacyRoute?.component).toBe(route?.component);
  });

  test("registers the Identity console routes", () => {
    expect(iam.routes?.map((route) => route.name)).toEqual([
      "iam.login.callback",
      "iam.login.callback.legacy",
      "iam.overview",
      "iam.users",
      "iam.roles",
      "iam.grants",
      "iam.relationships",
      "iam.schema",
      "iam.connections",
    ]);
    expect(iam.routes?.slice(2).map((route) => route.shell)).toEqual([
      "console",
      "console",
      "console",
      "console",
      "console",
      "console",
      "console",
    ]);
  });

  test("contributes the Identity console menu", () => {
    const menu = iam.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("iam");
    expect(menu?.label).toBe("Identity");
    expect(menu?.children?.map((item) => item.route)).toEqual([
      "iam.overview",
      "iam.users",
      "iam.roles",
      "iam.grants",
      "iam.relationships",
      "iam.schema",
      "iam.connections",
    ]);
    expect(menu?.children?.map((item) => item.to)).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
  });

  test("contributes OAuth methods to the login method slot", () => {
    const slot = iam.slots?.[0];
    expect(iam.slots).toHaveLength(1);
    expect(slot?.slot).toBe(AUTH_LOGIN_METHOD_SLOT);
    expect(slot?.id).toBe("iam.oauth-login");
    expect(slot?.content).toBeDefined();
  });
});
