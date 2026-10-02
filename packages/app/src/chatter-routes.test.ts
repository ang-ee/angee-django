import { describe, expect, test } from "vitest";
import { MenuTree } from "@angee/ui/chrome/menu-tree";

import { resourcePageRoutes } from "./define-base-addon";
import { routePolicyIndex } from "./route-policy";

const menus = MenuTree.from([{ id: "example", label: "Example", to: "/records" }]);
const inventory = { slots: [], chatter: [], drawers: [] };

describe("route surface policy", () => {
  test("a collection route scope carries aside selection to its record child", () => {
    const routes = resourcePageRoutes("example.records", "/records", () => null);
    const policy = routePolicyIndex(routes, [{ app: "example", route: "example.records", chatter: { tabs: ["comments"] } }], menus, inventory);
    expect(policy("example", "example.records").chatter).toEqual({ tabs: ["comments"] });
    expect(policy("example", "example.records.record").chatter).toEqual({ tabs: ["comments"] });
  });

  test("inherits from app through route ancestors and lets a child hide the aside", () => {
    const routes = [
      { name: "example.home", path: "/example" },
      { name: "example.record", path: "/example/$id", parent: "example.home" },
      { name: "example.quiet", path: "/example/$id/quiet", parent: "example.record" },
    ];
    const policy = routePolicyIndex(routes, [
      { app: "example", chatter: { tabs: ["comments", "activity"] }, shell: { commandSearch: false } },
      { app: "example", route: "example.home", shell: { breadcrumb: false } },
      { app: "example", route: "example.quiet", chatter: "hidden" },
    ], menus, inventory);
    expect(policy("example", "example.record").chatter).toEqual({ tabs: ["comments", "activity"] });
    expect(policy("example", "example.quiet")).toMatchObject({ chatter: "hidden", shell: { breadcrumb: false, commandSearch: false } });
  });
});
