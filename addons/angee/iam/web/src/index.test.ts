import {
  AUTH_LOGIN_METHOD_SLOT,
  MenuTree,
  type BaseMenuItem,
  type ChromeMenuItem,
} from "@angee/base";
import { describe, expect, test } from "vitest";

import iam from "./index";

describe("iam addon manifest", () => {
  test("registers the public login callback route", () => {
    const route = iam.routes?.find((item) => item.name === "iam.login.callback");
    const legacyRoute = iam.routes?.find(
      (item) => item.name === "iam.login.callback.legacy",
    );
    expect(route?.name).toBe("iam.login.callback");
    expect(route?.path).toBe("/sso/callback");
    expect(route?.shell).toBe("public");
    expect(route?.component).toBeTypeOf("function");
    expect(legacyRoute?.path).toBe("/login/callback");
    expect(legacyRoute?.shell).toBe("public");
    expect(legacyRoute?.component).toBe(route?.component);
  });

  test("registers the console routes, with $id detail children for the DataPages", () => {
    const names = iam.routes?.map((route) => route.name) ?? [];
    // The users/OIDC-providers DataPages each contribute a list + a `$id` record route.
    for (const name of [
      "iam.overview",
      "iam.users",
      "iam.users.record",
      "iam.roles",
      "iam.grants",
      "iam.relationships",
      "iam.schema",
      "iam.oidc",
      "iam.oidc.record",
    ]) {
      expect(names).toContain(name);
    }
    // The OAuth connect substrate (providers/accounts/credentials + connect callback)
    // moved to @angee/integrate.
    for (const gone of [
      "iam.providers",
      "iam.accounts",
      "iam.credentials",
      "iam.connect.callback",
    ]) {
      expect(names).not.toContain(gone);
    }
    const record = iam.routes?.find((route) => route.name === "iam.oidc.record");
    expect(record?.path).toBe("/iam/oidc/$id");
    expect(record?.parent).toBe("iam.oidc");
    expect(record?.component).toBeUndefined();
  });

  test("contributes the IAM console menu with a Roles dropdown and OIDC Providers", () => {
    const menu = iam.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("iam");
    expect(menu?.label).toBe("IAM");
    // Route-less root: target inherited from the first child (Overview).
    expect(menu?.route).toBeUndefined();
    expect(menu?.children?.map((item) => item.id)).toEqual([
      "iam.overview",
      "iam.users",
      "iam.roles.group",
      "iam.oidc",
    ]);
    const rolesGroup = menu?.children?.find((item) => item.id === "iam.roles.group");
    expect(rolesGroup?.route).toBeUndefined();
    expect(rolesGroup?.children?.map((item) => item.route)).toEqual([
      "iam.roles",
      "iam.grants",
      "iam.relationships",
      "iam.schema",
    ]);
    const oidc = menu?.children?.find((item) => item.id === "iam.oidc");
    expect(oidc?.route).toBe("iam.oidc");
  });

  test("references the landing route from exactly one menu item (chrome derivation)", () => {
    // Regression: a route-ful root + an Overview child both pointing at
    // iam.overview makes createApp throw "referenced by multiple menu items".
    const tree = MenuTree.from(iam.menus as readonly ChromeMenuItem[]);
    expect(tree.itemsForRoute("iam.overview")).toHaveLength(1);
  });

  test("contributes OAuth methods to the login method slot", () => {
    const slot = iam.slots?.[0];
    expect(iam.slots).toHaveLength(1);
    expect(slot?.slot).toBe(AUTH_LOGIN_METHOD_SLOT);
    expect(slot?.id).toBe("iam.oauth-login");
    expect(slot?.content).toBeDefined();
  });
});
