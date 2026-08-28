import { expectValidBaseAddon } from "@angee/app/testing";
import type { BaseMenuItem } from "@angee/ui";
import { describe, expect, test } from "vitest";

import operator from "./index";

// The console's eight sections, in nav order. Routes and the menu tree must
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
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(operator)).not.toThrow();
  });

  test("registers a console route per section plus resource detail routes", () => {
    const routes = operator.routes ?? [];
    // Section routes are the nav-level panes; detail routes carry a `$param` and
    // point back to their section's menu for active-state rather than appearing
    // as their own nav entry.
    const sectionRoutes = routes.filter((route) => !route.path.includes("$"));
    const detailRoutes = routes.filter((route) => route.path.includes("$"));
    expect(sectionRoutes.map((route) => route.path)).toEqual(SECTION_PATHS);
    for (const route of routes) {
      expect(route.component).toBeTypeOf("function");
    }
    const sectionNames = new Set(sectionRoutes.map((route) => route.name));
    for (const route of detailRoutes) {
      expect(route.menu && sectionNames.has(route.menu)).toBe(true);
    }
  });

  test("gives every route a unique addon-namespaced name", () => {
    const names = (operator.routes ?? []).map((route) => route.name);
    expect(names.every((name) => name.startsWith("operator."))).toBe(true);
    expect(new Set(names).size).toBe(names.length);
  });

  test("contributes one Operator Settings category whose children mirror routes", () => {
    expect(operator.menus).toHaveLength(1);
    const menu = operator.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("operator");
    expect(menu?.icon).toBe("operator");
    expect(menu?.parentId).toBeUndefined();
    expect(menu?.group).toBe("platform");
    expect(menu?.route).toBe("operator.overview");
    const sectionNames = (operator.routes ?? [])
      .filter((route) => !route.path.includes("$"))
      .map((route) => route.name);
    expect(menu?.children?.map((child) => child.route)).toEqual(sectionNames);
    expect(menu?.children?.map((child) => child.to)).toEqual(
      SECTION_PATHS.map(() => undefined),
    );
  });

  test("declares its menu icon and i18n bundle", () => {
    expect(operator.icons?.operator).toBeDefined();
    expect(operator.i18n?.operator).toBeDefined();
  });
});
