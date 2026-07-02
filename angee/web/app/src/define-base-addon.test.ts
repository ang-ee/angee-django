// @vitest-environment happy-dom

import { describe, expect, test } from "vitest";

import {
  defineBaseAddon,
  resourcePageRoutes,
} from "./define-base-addon";
import { expectValidBaseAddon } from "./testing";

function Page(): null {
  return null;
}

describe("resourcePageRoutes", () => {
  test("authors the list and nested record route with console defaults", () => {
    expect(
      resourcePageRoutes("notes", "/notes", Page, "notes.Note", { menu: "notes" }),
    ).toEqual([
      {
        name: "notes",
        path: "/notes",
        layout: "console",
        component: Page,
        resource: "notes.Note",
        menu: "notes",
      },
      {
        name: "notes.record",
        path: "/notes/$id",
        layout: "console",
        parent: "notes",
      },
    ]);
  });

  test("normalizes omitted route layouts on rendered addons", () => {
    const addon = defineBaseAddon({
      id: "notes",
      routes: [{ name: "notes", path: "/notes", component: Page }],
    });

    expect(addon.routes?.[0]?.layout).toBe("console");
  });
});

describe("expectValidBaseAddon", () => {
  test("accepts a route pair and matching menu", () => {
    const addon = defineBaseAddon({
      id: "notes",
      routes: resourcePageRoutes("notes", "/notes", Page, "notes.Note"),
      menus: [{ id: "notes", label: "Notes", route: "notes", icon: "book-open" }],
    });

    expect(() => expectValidBaseAddon(addon)).not.toThrow();
  });

  test("rejects an unparented record route", () => {
    const addon = defineBaseAddon({
      id: "notes",
      routes: [{ name: "record", path: "/notes/$id", component: Page }],
    });

    expect(() => expectValidBaseAddon(addon)).toThrow(/has no parent/);
  });
});
