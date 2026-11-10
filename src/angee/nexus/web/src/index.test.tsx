import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import nexus from "./index";

describe("nexus addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(nexus)).not.toThrow();
  });

  test("declares a glyph for every menu item it contributes", () => {
    // A menu item without an icon falls back to looking its id up in the glyph
    // registry, which silently renders nothing.
    for (const [id, item] of Object.entries(nexus.menus ?? {})) {
      expect(item.icon, `${id} declares no icon`).toBeTruthy();
      expect(Object.keys(nexus.icons ?? {})).toContain(item.icon);
    }
  });
});
