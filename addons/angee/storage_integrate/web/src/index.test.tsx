import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import storageIntegrate from "./index";
import { STORAGE_MOUNT_TOOLBAR_SLOT } from "./slots";

describe("angee.storage_integrate addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(storageIntegrate)).not.toThrow();
  });

  test("contributes local-folder connection through the Mount toolbar slot", () => {
    expect(storageIntegrate.slots?.[0]).toMatchObject({
      slot: STORAGE_MOUNT_TOOLBAR_SLOT,
      id: "storage-integrate.connect-local-folder",
      sequence: 10,
    });
  });
});
