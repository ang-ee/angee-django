import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import storageIntegrate from "@angee/storage-integrate";
import { describe, expect, test } from "vitest";

import storageIntegrateIphone from "./index";

describe("angee.storage_integrate_iphone addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(storageIntegrateIphone)).not.toThrow();
  });

  test("contributes the iPhone connection action to the Mount toolbar", () => {
    // The Mount toolbar's owner declares the container; this addon adds its verb after local folders.
    const { containers } = composeAddons(
      [{ id: storageIntegrate.id, containers: storageIntegrate.containers }, { ...storageIntegrateIphone, dependsOn: ["storage-integrate"] }],
      { canonicalModelLabel: (model) => model },
    );
    expect(containers.children["storage-integrate.mounts#toolbar"]?.map(({ id, sequence }) => ({ id, sequence }))).toEqual([
      { id: "storage-integrate.connect-local-folder", sequence: 10 },
      { id: "storage-integrate-iphone.connect", sequence: 20 },
    ]);
    expect(
      storageIntegrateIphone.i18n?.storage?.["iphone.mount.connect.button"],
    ).toBe("Connect iPhone backup");
    expect(Object.keys(storageIntegrateIphone.i18n?.storage ?? {}).sort()).toEqual([
      "iphone.mount.connect.button",
      "iphone.mount.connect.description",
      "iphone.mount.connect.error",
      "iphone.mount.connect.namePlaceholder",
      "iphone.mount.connect.title",
    ]);
  });
});
