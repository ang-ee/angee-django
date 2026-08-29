import { expectValidBaseAddon } from "@angee/app/testing";
import type { BaseMenuItem } from "@angee/ui";
import { describe, expect, test } from "vitest";

import tags from "./index";

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
    const root = tags.menus?.[0] as BaseMenuItem | undefined;
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

  test("contributes the record-scoped Tags chatter tab", () => {
    expect((tags.chatter ?? []).map((tab) => tab.id)).toContain("tags");
  });
});
