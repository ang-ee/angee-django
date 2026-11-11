import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { MenuTree } from "@angee/ui";
import { describe, expect, test } from "vitest";

import iam, { IAM_LOGIN_BACKGROUND_IMAGE_URLS } from "./index";

const declaredMenus = composeAddons([{ id: iam.id, menus: iam.menus, routes: iam.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("iam addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(iam)).not.toThrow();
  });

  test("offers the Users entry only to people managers", () => {
    expect(iam.menus["iam.users"]?.requires).toBe("iam.User#list");
  });

  test("references the landing route from exactly one menu item (chrome derivation)", () => {
    // Regression: a route-ful root + an Overview child both pointing at
    // iam.overview makes createApp throw "referenced by multiple menu items".
    const tree = MenuTree.from(declaredMenus);
    expect(tree.itemsForRoute("iam.overview")).toHaveLength(1);
  });

  test("gates every account verb and the last sign-in column on the user's projection", () => {
    // Account verbs read the row's projected admission and revision.
    const verbs = iam.containers?.["iam.User#actions-menu"] as Record<string, { requiredFields?: readonly string[] }>;
    expect(Object.keys(verbs)).not.toHaveLength(0);
    for (const child of Object.values(verbs)) {
      expect(child.requiredFields).toEqual(expect.arrayContaining(["account_actions", "revision"]));
    }
    // Last sign-in is present only for viewers reading it on every row; the field gate still decides each value.
    const columns = iam.containers?.["iam.users#columns"] as Record<string, { requires?: string }>;
    expect(Object.entries(columns).flatMap(([id, child]) => child.requires ? [[id, child.requires]] : []))
      .toEqual([["iam.last-login", "iam.User#read__last_login"]]);
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
