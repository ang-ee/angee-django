import type { ModelMetadata } from "@angee/metadata";
import { expect, test } from "vitest";

import type { SlotContribution } from "../../runtime";
import { RecordRailGroup, recordRailGroups, visibleRecordRailGroups } from "./form-view-rail";

const metadata = { fields: {
  due_date: { readable: true },
  private_note: { readable: true },
  secret: { readable: false },
} } as unknown as ModelMetadata;

test("rail declarations keep only readable groups and field rows", () => {
  const entries: readonly SlotContribution[] = [{
    id: "details", slot: "form-view.rail", model: "notes.Note",
    content: <>
      <RecordRailGroup id="properties" label="Properties" fields={[
        { field: { name: "due_date" } },
        { field: { name: "private_note" }, permission: "manage" },
        { field: { name: "secret" } },
      ]} />
      <RecordRailGroup id="settings" label="Settings" permission="manage"
        fields={[{ field: { name: "private_note" } }]} />
    </>,
  }];
  const groups = recordRailGroups(entries);
  const visible = visibleRecordRailGroups(groups, { id: "note-1", permissions: ["read"] }, metadata);
  expect(visible.map((group) => group.id)).toEqual(["properties"]);
  expect(visible[0]?.fields?.map(({ field }) => field.name)).toEqual(["due_date"]);
});

test("rail group ids fail fast on collision", () => {
  const entries: readonly SlotContribution[] = [{
    id: "one", slot: "form-view.rail", model: "notes.Note",
    content: <><RecordRailGroup id="properties" label="First" />
      <RecordRailGroup id="properties" label="Second" /></>,
  }];
  expect(() => recordRailGroups(entries)).toThrow(/duplicate record rail group id/);
});
