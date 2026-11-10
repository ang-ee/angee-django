import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import operator from "./index";

const declaredMenus = composeAddons([{ id: operator.id, menus: operator.menus, routes: operator.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("operator addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(operator)).not.toThrow();
  });

  test("renders every route and anchors each detail route to its section's menu", () => {
    const routes = operator.routes ?? [];
    // Section routes are the nav-level panes; detail routes carry a `$param` and
    // point back to their section's menu for active-state rather than appearing
    // as their own nav entry.
    const sectionRoutes = routes.filter((route) => !route.path.includes("$"));
    const detailRoutes = routes.filter((route) => route.path.includes("$"));
    for (const route of routes) {
      expect(route.component ?? route.indexComponent).toBeTypeOf("function");
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

  test("contributes one Settings category whose children mirror the section routes", () => {
    expect(declaredMenus).toHaveLength(1);
    const sectionNames = (operator.routes ?? [])
      .filter((route) => !route.path.includes("$"))
      .map((route) => route.name);
    expect(declaredMenus?.[0]?.children?.map((child) => child.route)).toEqual(sectionNames);
  });
});
