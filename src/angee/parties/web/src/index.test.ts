import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { MenuTree } from "@angee/ui";
import { describe, expect, test } from "vitest";

import { folderForm } from "./FolderForm";
import parties, { DIRECTORIES_TOOLBAR } from "./index";

const declaredMenus = composeAddons([{ id: parties.id, menus: parties.menus, routes: parties.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("parties addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(parties)).not.toThrow();
  });

  test("declares the directories toolbar its vendors contribute their connect verb to", () => {
    expect(parties.containers?.[DIRECTORIES_TOOLBAR]).toEqual({});
  });

  test("lists contact folders beside the directories in their Settings category", () => {
    const tree = MenuTree.from(declaredMenus);
    const directories = tree.byId.get("parties.directories");
    expect(directories).toMatchObject({ group: "platform" });
    expect(directories?.route).toBeUndefined();
    expect(directories?.children?.map((item) => [item.id, item.route])).toEqual([
      ["parties.directories.list", "parties.directories"],
      ["parties.folders", "parties.folders"],
    ]);
    // One menu item per page, so each derives its own chrome.
    expect(tree.itemsForRoute("parties.directories")).toHaveLength(1);
    expect(tree.itemsForRoute("parties.folders")).toHaveLength(1);
  });

  test("routes the folders under the directories' path and registers the folder form", () => {
    const routes = new Map((parties.routes ?? []).map((route) => [route.name, route]));
    expect(routes.get("parties.folders")).toMatchObject({ path: "/parties/directories/folders", resource: "parties.Folder" });
    expect(routes.get("parties.folders.record")).toMatchObject({ path: "/parties/directories/folders/$id", parent: "parties.folders" });
    expect(parties.forms?.["parties.Folder"]).toBe(folderForm);
  });
});
