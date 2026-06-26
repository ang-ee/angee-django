import type { BaseMenuItem } from "@angee/ui";
import { describe, expect, test } from "vitest";

import knowledge from "./index";

describe("knowledge addon manifest", () => {
  test("registers the wiki route on the console layout with a component", () => {
    const routes = knowledge.routes ?? [];
    expect(routes).toHaveLength(3);
    expect(routes[0]?.name).toBe("knowledge.home");
    expect(routes[0]?.path).toBe("/knowledge");
    expect(routes[0]?.layout).toBe("console");
    expect(routes[0]?.component).toBeTypeOf("function");
  });

  test("registers the vaults admin route as a static sibling", () => {
    const settings = (knowledge.routes ?? []).find(
      (route) => route.name === "knowledge.settings",
    );
    expect(settings?.path).toBe("/knowledge/settings");
    expect(settings?.parent).toBeUndefined();
    expect(settings?.component).toBeTypeOf("function");
  });

  test("nests the page reader route under the wiki resource route", () => {
    const record = (knowledge.routes ?? []).find(
      (route) => route.name === "knowledge.page",
    );
    expect(record?.path).toBe("/knowledge/$id");
    expect(record?.parent).toBe("knowledge.home");
    expect(record?.component).toBeUndefined();
    expect(record?.layout).toBe("console");
  });

  test("exposes a Knowledge menu with a Vaults child", () => {
    expect(knowledge.menus).toHaveLength(1);
    const menu = knowledge.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("knowledge");
    expect(menu?.route).toBe("knowledge.home");
    // Knowledge is a domain app (top of the rail), so it carries no group.
    expect(menu?.group).toBeUndefined();
    expect(menu?.children?.map((child) => child.route)).toEqual([
      "knowledge.home",
      "knowledge.settings",
    ]);
  });

  test("registers its vault/note glyphs", () => {
    expect(knowledge.icons?.vault).toBeDefined();
    expect(knowledge.icons?.note).toBeDefined();
    expect(knowledge.icons?.template).toBeDefined();
  });
});
