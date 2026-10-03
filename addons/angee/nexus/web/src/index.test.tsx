import { expectValidBaseAddon } from "@angee/app/testing";
import type { ChatterTabContent, ContainerChild } from "@angee/ui";
import { describe, expect, test } from "vitest";

import nexus from "./index";

describe("nexus addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(nexus)).not.toThrow();
  });

  test("registers the graph explorer and the ties/cadence resource pages", () => {
    expect((nexus.routes ?? []).map((route) => route.name)).toEqual([
      "nexus.inbox",
      "nexus.graph",
      "nexus.ties",
      "nexus.ties.record",
      "nexus.cadences",
      "nexus.cadences.record",
    ]);
  });

  test("registers the Nexus inbox and brings relationship analytics under Nexus", () => {
    const menus = Object.values(nexus.menus ?? {});
    expect(menus.map((item) => item.route)).toEqual([
      undefined, "nexus.inbox",
      "nexus.graph",
      "nexus.ties",
      "nexus.cadences",
    ]);
    expect(menus.map((item) => item.parent)).toEqual([undefined, "nexus", "nexus", "nexus", "nexus"]);
    expect(nexus.routes?.find((route) => route.name === "nexus.inbox")?.menu).toBeUndefined();
    expect(menus.slice(1).map((item) => item.sequence)).toEqual([10, 20, 30, 40]);
    expect(nexus.menus["nexus.ties"]?.hide).toBe(true);
    expect(nexus.menus.messaging?.hide).toBeUndefined();
  });

  test("offers a Nexus perspective for the deployment to select without setting a shell or brand", () => {
    expect(nexus.perspectives).toEqual({ nexus: { root: "nexus", home: "nexus.inbox" } });
    expect(nexus.shell).toBeUndefined();
    expect(nexus.brand).toBeUndefined();
  });

  test("declares a glyph for every menu item it contributes", () => {
    // A menu item without an icon falls back to looking its id up in the glyph
    // registry, which silently renders nothing.
    for (const [id, item] of Object.entries(nexus.menus ?? {})) {
      expect(item.icon, `${id} declares no icon`).toBeTruthy();
      expect(Object.keys(nexus.icons ?? {})).toContain(item.icon);
    }
  });

  test("declares canonical model and record scopes for chatter tabs", () => {
    const containers = (nexus.containers ?? {}) as Record<string, Record<string, ContainerChild<ChatterTabContent>>>;
    const chatter = ["parties.Party#aside", "parties.Circle#aside"].flatMap((address) =>
      Object.entries(containers[address] ?? {}).map(([id, child]) => ({ id, address, ...child })));
    expect(chatter.map(({ id, sequence, address, content }) => ({ id, sequence, address, aliases: content.aliases }))).toEqual([
      { id: "nexus.timeline", sequence: 30, address: "parties.Party#aside", aliases: ["timeline"] },
      { id: "nexus.network", sequence: 31, address: "parties.Party#aside", aliases: ["network"] },
      { id: "nexus.feed", sequence: 32, address: "parties.Circle#aside", aliases: ["feed"] },
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
    for (const { content } of chatter) {
      expect(content.when?.(recordContext)).toBe(true);
      expect(content.when?.(dashboardContext)).toBe(false);
    }
  });
});
