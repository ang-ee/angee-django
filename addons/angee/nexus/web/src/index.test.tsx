import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import nexus from "./index";

describe("nexus addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(nexus)).not.toThrow();
  });

  test("registers the derived ties and human cadence resource pages", () => {
    expect((nexus.routes ?? []).map((route) => route.name)).toEqual([
      "nexus.ties",
      "nexus.ties.record",
      "nexus.cadences",
      "nexus.cadences.record",
    ]);
  });

  test("keeps ties and cadences in the connections menu", () => {
    const menu = (nexus.menus ?? []).find((item) => item.id === "nexus");
    expect(menu?.children?.map((item) => item.route)).toEqual([
      "nexus.ties",
      "nexus.cadences",
    ]);
  });

  test("timeline chatter tab self-gates to party records", () => {
    const timeline = (nexus.chatter ?? []).find((entry) => entry.id === "timeline");
    expect(timeline?.render).toBeDefined();
    const render = timeline?.render;
    if (!render) throw new Error("missing render");
    // A non-party canonical target drops the tab even when the route model is a
    // Party subtype.
    expect(
      render({
        pathname: "/parties/people/abc",
        params: { id: "abc" },
        route: {
          name: "parties.people.record",
          path: "/parties/people/$id",
          viewType: "list",
          modelLabel: "parties.Person",
          canonicalLabel: "storage.File",
        },
        view: { kind: "record", type: "list", sqid: "abc" },
      }),
    ).toBeNull();
    // A future subtype inherits the tab from its canonical Party target without
    // being named by nexus.
    expect(
      render({
        pathname: "/crm/vips/pty_1",
        params: { id: "pty_1" },
        route: {
          name: "crm.vips.record",
          path: "/crm/vips/$id",
          viewType: "list",
          modelLabel: "crm.Vip",
          canonicalLabel: "parties.Party",
        },
        view: { kind: "record", type: "list", sqid: "pty_1" },
      }),
    ).not.toBeNull();
    // A dashboard (no record) never shows the tab, even on the party route.
    expect(
      render({
        pathname: "/parties/people",
        params: {},
        route: {
          name: "parties.people",
          path: "/parties/people",
          viewType: "list",
          modelLabel: "parties.Person",
          canonicalLabel: "parties.Party",
        },
        view: { kind: "dashboard", type: "list" },
      }),
    ).toBeNull();
  });
});
