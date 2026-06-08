import type { BaseMenuItem } from "@angee/base";
import { describe, expect, test } from "vitest";

import storage from "./index";

describe("storage addon manifest", () => {
  test("registers the files route on the console shell with a component", () => {
    const routes = storage.routes ?? [];
    expect(routes).toHaveLength(1);
    expect(routes[0]?.name).toBe("storage.files");
    expect(routes[0]?.path).toBe("/storage");
    expect(routes[0]?.shell).toBe("console");
    expect(routes[0]?.component).toBeTypeOf("function");
  });

  test("exposes a single Files menu targeting the files route", () => {
    expect(storage.menus).toHaveLength(1);
    const menu = storage.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("storage");
    expect(menu?.route).toBe("storage.files");
    expect(menu?.group).toBe("platform");
  });

  test("registers its drive/folder glyphs", () => {
    expect(storage.icons?.drive).toBeDefined();
    expect(storage.icons?.folder).toBeDefined();
  });
});
