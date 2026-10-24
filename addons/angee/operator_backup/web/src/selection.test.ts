import { expect, test } from "vitest";
import * as v from "valibot";
import { applyInventorySelection, BackupSelectionSchema, defaultBackupSelection } from "./selection";

test("checking an excluded resource includes it without changing other groups or the source policy", () => {
  const original = defaultBackupSelection();
  original.sources = { mode: "none", names: [], exclude: ["framework"], policy: "fetch_published" };
  const changed = applyInventorySelection(original, [{ group: "sources", name: "framework" }], "include");
  expect(changed.sources).toEqual({ mode: "selected", names: ["framework"], exclude: [], policy: "fetch_published" });
  expect(changed.databases).toBe(original.databases);
  expect(original.sources.mode).toBe("none");
  expect(original.sources.exclude).toEqual(["framework"]);
});

test("excluding checks retains All discovered and deduplicates exclusions", () => {
  const original = defaultBackupSelection();
  const resources = [{ group: "service_state", name: "cache" }, { group: "service_state", name: "cache" }];
  const changed = applyInventorySelection(original, resources, "exclude");
  expect(changed.service_state).toEqual({ mode: "all", names: [], exclude: ["cache"] });
  expect(original.service_state.exclude).toEqual([]);
});

test("the JSON boundary rejects unsupported selection versions and source policies", () => {
  expect(v.safeParse(BackupSelectionSchema, { ...defaultBackupSelection(), version: 2 }).success).toBe(false);
  const invalid = defaultBackupSelection();
  expect(v.safeParse(BackupSelectionSchema, { ...invalid, sources: { ...invalid.sources, policy: "skip_local" } }).success).toBe(false);
});
