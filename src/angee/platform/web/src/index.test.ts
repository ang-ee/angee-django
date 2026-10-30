import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import platform from "./index";

const declaredMenus = composeAddons([{ id: platform.id, menus: platform.menus, routes: platform.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("platform addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(platform)).not.toThrow();
  });

  test("registers a console route per section plus model/addon detail routes", () => {
    const routes = platform.routes ?? [];
    expect(routes.map((route) => route.path)).toEqual([
      "/platform",
      "/platform/models",
      "/platform/models/$id",
      "/platform/fields",
      "/platform/implementations",
      "/platform/implementations/$id",
      "/platform/addons",
      "/platform/addons/$id",
      "/platform/resources",
    ]);
    for (const route of routes) {
      expect(route.component ?? route.indexComponent).toBeTypeOf("function");
    }
  });

  test("is a Settings category", () => {
    expect(declaredMenus).toHaveLength(1);
    const root = declaredMenus?.[0];
    expect(root?.id).toBe("platform");
    expect(root?.group).toBe("platform");
    expect(root?.icon).toBe("terminal");
  });

  test("groups the explorer sections under one Platform tree branch", () => {
    const root = declaredMenus?.[0];
    const groups = root?.children ?? [];
    expect(groups.map((group) => group.id)).toEqual(["platform.explore", "platform.resources"]);
    expect(groups[0]?.children?.map((child) => child.label)).toEqual([
      "Graph",
      "Models",
      "Fields",
      "Implementations",
      "Addons",
    ]);
    expect(groups[0]?.children?.map((child) => child.route)).toEqual([
      "platform.graph",
      "platform.models",
      "platform.fields",
      "platform.implementations",
      "platform.addons",
    ]);
  });

  test("owns the ledger console while preserving its route and resource identity", () => {
    const root = declaredMenus?.[0];
    expect(root?.children?.find((child) => child.id === "platform.resources")).toMatchObject({
      label: "Resources", icon: "archive", route: "resources.ledger",
    });
    expect(platform.routes?.find((route) => route.name === "resources.ledger")).toMatchObject({
      path: "/platform/resources", resource: "resources.Resource",
    });
  });

});
