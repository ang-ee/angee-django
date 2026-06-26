import type { BaseMenuItem } from "@angee/ui";
import { describe, expect, test } from "vitest";

import storage from "./index";

describe("storage addon manifest", () => {
  test("registers the files route on the console layout with a component", () => {
    const routes = storage.routes ?? [];
    expect(routes).toHaveLength(3);
    expect(routes[0]?.name).toBe("storage.files");
    expect(routes[0]?.path).toBe("/storage");
    expect(routes[0]?.layout).toBe("console");
    expect(routes[0]?.component).toBeTypeOf("function");
  });

  test("registers the settings admin route as a static sibling", () => {
    const settings = (storage.routes ?? []).find(
      (route) => route.name === "storage.settings",
    );
    expect(settings?.path).toBe("/storage/settings");
    expect(settings?.parent).toBeUndefined();
    expect(settings?.component).toBeTypeOf("function");
  });

  test("nests the file record route under the list", () => {
    const record = (storage.routes ?? []).find(
      (route) => route.name === "storage.file",
    );
    expect(record?.path).toBe("/storage/$id");
    expect(record?.parent).toBe("storage.files");
    expect(record?.component).toBeUndefined();
    expect(record?.layout).toBe("console");
  });

  test("exposes a Files menu with a Settings child", () => {
    expect(storage.menus).toHaveLength(1);
    const menu = storage.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("storage");
    expect(menu?.route).toBe("storage.files");
    // Storage is a domain app (top of the rail), so it carries no group.
    expect(menu?.group).toBeUndefined();
    expect(menu?.children?.map((child) => child.route)).toEqual([
      "storage.files",
      "storage.settings",
    ]);
  });

  test("registers its drive glyph", () => {
    expect(storage.icons?.drive).toBeDefined();
    expect(storage.icons?.image).toBeDefined();
  });
});
