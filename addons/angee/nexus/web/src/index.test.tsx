import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import nexus from "./index";

describe("nexus addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(nexus)).not.toThrow();
  });

  test("registers the graph explorer and the ties/cadence resource pages", () => {
    expect((nexus.routes ?? []).map((route) => route.name)).toEqual([
      "nexus.graph",
      "nexus.ties",
      "nexus.ties.record",
      "nexus.cadences",
      "nexus.cadences.record",
    ]);
  });

  test("overlays the parties rail instead of standing up its own app", () => {
    // The chrome derives the app rail from the menu roots, so contributing a root
    // here would make nexus a destination beside parties rather than an
    // intelligence layer over it. Every item hangs off the rail parties owns.
    const menus = nexus.menus ?? [];
    expect(menus.map((item) => item.route)).toEqual([
      "nexus.graph",
      "nexus.ties",
      "nexus.cadences",
    ]);
    expect(menus.map((item) => item.parentId)).toEqual(["parties", "parties", "parties"]);
    expect(menus.some((item) => item.children)).toBe(false);
  });

  test("declares a glyph for every menu item it contributes", () => {
    // A menu item without an icon falls back to looking its id up in the glyph
    // registry, which silently renders nothing.
    for (const item of nexus.menus ?? []) {
      expect(item.icon, `${item.id} declares no icon`).toBeTruthy();
      expect(Object.keys(nexus.icons ?? {})).toContain(item.icon);
    }
  });

  test("declares canonical model and record scopes for chatter tabs", () => {
    const chatter = nexus.chatter ?? [];
    expect(chatter.map(({ id, sequence, model }) => ({ id, sequence, model }))).toEqual([
      { id: "timeline", sequence: 30, model: "parties.Party" },
      { id: "network", sequence: 31, model: "parties.Party" },
      { id: "feed", sequence: 32, model: "parties.Circle" },
    ]);
    const recordContext = {
      pathname: "/parties/people/abc",
      params: { id: "abc" },
      view: { kind: "record" as const, type: "list", sqid: "abc" },
    };
    const dashboardContext = {
      pathname: "/parties/people",
      params: {},
      view: { kind: "dashboard" as const, type: "list" },
    };
    for (const entry of chatter) {
      expect(entry.when?.(recordContext)).toBe(true);
      expect(entry.when?.(dashboardContext)).toBe(false);
    }
  });
});
