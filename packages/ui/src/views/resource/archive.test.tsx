// @vitest-environment happy-dom

import { cleanup, renderHook } from "@testing-library/react";
import { schemaFieldMetadataFromDataResources, type DataResourceFieldMetadata, type DataResourceMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, describe, expect, test } from "vitest";

import { useArchiveActions } from "./archive";

const flag: DataResourceFieldMetadata = {
  name: "archived",
  kind: "scalar",
  scalar: "Boolean",
  readable: true,
  aggregatable: false,
  creatable: true,
  updatable: true,
  requiredOnCreate: false,
  archivable: true,
};
const resource = testDataResource("notes.Note", { fields: [flag], updateFields: [flag.name] });

function archiveActions(value: DataResourceMetadata | null | undefined) {
  const metadata = value ? schemaFieldMetadataFromDataResources([value]).labels[value.modelLabel] : value;
  return renderHook(() => useArchiveActions(metadata));
}

describe("record archive actions", () => {
  afterEach(cleanup);

  test.each([undefined, null, testDataResource("notes.Note")])("no actions without an archivable field (%s)", (value) => {
    expect(archiveActions(value).result.current).toEqual([]);
  });

  test.each([
    { ...resource, roots: { ...resource.roots, update: undefined } },
    { ...resource, updateFields: ["title"] },
    { ...resource, fields: [{ ...flag, updatable: false }] },
  ])("no actions when the update root does not accept the flag (%j)", (value) => {
    expect(archiveActions(value).result.current).toEqual([]);
  });

  test("Archive and Unarchive follow the flag and patch its metadata-owned name", () => {
    const [archive, unarchive] = archiveActions(resource).result.current;
    expect(archive).toMatchObject({ label: "Archive", icon: "archive", placement: "menu", set: { archived: true } });
    expect(unarchive).toMatchObject({ label: "Unarchive", icon: "undo-2", placement: "menu", set: { archived: false } });
    for (const archived of [undefined, null, false, true]) {
      const record = { id: "note-1", archived };
      expect(archive!.visibleWhen!(record)).toBe(archived === false);
      expect(unarchive!.visibleWhen!(record)).toBe(archived === true);
    }
    expect(archive!.visibleWhen!({ id: "note-1" })).toBe(false);
    expect(unarchive!.visibleWhen!({ id: "note-1" })).toBe(false);
    for (const action of [archive!, unarchive!]) {
      expect(action.permission).toBeUndefined();
      expect(action.confirm).toBeUndefined();
      expect(action.run).toBeUndefined();
    }
  });
});
