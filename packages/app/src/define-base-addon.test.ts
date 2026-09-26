// @vitest-environment happy-dom

import { lazyRouteComponent } from "@tanstack/react-router";
import { describe, expect, expectTypeOf, test } from "vitest";

import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
} from "./define-base-addon";
import type { LoginPage } from "./auth/LoginPage";
import { expectValidBaseAddon } from "./testing";

function Page(): null {
  return null;
}

test("route components accept optional props and retain native lazy preloading", () => {
  function OptionalPropsPage({ title }: { title?: string }): string | null {
    return title ?? null;
  }
  const LazyPage = lazyRouteComponent(async () => ({ default: Page }));
  const addon = defineBaseAddon({
    id: "pages",
    routes: [
      { name: "eager", path: "/eager", component: OptionalPropsPage },
      { name: "lazy", path: "/lazy", component: LazyPage },
    ],
  });

  expect(addon.routes?.[0]?.component).toBe(OptionalPropsPage);
  expect(addon.routes?.[1]?.component?.preload).toBe(LazyPage.preload);
  expectTypeOf<typeof LoginPage>().toExtend<NonNullable<BaseAddonRoute["component"]>>();
  expectTypeOf<(props: { required: string }) => null>()
    .not.toExtend<NonNullable<BaseAddonRoute["component"]>>();
});

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

  test("uses a native index page when the record has its own component", () => {
    function Detail(): null { return null; }
    const routes = resourcePageRoutes("services", "/services", Page, undefined, {
      detailComponent: Detail,
    });

    expect(routes[0]?.component).toBeUndefined();
    expect(routes[0]?.indexComponent).toBe(Page);
    expect(routes[1]?.parent).toBe("services");
    expect(routes[1]?.component).toBe(Detail);
  });
});

describe("expectValidBaseAddon", () => {
  test("accepts a route pair and matching menu", () => {
    const addon = defineBaseAddon({
      id: "notes",
      routes: resourcePageRoutes("notes", "/notes", Page, "notes.Note"),
      menus: [{ id: "notes", label: "Notes", route: "notes", icon: "notes" }],
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

  test("leaves cross-addon menu route validation to full app composition", () => {
    const addon = defineBaseAddon({
      id: "notes-extension",
      menus: [
        {
          id: "notes.extra",
          label: "Extra",
          route: "notes.home",
          params: { section: "shared" },
          icon: "notes",
        },
      ],
    });

    expect(() => expectValidBaseAddon(addon)).not.toThrow();
    expect(addon.menus?.[0]?.params).toEqual({ section: "shared" });
  });
});
