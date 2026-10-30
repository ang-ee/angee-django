import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import tags from "./index";

const declaredMenus = composeAddons([{ id: tags.id, menus: tags.menus, routes: tags.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("angee.tags addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(tags)).not.toThrow();
  });

  test("uses the Tags route-name convention and names the list resource", () => {
    expect((tags.routes ?? []).map((route) => route.name)).toEqual([
      "tags.tags",
      "tags.tag",
    ]);
    const resources = (tags.routes ?? [])
      .map((route) => route.resource)
      .filter((resource): resource is string => Boolean(resource));
    expect(resources).toEqual(["tags.Tag"]);
  });

  test("contributes Tags as a Settings category", () => {
    const root = declaredMenus?.[0];
    expect(root).toMatchObject({
      id: "tags",
      label: "Tags",
      group: "platform",
    });
    expect(root?.children?.[0]).toMatchObject({
      id: "tags.tags",
      label: "Tags",
      route: "tags.tags",
    });
  });

  test("registers the tag form, independent record widget and Tags page's containers", () => {
    expect(Object.keys(tags.widgets ?? {})).toEqual(["angee.tags.recordTags"]);
    expect(Object.keys(tags.forms ?? {})).toEqual(["tags.Tag"]);
    expect(Object.keys(tags.containers ?? {})).toEqual(["tags.tags#facets", "tags.tags#columns", "tags.tags#fields"]);
  });
});
