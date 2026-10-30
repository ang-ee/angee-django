import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import type { ContainerChild, RelatedGroupContent } from "@angee/ui";
import { describe, expect, test } from "vitest";

import { enStorageMessages } from "./i18n";
import storage from "./index";

const declaredMenus = composeAddons([{ id: storage.id, menus: storage.menus, routes: storage.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("storage addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(storage)).not.toThrow();
  });

  test("registers the files route on the console layout with a component", () => {
    const routes = storage.routes ?? [];
    expect(routes).toHaveLength(3);
    expect(routes[0]?.name).toBe("storage.files");
    expect(routes[0]?.path).toBe("/storage");
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
    expect((storage.routes ?? []).find((route) => route.name === "storage.files")?.resource).toBe(
      "storage.File",
    );
    expect(record?.path).toBe("/storage/$id");
    expect(record?.parent).toBe("storage.files");
    expect(record?.component).toBeUndefined();
  });

  test("exposes a Files menu with a Settings child", () => {
    expect(declaredMenus).toHaveLength(1);
    const menu = declaredMenus?.[0];
    expect(menu?.id).toBe("storage");
    expect(menu?.route).toBe("storage.files");
    // Storage is a domain app (top of the rail), so it carries no group.
    expect(menu?.group).toBeUndefined();
    expect(menu?.children?.map((child) => child.route)).toEqual([
      "storage.files",
      "storage.settings",
    ]);
    expect(enStorageMessages["loading"]).toBe("Loading files…");
    expect(enStorageMessages["drives.unavailableTitle"]).toBe(
      "Files unavailable",
    );
    expect(enStorageMessages["drives.emptyDescription"]).toBe(
      "No drives are available to you.",
    );
    expect(enStorageMessages["settings.backends.description"]).toBe(
      "Infrastructure a drive is created against.",
    );
  });

  test("offers the Files group of Related only where the record's type carries file attachments", () => {
    const tab = (storage.containers?.["related#groups"] as Record<string, ContainerChild<RelatedGroupContent>>)["storage.files"]!;
    expect(storage.containers?.["record#aside"]).toBeUndefined();
    expect(tab.content.edge).toEqual({ model: "storage.FileAttachment" });
    expect(tab.content.when).toBeUndefined();
  });

  test("registers its drive glyph", () => {
    expect(storage.icons?.drive).toBeDefined();
    expect(storage.icons?.image).toBeDefined();
  });
});
