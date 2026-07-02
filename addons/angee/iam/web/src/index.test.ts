import { AUTH_LOGIN_METHOD_SLOT } from "@angee/app/auth";
import { expectValidBaseAddon } from "@angee/app/testing";
import {
  formViewSectionsSlot, MenuTree, type BaseMenuItem, type ChromeMenuItem } from "@angee/ui";
import { describe, expect, test } from "vitest";

import iam, { IAM_LOGIN_BACKGROUND_IMAGE_URLS } from "./index";

describe("iam addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(iam)).not.toThrow();
  });

  test("registers the public login callback route", () => {
    const route = iam.routes?.find((item) => item.name === "iam.login.callback");
    expect(route?.name).toBe("iam.login.callback");
    expect(route?.path).toBe("/sso/callback");
    expect(route?.layout).toBe("public");
    expect(route?.component).toBeTypeOf("function");
  });

  test("registers the console routes, with $id detail children for the ResourceLists", () => {
    const names = iam.routes?.map((route) => route.name) ?? [];
    // The Users ResourceList contributes a list + a `$id` record route. (OIDC login is
    // now a tab on integrate's OAuth client form, not a separate iam page.)
    for (const name of [
      "iam.overview",
      "iam.users",
      "iam.users.record",
      "iam.groups",
      "iam.groups.record",
      "iam.roles",
      "iam.grants",
      "iam.relationships",
      "iam.schema",
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
    const record = iam.routes?.find((route) => route.name === "iam.users.record");
    expect(record?.path).toBe("/iam/users/$id");
    expect(record?.parent).toBe("iam.users");
    expect(record?.component).toBeUndefined();
    const groupRecord = iam.routes?.find((route) => route.name === "iam.groups.record");
    expect(groupRecord?.path).toBe("/iam/groups/$id");
    expect(groupRecord?.parent).toBe("iam.groups");
    expect(names).not.toContain("iam.permissions");
  });

  test("contributes the IAM console menu with a Roles dropdown", () => {
    const menu = iam.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("iam");
    expect(menu?.label).toBe("IAM");
    // Route-less root: target inherited from the first child (Overview).
    expect(menu?.route).toBeUndefined();
    expect(menu?.children?.map((item) => item.id)).toEqual([
      "iam.overview",
      "iam.users.group",
      "iam.roles.group",
    ]);
    const usersGroup = menu?.children?.find((item) => item.id === "iam.users.group");
    expect(usersGroup?.route).toBeUndefined();
    expect(usersGroup?.children?.map((item) => item.route)).toEqual([
      "iam.users",
      "iam.groups",
    ]);
    const rolesGroup = menu?.children?.find((item) => item.id === "iam.roles.group");
    expect(rolesGroup?.route).toBeUndefined();
    expect(rolesGroup?.children?.map((item) => item.route)).toEqual([
      "iam.roles",
      "iam.grants",
      "iam.relationships",
      "iam.schema",
    ]);
  });

  test("references the landing route from exactly one menu item (chrome derivation)", () => {
    // Regression: a route-ful root + an Overview child both pointing at
    // iam.overview makes createApp throw "referenced by multiple menu items".
    const tree = MenuTree.from(iam.menus as readonly ChromeMenuItem[]);
    expect(tree.itemsForRoute("iam.overview")).toHaveLength(1);
  });

  test("contributes the login methods and the OIDC tab on the OAuth client form", () => {
    expect(iam.slots).toHaveLength(2);
    const login = iam.slots?.[0];
    expect(login?.slot).toBe(AUTH_LOGIN_METHOD_SLOT);
    expect(login?.id).toBe("iam.oauth-login");
    expect(login?.content).toBeDefined();
    // The OIDC login tab the iam addon adds to integrate's OAuth client form.
    const oidc = iam.slots?.[1];
    expect(oidc?.slot).toBe(formViewSectionsSlot("OAuthClient"));
    expect(oidc?.id).toBe("iam.oidc-login");
    expect(oidc?.content).toBeDefined();
  });

  test("publishes login backgrounds as frontend build assets", () => {
    expect(IAM_LOGIN_BACKGROUND_IMAGE_URLS).toHaveLength(6);
    expect(IAM_LOGIN_BACKGROUND_IMAGE_URLS.map((url) => basename(new URL(url).pathname))).toEqual([
      "angee-children-build.webp",
      "angee-children-future.webp",
      "angee-future-city.webp",
      "angee-pond-walk.webp",
      "angee-vision-cinimatic.webp",
      "angee-vision-daytime.webp",
    ]);
    expect(IAM_LOGIN_BACKGROUND_IMAGE_URLS.every((url) => url.startsWith("/static/"))).toBe(false);
  });
});

function basename(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}
