import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { MenuTree } from "@angee/ui";
import { describe, expect, test } from "vitest";

import integrateVcs from "./index";

const declaredMenus = composeAddons([{ id: integrateVcs.id, menus: integrateVcs.menus, routes: integrateVcs.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("integrate_vcs addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(integrateVcs)).not.toThrow();
  });

  test("registers the complete bridge form under its canonical label", () => {
    expect(integrateVcs.forms?.["integrate_vcs.VcsBridge"]).toBeDefined();
  });

  test("routes every source menu leaf exactly once", () => {
    const tree = MenuTree.from(declaredMenus);
    for (const route of ["integrate_vcs.sources", "integrate_vcs.templates", "integrate_vcs.repositories", "integrate_vcs.vcs"]) {
      expect(tree.itemsForRoute(route)).toHaveLength(1);
    }
  });
});
