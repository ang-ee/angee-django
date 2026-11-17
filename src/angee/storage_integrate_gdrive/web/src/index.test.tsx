import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import storageIntegrate from "@angee/storage-integrate";
import { describe, expect, test } from "vitest";

import storageIntegrateGdrive from "./index";

describe("angee.storage_integrate_gdrive addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(storageIntegrateGdrive)).not.toThrow();
  });

  test("contributes the Google Drive connection action to the Mount toolbar", () => {
    // The Mount toolbar's owner declares the container; this addon adds its verb after local folders.
    const { containers } = composeAddons(
      [{ id: storageIntegrate.id, containers: storageIntegrate.containers }, { ...storageIntegrateGdrive, dependsOn: ["storage-integrate"] }],
      { canonicalModelLabel: (model) => model },
    );
    expect(containers.children["storage-integrate.mounts#toolbar"]?.map(({ id, sequence }) => ({ id, sequence }))).toEqual([
      { id: "storage-integrate.connect-local-folder", sequence: 10 },
      { id: "storage-integrate-gdrive.connect", sequence: 30 },
    ]);
  });
});
