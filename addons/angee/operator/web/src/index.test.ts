import type { BaseMenuItem } from "@angee/ui";
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
  test("registers a console route per section plus resource detail routes", () => {
    const routes = operator.routes ?? [];
    // Section routes are the nav-level panes; detail routes carry a `$param` and
    // point back to their section's menu for active-state rather than appearing
    // as their own nav entry.
    const sectionRoutes = routes.filter((route) => !route.path.includes("$"));
    const detailRoutes = routes.filter((route) => route.path.includes("$"));
    expect(sectionRoutes.map((route) => route.path)).toEqual(SECTION_PATHS);
    for (const route of routes) {
      expect(route.layout).toBe("console");
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

  test("contributes a single Operator group under platform, children mirror routes", () => {
    expect(operator.menus).toHaveLength(1);
    const menu = operator.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("operator");
    expect(menu?.icon).toBe("operator");
    // Operator contributes into the platform app rather than owning a rail root,
    // so it nests under platform and carries no `group` of its own; it keeps its
    // `route` so the route's `menu: "operator"` crumb resolves to it.
    expect(menu?.parentId).toBe("platform");
    expect(menu?.group).toBeUndefined();
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
