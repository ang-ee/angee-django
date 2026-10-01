import { expect, test } from "vitest";

import { createKeyedEntry, keyedCollectionFromRecord, keyedCollectionToRecord } from "./keyed-collection";

test("loaded keys become stable identities and renaming changes only the editable key", () => {
  const source = { first: { label: "One" }, second: { label: "Two" } };
  const entries = keyedCollectionFromRecord(source);
  entries[0]!.key = "renamed";
  entries[0]!.value.label = "Edited";
  const saved = keyedCollectionToRecord(entries);
  expect(saved.status).toBe("ok");
  if (saved.status !== "ok") throw new Error("Expected a valid snapshot");
  expect(entries.map((entry) => entry.clientId)).toEqual(["first", "second"]);
  expect(saved.data.values).toEqual({ renamed: { label: "Edited" }, second: { label: "Two" } });
  expect(saved.data.keyByClientId.get("first")).toBe("renamed");
  expect(saved.data.clientIdByKey.get("renamed")).toBe("first");
  expect(source.first.label).toBe("One");
});

test("new entries get generated identities independent of their editable keys", () => {
  const first = createKeyedEntry({ label: "One" }, "first");
  const second = createKeyedEntry({ label: "Two" }, "second");
  expect(first.clientId).toBeTruthy();
  expect(first.clientId).not.toBe(first.key);
  expect(second.clientId).not.toBe(first.clientId);
  const saved = keyedCollectionToRecord([first, second]);
  expect(saved).toMatchObject({ status: "ok", data: { values: { first: { label: "One" }, second: { label: "Two" } } } });
});

test("submit maps and values stay fixed while later edits are made", () => {
  const entries = keyedCollectionFromRecord({ first: { label: "One" } });
  entries[0]!.key = "submitted";
  const saved = keyedCollectionToRecord(entries);
  if (saved.status !== "ok") throw new Error("Expected a valid snapshot");
  entries[0]!.key = "later";
  entries[0]!.value.label = "Later";
  expect(saved.data.clientIdByKey.get("submitted")).toBe("first");
  expect(saved.data.clientIdByKey.has("later")).toBe(false);
  expect(saved.data.values).toEqual({ submitted: { label: "One" } });
});

test("duplicate keys return issues for each client identity without a partial snapshot", () => {
  expect(keyedCollectionToRecord([
    { clientId: "one", key: "same", value: 1 }, { clientId: "two", key: "same", value: 2 },
  ])).toEqual({ status: "invalid", issues: {
    fieldErrors: { one: ["Duplicate collection key: same"], two: ["Duplicate collection key: same"] }, formErrors: [],
  } });
});

test("integer keys preserve authored array order across editing and save mapping", () => {
  const entries = [createKeyedEntry("Ten", "10"), createKeyedEntry("Two", "2")];
  entries[0]!.key = "20";
  const saved = keyedCollectionToRecord(entries);
  if (saved.status !== "ok") throw new Error("Expected a valid snapshot");
  expect(entries.map((entry) => entry.key)).toEqual(["20", "2"]);
  expect([...saved.data.keyByClientId.values()]).toEqual(["20", "2"]);
  expect([...saved.data.clientIdByKey.keys()]).toEqual(["20", "2"]);
});

test("special object keys round-trip without prototype or inherited-entry interference", () => {
  const source = Object.fromEntries([["__proto__", 1], ["constructor", 2], ["", 3]]);
  const saved = keyedCollectionToRecord(keyedCollectionFromRecord(source));
  if (saved.status !== "ok") throw new Error("Expected a valid snapshot");
  expect(Object.entries(saved.data.values)).toEqual(Object.entries(source));
  expect(saved.data.clientIdByKey.get("__proto__")).toBe("__proto__");
  expect(keyedCollectionToRecord([])).toMatchObject({ status: "ok", data: { values: {} } });
});
