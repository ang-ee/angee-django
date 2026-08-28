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
    expect(Object.keys(storageIntegrate.i18n?.storage ?? {}).sort()).toEqual([
      "mount.action.sync",
      "mount.browse.alreadyMounted",
      "mount.browse.empty",
      "mount.browse.error",
      "mount.browse.loading",
      "mount.browse.manualHint",
      "mount.browse.notReadable",
      "mount.browse.truncated",
      "mount.browse.up",
      "mount.browse.useThisFolder",
      "mount.connect.mode",
      "mount.connect.modeCopy",
      "mount.connect.modeReference",
      "mount.connect.name",
      "mount.connect.sourceFolder",
      "mount.connect.submit",
      "mount.connect.submitting",
      "mount.group.sync",
      "mount.localFolder.button",
      "mount.localFolder.description",
      "mount.localFolder.error",
      "mount.localFolder.namePlaceholder",
      "mount.localFolder.title",
      "mount.name",
    ]);
  });
});
