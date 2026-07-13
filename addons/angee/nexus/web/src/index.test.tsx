import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import nexus from "./index";

describe("nexus addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(nexus)).not.toThrow();
  });

  test("registers only the ties resource pages", () => {
    expect((nexus.routes ?? []).map((route) => route.name)).toEqual([
      "nexus.ties",
      "nexus.ties.record",
    ]);
  });

  test("keeps only ties in the connections menu", () => {
    const menu = (nexus.menus ?? []).find((item) => item.id === "nexus");
    expect(menu?.children?.map((item) => item.route)).toEqual(["nexus.ties"]);
  });

  test("timeline chatter tab self-gates to party records", () => {
    const timeline = (nexus.chatter ?? []).find((entry) => entry.id === "timeline");
    expect(timeline?.render).toBeDefined();
    const render = timeline?.render;
    if (!render) throw new Error("missing render");
    // A non-party record drops the tab (null render), a party record mounts it.
    expect(
      render({
        pathname: "/storage/files/abc",
        params: { id: "abc" },
        route: { name: "storage.files.record", path: "/storage/files/$id", viewType: "list", modelLabel: "storage.File" },
        view: { kind: "record", type: "list", sqid: "abc" },
      }),
    ).toBeNull();
    expect(
      render({
        pathname: "/parties/people/pty_1",
        params: { id: "pty_1" },
        route: { name: "parties.people.record", path: "/parties/people/$id", viewType: "list", modelLabel: "parties.Person" },
        view: { kind: "record", type: "list", sqid: "pty_1" },
      }),
    ).not.toBeNull();
    // A dashboard (no record) never shows the tab, even on the party route.
    expect(
      render({
        pathname: "/parties/people",
        params: {},
        route: { name: "parties.people", path: "/parties/people", viewType: "list", modelLabel: "parties.Person" },
        view: { kind: "dashboard", type: "list" },
      }),
    ).toBeNull();
  });
});
