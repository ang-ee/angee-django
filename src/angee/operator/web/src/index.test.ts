import type { ChromeMenuItem } from "@angee/base";
import { describe, expect, test } from "vitest";

import operator from "./index";

// The console's eight sections, in nav order. Routes and the menu sub-nav must
// stay aligned to this list — a missing or extra entry is a wiring bug.
const SECTION_PATHS = [
  "/operator",
  "/operator/services",
  "/operator/workspaces",
  "/operator/sources",
  "/operator/gitops",
  "/operator/operations",
  "/operator/templates",
  "/operator/secrets",
];

describe("operator addon manifest", () => {
  test("registers one console route per section, each with a component", () => {
    const routes = operator.routes ?? [];
    expect(routes).toHaveLength(SECTION_PATHS.length);
    expect(routes.map((route) => route.path)).toEqual(SECTION_PATHS);
    for (const route of routes) {
      expect(route.shell).toBe("console");
      expect(route.component).toBeTypeOf("function");
    }
  });

  test("gives every route a unique addon-namespaced name", () => {
    const names = (operator.routes ?? []).map((route) => route.name);
    expect(names.every((name) => name.startsWith("operator."))).toBe(true);
    expect(new Set(names).size).toBe(names.length);
  });

  test("exposes a single Operator menu whose children mirror the routes", () => {
    expect(operator.menus).toHaveLength(1);
    const menu = operator.menus?.[0] as ChromeMenuItem | undefined;
    expect(menu?.id).toBe("operator");
    expect(menu?.icon).toBe("operator");
    expect(menu?.group).toBe("platform");
    expect(menu?.children?.map((child) => child.to)).toEqual(SECTION_PATHS);
  });

  test("declares its menu icon and i18n bundle", () => {
    expect(operator.icons?.operator).toBeDefined();
    expect(operator.i18n?.operator).toBeDefined();
  });
});
