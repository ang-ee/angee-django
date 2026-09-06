import { expectValidBaseAddon } from "@angee/app/testing";
import { MenuTree, type ChromeMenuItem } from "@angee/ui";
import { describe, expect, test } from "vitest";

import integrateVcs from "./index";

describe("integrate_vcs addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(integrateVcs)).not.toThrow();
  });

  test("owns the four VCS resources under their stable paths", () => {
    for (const [name, path, model] of [
      ["integrate_vcs.vcs", "/integrate/vcs", "integrate_vcs.VcsBridge"],
      ["integrate_vcs.repositories", "/integrate/repositories", "integrate_vcs.Repository"],
      ["integrate_vcs.sources", "/integrate/sources", "integrate_vcs.Source"],
      ["integrate_vcs.templates", "/integrate/templates", "integrate_vcs.Template"],
    ] as const) {
      const route = integrateVcs.routes?.find((item) => item.name === name);
      expect(route).toMatchObject({ path, resource: model });
    }
  });

  test("registers the complete bridge form under its canonical label", () => {
    expect(integrateVcs.forms?.["integrate_vcs.VcsBridge"]).toBeDefined();
  });

  test("routes every source menu leaf exactly once", () => {
    const tree = MenuTree.from(integrateVcs.menus as readonly ChromeMenuItem[]);
    for (const route of ["integrate_vcs.sources", "integrate_vcs.templates", "integrate_vcs.repositories", "integrate_vcs.vcs"]) {
      expect(tree.itemsForRoute(route)).toHaveLength(1);
    }
  });
});
