import type { TreeMenuItem } from "@refinedev/core";
import { describe, expect, test } from "vitest";

import { MenuTree } from "./menu-tree";
import { chromeMenuItemsFromRefine } from "./refine-menu";

describe("chromeMenuItemsFromRefine", () => {
  test("retains the full menu parent when native breadcrumbs collapse a duplicate group", () => {
    const root = refineItem("menu:desk", "desk", "Desk", "/desk");
    const group = refineItem("menu:desk.group", "desk.group", "Notes", "/notes", "menu:desk");
    const leaf = refineItem("menu:desk.notes", "desk.notes", "Notes", "/notes", "menu:desk");
    leaf.meta = { ...leaf.meta, menuParent: "menu:desk.group" };
    root.children = [group, leaf];
    const items = chromeMenuItemsFromRefine([root]);
    expect(() => JSON.stringify(items)).not.toThrow();
    const tree = MenuTree.from(items);
    expect(tree.trailFor("desk.notes").map((item) => item.id)).toEqual(["desk", "desk.group", "desk.notes"]);
    expect(tree.byId.get("desk")?.children?.map((item) => item.id)).toEqual(["desk.group"]);
  });

  test("preserves refine parent metadata so flat menu output re-nests before rail rendering", () => {
    const items = chromeMenuItemsFromRefine([
      refineItem("menu:agents", "agents", "Agents", "/agents", undefined, true),
      refineItem(
        "menu:agents.menu.agents",
        "agents.menu.agents",
        "Agents",
        "/agents",
        "menu:agents",
      ),
      refineItem(
        "menu:agents.agents",
        "agents.agents",
        "Agents",
        "/agents",
        "menu:agents.menu.agents",
      ),
    ]);
    const tree = MenuTree.from(items);

    expect(tree.roots.map((item) => item.id)).toEqual(["agents"]);
    expect(tree.trailFor("agents.agents").map((item) => item.id)).toEqual([
      "agents",
      "agents.menu.agents",
      "agents.agents",
    ]);
    expect(tree.railMenuItems().map((item) => item.id)).toEqual(["agents"]);
  });
});

function refineItem(
  identifier: string,
  menuId: string,
  label: string,
  route: string,
  parent?: string,
  appRoot?: boolean,
): TreeMenuItem {
  return {
    key: identifier,
    name: identifier,
    identifier,
    label,
    route,
    meta: {
      menuId,
      ...(parent ? { parent } : {}),
      ...(appRoot ? { appRoot } : {}),
    },
    children: [],
  } as TreeMenuItem;
}
