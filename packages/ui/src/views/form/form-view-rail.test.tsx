import type { ModelMetadata } from "@angee/metadata";
import { expect, test } from "vitest";

import type { ComposedContainerChild } from "../../runtime";
import { RecordRailGroup, recordRailGroups, visibleRecordRailGroups } from "./form-view-rail";

const metadata = { fields: {
  due_date: { readable: true },
  private_note: { readable: true },
  secret: { readable: false },
} } as unknown as ModelMetadata;

const railChild = (id: string, child: Omit<ComposedContainerChild, "id" | "owner" | "address">): ComposedContainerChild =>
  ({ ...child, id, owner: id.split(".")[0]!, address: "notes.Note#rail" });

test("rail declarations keep only readable groups and field rows", () => {
  const entries = [railChild("notes.details", {
    content: <>
      <RecordRailGroup id="properties" label="Properties" fields={[
        { field: { name: "due_date" } },
        { field: { name: "private_note" }, permission: "manage" },
        { field: { name: "secret" } },
      ]} />
      <RecordRailGroup id="settings" label="Settings" permission="manage"
        fields={[{ field: { name: "private_note" } }]} />
    </>,
  })];
  const groups = recordRailGroups(entries);
  const visible = visibleRecordRailGroups(groups, { id: "note-1", permissions: ["read"] }, metadata);
  expect(visible.map((group) => group.id)).toEqual(["properties"]);
  expect(visible[0]?.fields?.map(({ field }) => field.name)).toEqual(["due_date"]);
});

test("a child's permission gates each group it declares without one of its own", () => {
  const entries = [
    railChild("notes.private", {
      permission: "manage",
      content: <RecordRailGroup id="private" label="Private" fields={[{ field: { name: "due_date" } }]} />,
    }),
    railChild("notes.public", {
      content: <RecordRailGroup id="public" label="Public" fields={[{ field: { name: "due_date" } }]} />,
    }),
  ];
  const groups = recordRailGroups(entries);
  expect(groups.map(({ id, permission, childPermission }) => ({ id, permission, childPermission }))).toEqual([
    { id: "private", permission: undefined, childPermission: "manage" },
    { id: "public", permission: undefined, childPermission: undefined },
  ]);
  const visibleTo = (permissions: string[]) =>
    visibleRecordRailGroups(groups, { id: "note-1", permissions }, metadata).map((group) => group.id);
  expect(visibleTo(["read"])).toEqual(["public"]);
  expect(visibleTo(["read", "manage"])).toEqual(["private", "public"]);
});

test("rail group ids fail fast on collision", () => {
  const entries = [railChild("notes.one", {
    content: <><RecordRailGroup id="properties" label="First" />
      <RecordRailGroup id="properties" label="Second" /></>,
  })];
  expect(() => recordRailGroups(entries)).toThrow(/duplicate record rail group id/);
});
