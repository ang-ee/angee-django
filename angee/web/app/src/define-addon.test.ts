import { describe, expect, test } from "vitest";

import { composeAddons, defineAddon } from "./define-addon";

describe("defineAddon", () => {
  test("returns the manifest unchanged for typed authoring", () => {
    const manifest = defineAddon({ id: "notes" });
    expect(manifest).toEqual({ id: "notes" });
  });
});

describe("composeAddons", () => {
  test("concatenates routes in addon order", () => {
    const a = defineAddon({ id: "a", routes: [{ name: "a.home", path: "/a", layout: "console" }] });
    const b = defineAddon({ id: "b", routes: [{ name: "b.home", path: "/b", layout: "console" }] });
    expect(composeAddons([a, b]).routes.map((r) => r.path)).toEqual(["/a", "/b"]);
  });

  test("rejects two routes that declare the same name", () => {
    const a = defineAddon({ id: "a", routes: [{ name: "dup", path: "/a", layout: "console" }] });
    const b = defineAddon({ id: "b", routes: [{ name: "dup", path: "/b", layout: "console" }] });
    expect(() => composeAddons([a, b])).toThrow(/dup/);
  });

  test("rejects duplicate menu item ids across nested menus", () => {
    const a = defineAddon({
      id: "a",
      menus: [
        {
          id: "root",
          label: "Root",
          children: [{ id: "dup", label: "Dup" }],
        },
      ],
    });
    const b = defineAddon({ id: "b", menus: [{ id: "dup", label: "Dup" }] });

    expect(() => composeAddons([a, b])).toThrow(/menu item id "dup"/);
  });

  test("defaults menu item id to route before claiming collisions", () => {
    const composed = composeAddons([
      defineAddon({
        id: "notes",
        menus: [
          {
            label: "Notes",
            route: "notes.home",
            children: [{ label: "Archive", route: "notes.archive" }],
          },
        ],
      }),
    ]);

    expect(composed.menus[0]?.id).toBe("notes.home");
    expect(composed.menus[0]?.children?.[0]?.id).toBe("notes.archive");
    expect(() =>
      composeAddons([
        defineAddon({ id: "a", menus: [{ route: "shared.route" }] }),
        defineAddon({ id: "b", menus: [{ id: "shared.route" }] }),
      ]),
    ).toThrow(/menu item id "shared.route"/);
  });

  test("requires a menu id when no route can own the default", () => {
    expect(() =>
      composeAddons([defineAddon({ id: "bad", menus: [{ label: "Bad" }] })]),
    ).toThrow(/without id or route/);
  });

  test("merges widget and i18n registries", () => {
    const a = defineAddon({
      id: "a",
      widgets: { text: "TEXT" },
      i18n: { notes: { title: "Title" } },
    });
    const b = defineAddon({ id: "b", widgets: { date: "DATE" } });
    const composed = composeAddons([a, b]);
    expect(composed.widgets).toEqual({ text: "TEXT", date: "DATE" });
    expect(composed.i18n).toEqual({ notes: { title: "Title" } });
  });

  test("orders chatter contributions by sequence, not addon order", () => {
    const a = defineAddon({
      id: "a",
      chatter: [{ id: "late", sequence: 20 }],
    });
    const b = defineAddon({
      id: "b",
      chatter: [{ id: "early", sequence: 10 }],
    });
    expect(composeAddons([a, b]).chatter.map((c) => c.id)).toEqual([
      "early",
      "late",
    ]);
  });

  test("a later slot contribution with the same key overrides the earlier one", () => {
    const a = defineAddon({
      id: "a",
      slots: [{ slot: "header", id: "logo", sequence: 1, content: "A" }],
    });
    const b = defineAddon({
      id: "b",
      slots: [{ slot: "header", id: "logo", sequence: 2, content: "B" }],
    });
    const slots = composeAddons([a, b]).slots;
    expect(slots).toHaveLength(1);
    expect(slots[0]?.sequence).toBe(2);
    expect(slots[0]?.content).toBe("B");
  });

  test("the same slot id under a different slot is kept separate", () => {
    const a = defineAddon({
      id: "a",
      slots: [
        { slot: "header", id: "logo" },
        { slot: "footer", id: "logo" },
      ],
    });
    expect(composeAddons([a]).slots).toHaveLength(2);
  });

  test("orders drawer contributions by sequence, not addon order", () => {
    const a = defineAddon({
      id: "a",
      drawers: [
        { id: "late", edge: "bottom", title: "Late", sequence: 20, render: () => null },
      ],
    });
    const b = defineAddon({
      id: "b",
      drawers: [
        { id: "early", edge: "bottom", title: "Early", sequence: 10, render: () => null },
      ],
    });
    expect(composeAddons([a, b]).drawers.map((d) => d.id)).toEqual([
      "early",
      "late",
    ]);
  });

  test("keeps the same drawer id separate under a different edge", () => {
    const a = defineAddon({
      id: "a",
      drawers: [
        { id: "logs", edge: "right", title: "Logs", render: () => null },
        { id: "logs", edge: "bottom", title: "Logs", render: () => null },
      ],
    });
    expect(composeAddons([a]).drawers).toHaveLength(2);
  });

  test("rejects two drawers claiming the same edge and id", () => {
    const a = defineAddon({
      id: "a",
      drawers: [{ id: "logs", edge: "bottom", title: "A", render: () => null }],
    });
    const b = defineAddon({
      id: "b",
      drawers: [{ id: "logs", edge: "bottom", title: "B", render: () => null }],
    });
    expect(() => composeAddons([a, b])).toThrow(/drawer/);
  });

  test("rejects two addons that declare the same widget key", () => {
    const a = defineAddon({ id: "a", widgets: { text: "A" } });
    const b = defineAddon({ id: "b", widgets: { text: "B" } });
    expect(() => composeAddons([a, b])).toThrow(/text/);
  });

  test("merges data providers keyed by provider name", () => {
    const a = defineAddon({ id: "a", dataProviders: { operator: "OP" } });
    const b = defineAddon({ id: "b", dataProviders: { ledger: "LEDGER" } });
    expect(composeAddons([a, b]).dataProviders).toEqual({
      operator: "OP",
      ledger: "LEDGER",
    });
  });

  test("rejects two addons that claim the same data provider name", () => {
    const a = defineAddon({ id: "a", dataProviders: { operator: "A" } });
    const b = defineAddon({ id: "b", dataProviders: { operator: "B" } });
    expect(() => composeAddons([a, b])).toThrow(/data provider "operator"/);
  });
});
