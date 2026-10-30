import { expectValidBaseAddon } from "@angee/app/testing";
import { Tab, type ContainerChild, type TimelineKindContent } from "@angee/ui";
import { isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
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

  test("lands the Nexus root on its inbox when a deployment selects it, without a shell or brand of its own", () => {
    expect(nexus.menus.nexus?.home).toBe("nexus.inbox");
    expect(nexus.menus.nexus?.brand).toBeUndefined();
    expect(nexus.shell).toBeUndefined();
  });

  test("declares a glyph for every menu item it contributes", () => {
    // A menu item without an icon falls back to looking its id up in the glyph
    // registry, which silently renders nothing.
    for (const [id, item] of Object.entries(nexus.menus ?? {})) {
      expect(item.icon, `${id} declares no icon`).toBeTruthy();
      expect(Object.keys(nexus.icons ?? {})).toContain(item.icon);
    }
  });

  test("contributes correspondence to Timeline and Network to the record body", () => {
    const containers = nexus.containers ?? {};
    expect(containers).not.toHaveProperty("parties.Party#aside");
    expect(containers).not.toHaveProperty("parties.Circle#aside");
    const kinds = containers["timeline#kinds"] as Record<string, ContainerChild<TimelineKindContent>>;
    expect(kinds["nexus.correspondence"]).toEqual({ content: {
      label: "Correspondence", icon: "channel", verb: "Open message", target: { page: true },
    } });
    const sections = containers["parties.Party#sections"] as Record<string, ContainerChild>;
    const network = sections["nexus.network"]?.content;
    expect(isValidElement(network)).toBe(true);
    if (!isValidElement<{ id: string; label: React.ReactNode }>(network)) throw new Error("Missing Network tab");
    expect(network.type).toBe(Tab);
    expect(network.props.id).toBe("network");
    expect(renderToStaticMarkup(<>{network.props.label}</>)).toBe("Network");
  });
});
