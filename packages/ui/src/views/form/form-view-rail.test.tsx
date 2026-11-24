import type { ModelMetadata } from "@angee/metadata";
import { describe, expect, test } from "vitest";

import type { ComposedContainerChild } from "../../runtime";
import { RecordRailGroup, displayedRailGroups, recordRailGroups, visibleRecordRailGroups } from "./form-view-rail";

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
  const visible = visibleRecordRailGroups(groups, { id: "note-1", permissions: ["read"] }, {}, metadata);
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
    visibleRecordRailGroups(groups, { id: "note-1", permissions }, {}, metadata).map((group) => group.id);
  expect(visibleTo(["read"])).toEqual(["public"]);
  expect(visibleTo(["read", "manage"])).toEqual(["private", "public"]);
});

describe("displayedRailGroups", () => {
  // `imap.health` is admitted for a record whose kind is imap.
  const admits = (child: string, record: { kind?: string }) => child !== "imap.health" || record.kind === "imap";
  const groups = recordRailGroups([
    railChild("notes.details", {
      content: <>
        <RecordRailGroup id="properties" label="Properties" fields={[
          { field: { name: "due_date" } },
          { field: { name: "private_note" }, permission: "manage" },
        ]} />
        <RecordRailGroup id="settings" label="Settings" permission="manage" fields={[{ field: { name: "due_date" } }]} />
      </>,
    }),
    railChild("notes.private", { permission: "admin", content: <RecordRailGroup id="private" label="Private" content="Private" /> }),
    railChild("imap.health", { impl: "imap", content: <RecordRailGroup id="health" label="Health" fields={[{ field: { name: "secret" } }]} /> }),
  ]);
  const displayed = (record: { id: string; kind?: string; permissions: string[] }) =>
    displayedRailGroups(groups, record, admits).map((group) => [group.id, group.fields?.map(({ field }) => field.name)]);

  test.each<[string, { id: string; kind?: string; permissions: string[] }, unknown[]]>([
    ["a record holding no gate's permission, whose child is not admitted", { id: "n", kind: "mail", permissions: ["read"] },
      [["properties", ["due_date"]]]],
    ["a record holding the group's and the row's permission", { id: "n", kind: "mail", permissions: ["read", "manage"] },
      [["properties", ["due_date", "private_note"]], ["settings", ["due_date"]]]],
    ["a record holding the child's permission", { id: "n", kind: "mail", permissions: ["read", "admin"] },
      [["properties", ["due_date"]], ["private", []]]],
    ["a record admitting the row-decided child", { id: "n", kind: "imap", permissions: ["read"] },
      [["properties", ["due_date"]], ["health", ["secret"]]]],
  ])("%s displays exactly the groups and rows it holds every gate of", (_case, record, expected) => {
    expect(displayed(record)).toEqual(expected);
  });

  test("leaves the metadata's readable rule to the display: an unreadable row stays one the record displays", () => {
    const record = { id: "n", kind: "imap", permissions: ["read"] };
    expect(displayedRailGroups(groups, record, admits).find((group) => group.id === "health")?.fields?.map(({ field }) => field.name)).toEqual(["secret"]);
    expect(visibleRecordRailGroups(groups, record, {}, metadata, admits).find((group) => group.id === "health")).toBeUndefined();
  });

  test("two alternatives a record admits together clash whatever their permissions", () => {
    const clashing = recordRailGroups([
      railChild("imap.health", { impl: "imap", content: <RecordRailGroup id="health" label="Health" content="IMAP" /> }),
      railChild("imap.health-admin", { impl: "imap", permission: "admin", content: <RecordRailGroup id="health" label="Health" content="Admin" /> }),
    ]);
    expect(() => displayedRailGroups(clashing, { id: "n", kind: "imap", permissions: ["read"] }, admits))
      .toThrow(/duplicate record rail group id "health"/);
  });

  test("guard: without a record the rail shows no group", () => {
    expect(visibleRecordRailGroups(groups, null, {}, metadata, admits)).toEqual([]);
  });
});

test("rail group ids fail fast on collision", () => {
  const entries = [railChild("notes.one", {
    content: <><RecordRailGroup id="properties" label="First" />
      <RecordRailGroup id="properties" label="Second" /></>,
  })];
  expect(() => recordRailGroups(entries)).toThrow(/duplicate record rail group id/);
});

test("alternatives the record chooses between may share a group id, never with a fixed group", () => {
  const original = railChild("notes.health", { content: <RecordRailGroup id="health" label="Health" content="Generic" /> });
  const variant = railChild("chat.health", {
    variant: { of: "notes.health", impl: "chat" },
    content: <RecordRailGroup id="health" label="Health" content="Chat" />,
  });
  const groups = recordRailGroups([original, variant]);
  const admits = (child: string, record: { kind?: string }) => (child === "chat.health") === (record.kind === "chat");
  expect(visibleRecordRailGroups(groups, { id: "n", kind: "chat" }, {}, metadata, admits).map((group) => group.content)).toEqual(["Chat"]);
  expect(visibleRecordRailGroups(groups, { id: "n", kind: "mail" }, {}, metadata, admits).map((group) => group.content)).toEqual(["Generic"]);
  const fixed = railChild("notes.status", { content: <RecordRailGroup id="health" label="Status" /> });
  expect(() => recordRailGroups([fixed, variant])).toThrow(/duplicate record rail group id "health"/);
});
