import { expectValidBaseAddon } from "@angee/app/testing";
import { STORAGE_MOUNT_TOOLBAR_SLOT } from "@angee/storage-integrate";
import { describe, expect, test } from "vitest";

import storageIntegrateIphone from "./index";

describe("angee.storage_integrate_iphone addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(storageIntegrateIphone)).not.toThrow();
  });

  test("contributes the iPhone connection action to the Mount toolbar", () => {
    expect(storageIntegrateIphone.slots?.[0]).toMatchObject({
      slot: STORAGE_MOUNT_TOOLBAR_SLOT,
      id: "storage-integrate-iphone.connect",
      sequence: 20,
    });
    expect(
      storageIntegrateIphone.i18n?.storage?.["iphone.mount.connect.button"],
    ).toBe("Connect iPhone backup");
  });
});
