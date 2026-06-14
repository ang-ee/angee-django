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

  test("registers the console account-connect callback route", () => {
    const route = iam.routes?.find((item) => item.name === "iam.connect.callback");
    const legacyRoute = iam.routes?.find(
      (item) => item.name === "iam.connect.callback.legacy",
    );
    expect(route?.path).toBe("/callback");
    expect(route?.shell).toBe("console");
    expect(route?.component).toBeTypeOf("function");
    expect(legacyRoute?.path).toBe("/iam/oauth/callback");
    expect(legacyRoute?.shell).toBe("console");
    expect(legacyRoute?.component).toBe(route?.component);
  });

  test("registers the console routes, with $id detail children for the DataPages", () => {
    const names = iam.routes?.map((route) => route.name) ?? [];
    // The federation/users DataPages each contribute a list + a `$id` record route.
    for (const name of [
      "iam.overview",
      "iam.users",
      "iam.users.record",
      "iam.roles",
      "iam.grants",
      "iam.relationships",
      "iam.schema",
      "iam.providers",
      "iam.providers.record",
      "iam.accounts",
      "iam.accounts.record",
      "iam.credentials",
      "iam.credentials.record",
    ]) {
      expect(names).toContain(name);
    }
    expect(names).not.toContain("iam.connections");
    const record = iam.routes?.find((route) => route.name === "iam.providers.record");
    expect(record?.path).toBe("/iam/providers/$id");
    expect(record?.parent).toBe("iam.providers");
    expect(record?.component).toBeUndefined();
  });

  test("contributes the IAM console menu with Roles and Federation dropdowns", () => {
    const menu = iam.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("iam");
    expect(menu?.label).toBe("IAM");
    // Route-less root: target inherited from the first child (Overview).
    expect(menu?.route).toBeUndefined();
    expect(menu?.children?.map((item) => item.id)).toEqual([
      "iam.overview",
      "iam.users",
      "iam.roles.group",
      "iam.federation",
    ]);
    const rolesGroup = menu?.children?.find((item) => item.id === "iam.roles.group");
    expect(rolesGroup?.route).toBeUndefined();
    expect(rolesGroup?.children?.map((item) => item.route)).toEqual([
      "iam.roles",
      "iam.grants",
      "iam.relationships",
      "iam.schema",
    ]);
    const federation = menu?.children?.find((item) => item.id === "iam.federation");
    expect(federation?.children?.map((item) => item.route)).toEqual([
      "iam.providers",
      "iam.accounts",
      "iam.credentials",
    ]);
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
