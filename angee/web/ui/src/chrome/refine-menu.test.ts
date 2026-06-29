import type { TreeMenuItem } from "@refinedev/core";
import { describe, expect, test } from "vitest";

import { MenuTree } from "./menu-tree";
import { chromeMenuItemsFromRefine } from "./refine-menu";

describe("chromeMenuItemsFromRefine", () => {
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
