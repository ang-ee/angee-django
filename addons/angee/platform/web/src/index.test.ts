import type { BaseMenuItem } from "@angee/base";
import { describe, expect, test } from "vitest";

import platform from "./index";

describe("platform addon manifest", () => {
  test("registers one console route per explorer section", () => {
    const routes = platform.routes ?? [];
    expect(routes.map((route) => route.path)).toEqual([
      "/platform",
      "/platform/models",
      "/platform/fields",
      "/platform/addons",
    ]);
    for (const route of routes) {
      expect(route.shell).toBe("console");
      expect(route.component).toBeTypeOf("function");
    }
  });

  test("is a bottom-cluster app that opts into the sidebar", () => {
    expect(platform.menus).toHaveLength(1);
    const root = platform.menus?.[0] as BaseMenuItem | undefined;
    expect(root?.id).toBe("platform");
    expect(root?.group).toBe("platform");
    expect(root?.sidebar).toBe(true);
    expect(root?.icon).toBe("platform");
  });

  test("groups the explorer sections under one Platform top menu", () => {
    const root = platform.menus?.[0] as BaseMenuItem | undefined;
    const groups = root?.children ?? [];
    expect(groups.map((group) => group.id)).toEqual(["platform.explore"]);
    expect(groups[0]?.children?.map((child) => child.route)).toEqual([
      "platform.graph",
      "platform.models",
      "platform.fields",
      "platform.addons",
    ]);
  });

  test("registers the platform glyph", () => {
    expect(platform.icons?.platform).toBeDefined();
  });
});
