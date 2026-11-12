// @vitest-environment happy-dom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { expect, test } from "vitest";
import { AuthStateProvider } from "@angee/app";
import type { DataResourceFieldMetadata } from "@angee/metadata";
import type { RecordPanelContext } from "@angee/ui";
import {
  RoutedRuntimeFixture, storySchema, testDataResource, testQueryField, testResourceQuery, type GraphQLWireRequest,
} from "@angee/storybook/testing";

import { useGroupRecordTabs } from "./group-record-tabs";

const field = (name: string, scalar = "String"): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar, readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
});
const choices = (name: string, values: readonly string[]): DataResourceFieldMetadata => ({
  ...field(name), kind: "enum", widget: "select",
  values: values.map((value) => ({ value, description: value.charAt(0) + value.slice(1).toLowerCase() })),
});
const queryFields = (names: readonly string[]) => testResourceQuery({ fields: Object.fromEntries(names.map((name) => [name, testQueryField(name)])) });
const resources = [
  testDataResource("spaces.Membership", {
    roots: { list: "space_memberships", aggregate: "space_memberships_aggregate", detail: "space_memberships_by_pk" },
    typeNames: { node: "SpaceMembershipType", filter: "space_memberships_bool_exp", order: "space_memberships_order_by" },
    fields: [field("id"), choices("role", ["OWNER", "MODERATOR", "MEMBER", "VIEWER"]),
      choices("notification_policy", ["INBOX", "EMAIL", "MUTED"]), field("is_confirmed", "Boolean"), field("created_at", "DateTime")],
    query: queryFields(["id", "group", "party.display_name", "role", "is_confirmed", "created_at"]),
  }),
  testDataResource("spaces.GroupThread", {
    capabilities: ["list", "detail"],
    roots: { list: "space_threads", aggregate: "space_threads_aggregate", detail: "space_threads_by_pk" },
    typeNames: { node: "SpaceThreadType", filter: "space_threads_bool_exp", order: "space_threads_order_by" },
    fields: [field("id"), field("message_count", "Int"), field("last_message_at", "DateTime")],
    query: queryFields(["id", "groups", "title.text", "message_count", "last_message_at"]),
  }),
  testDataResource("parties.Party", {
    recordRepresentation: "display_name",
    roots: { list: "parties", aggregate: "parties_aggregate", detail: "parties_by_pk" },
    typeNames: { node: "PartyType", filter: "parties_bool_exp", order: "parties_order_by" },
    fields: [field("id"), field("display_name")],
    query: queryFields(["id", "display_name"]),
  }),
];

const memberships = [
  { id: "mem_ada", party: { display_name: "Ada Lovelace" }, role: "MEMBER", is_confirmed: true, created_at: "2026-10-01T09:00:00Z",
    permissions: ["write", "write__role", "delete"], notification_policy: "INBOX", subtype_keys: [] },
  { id: "mem_self", party: { display_name: "Grace Hopper" }, role: "OWNER", is_confirmed: true, created_at: "2026-10-02T09:00:00Z",
    permissions: ["set_notifications"], notification_policy: "EMAIL", subtype_keys: ["comment"] },
];
const threadsByGroup: Record<string, readonly Record<string, unknown>[]> = {
  grp_1: [
    { id: "thr_1", title: { text: "Primary" }, groups: [{ id: "grp_1", name: "Community" }], message_count: 2, last_message_at: null },
    { id: "thr_2", title: { text: "Side thread" }, groups: [{ id: "grp_1", name: "Community" }, { id: "grp_2", name: "Moderators" }],
      message_count: 1, last_message_at: null },
  ],
  grp_2: [{ id: "thr_3", title: { text: "Other group" }, groups: [{ id: "grp_2", name: "Moderators" }], message_count: 1, last_message_at: null }],
  grp_empty: [],
};
const emptyFeed = { count: 0, older_cursor: null, has_older: false, has_more_in_window: false, has_older_than_through: false,
  newer_cursor: null, has_newer: false, has_newer_than_before: false, messages: [] };
const count = (rows: readonly unknown[]) => ({ aggregate: { count: rows.length } });
const scoped = (args: Record<string, unknown>) => {
  const where = JSON.stringify(args.where ?? {});
  return Object.entries(threadsByGroup).find(([group]) => where.includes(`"${group}"`))?.[1] ?? [];
};

function groupSchemas(requests: GraphQLWireRequest[]) {
  const fixture = storySchema({
    space_memberships: () => memberships,
    space_memberships_aggregate: () => count(memberships),
    update_space_memberships_by_pk: (args) => ({ id: (args.pk_columns as { id: string }).id, role: (args._set as { role: string }).role }),
    set_membership_notifications: (args) => ({ id: args.id, notification_policy: args.policy, subtype_keys: args.subtype_keys }),
    space_threads: scoped,
    space_threads_aggregate: (args) => count(scoped(args)),
    space_threads_by_pk: (args) => Object.values(threadsByGroup).flat().find((thread) => thread.id === args.id) ?? null,
    thread_message_feed: () => emptyFeed,
    parties: () => [{ id: "pty_alan", display_name: "Alan Turing" }],
    parties_aggregate: () => ({ aggregate: { count: 1 } }),
  }, (request) => requests.push(request)).public!;
  return { console: { ...fixture, metadata: { angee: { resources } } } };
}

/** One record tab as the group page mounts it, active, with the panel context FormView passes. */
function GroupTab({ tab, recordId, record }: { tab: "roster" | "threads"; recordId: string; record: Record<string, unknown> }): ReactElement {
  const entry = useGroupRecordTabs().find(({ id }) => id === tab)!;
  // The tabs read only the loaded group record from the form's save surface.
  const form = { displayRecord: record } as unknown as RecordPanelContext["form"];
  return <>{entry.render({ recordId, reload: () => undefined, focusField: () => undefined, form, active: true })}</>;
}

// The transcript's keyset feed reads only for a signed-in actor.
const SIGNED_IN = { user: { id: "usr_grace", name: "Grace Hopper" }, status: "authenticated" as const, hasRole: () => false };

function renderGroupTab(tab: "roster" | "threads", membershipRoles: readonly string[] = ["OWNER", "MODERATOR", "MEMBER", "VIEWER"]) {
  const requests: GraphQLWireRequest[] = [];
  const schemas = groupSchemas(requests);
  const record = { permissions: ["write", "manage_roster"], membership_roles: membershipRoles };
  const host = (recordId: string) => <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/spaces">
    <AuthStateProvider auth={SIGNED_IN}><GroupTab tab={tab} recordId={recordId} record={record} /></AuthStateProvider>
  </RoutedRuntimeFixture>;
  const view = render(host("grp_1"));
  return { requests, showGroup: (recordId: string) => view.rerender(host(recordId)) };
}

async function membershipRow(name: string): Promise<HTMLElement> {
  const cell = await screen.findByText(name);
  const row = cell.closest<HTMLElement>("tr, [role='row']");
  if (!row) throw new Error(`No row holds ${name}.`);
  return row;
}

async function choose(dialog: HTMLElement, label: string, option: string) {
  fireEvent.click(within(dialog).getByRole("combobox", { name: label }));
  const choice = await screen.findByRole("option", { name: option });
  fireEvent.pointerDown(choice, { pointerType: "mouse", button: 0 });
  fireEvent.click(choice);
  await waitFor(() => expect(within(dialog).getByRole("combobox", { name: label }).textContent).toContain(option));
}

test("the roster lists the open group's members and offers each row only the verbs its permissions allow", async () => {
  const { requests } = renderGroupTab("roster");
  const ada = await membershipRow("Ada Lovelace");
  const grace = await membershipRow("Grace Hopper");
  expect(requests.find(({ fields }) => fields.includes("space_memberships"))?.variables.where).toEqual({ _and: [{ group: { _eq: "grp_1" } }] });
  expect(within(ada).getByRole("button", { name: "Change role" })).toBeTruthy();
  expect(within(ada).getByRole("button", { name: "Remove" })).toBeTruthy();
  expect(within(ada).queryByRole("button", { name: "Notifications" })).toBeNull();
  expect(within(grace).getByRole("button", { name: "Notifications" })).toBeTruthy();
  expect(within(grace).queryByRole("button", { name: "Change role" })).toBeNull();
  expect(within(grace).queryByRole("button", { name: "Remove" })).toBeNull();
});

test("a role change submits the chosen role in the update's lowercase wire casing", async () => {
  const { requests } = renderGroupTab("roster");
  fireEvent.click(within(await membershipRow("Ada Lovelace")).getByRole("button", { name: "Change role" }));
  const dialog = await screen.findByRole("dialog", { name: "Change role" });
  expect(within(dialog).getByRole("combobox", { name: "Role" }).textContent).toContain("Member");
  await choose(dialog, "Role", "Moderator");
  fireEvent.click(within(dialog).getByRole("button", { name: "Save role" }));
  await waitFor(() => expect(requests.find(({ fields }) => fields.includes("update_space_memberships_by_pk"))?.variables)
    .toEqual({ id: "mem_ada", role: "moderator" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Change role" })).toBeNull());
});

test("notification preferences submit the chosen policy and keep the member's subtype choices", async () => {
  const { requests } = renderGroupTab("roster");
  fireEvent.click(within(await membershipRow("Grace Hopper")).getByRole("button", { name: "Notifications" }));
  const dialog = await screen.findByRole("dialog", { name: "Notifications" });
  expect(within(dialog).getByRole("combobox", { name: "Notifications" }).textContent).toContain("Email");
  await choose(dialog, "Notifications", "Muted");
  fireEvent.click(within(dialog).getByRole("button", { name: "Save preferences" }));
  await waitFor(() => expect(requests.find(({ fields }) => fields.includes("set_membership_notifications"))?.variables)
    .toEqual({ id: "mem_self", policy: "MUTED", subtype_keys: ["comment"] }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Notifications" })).toBeNull());
});

test("adding a member offers exactly the roles the server returned for this seat", async () => {
  renderGroupTab("roster", ["MEMBER", "VIEWER"]);
  fireEvent.click(await screen.findByRole("button", { name: "Add member" }));
  const dialog = await screen.findByRole("dialog", { name: "Add member" });
  expect(within(dialog).getByRole("combobox", { name: "Role" }).textContent).toContain("Member");
  fireEvent.click(within(dialog).getByRole("combobox", { name: "Role" }));
  expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(["Member", "Viewer"]);
});

test("a seat the server grants no role to has no add verb", async () => {
  renderGroupTab("roster", []);
  await membershipRow("Ada Lovelace");
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
});

test("opening another group drops the open roster dialog", async () => {
  const { showGroup } = renderGroupTab("roster");
  fireEvent.click(await screen.findByRole("button", { name: "Add member" }));
  expect(await screen.findByRole("dialog", { name: "Add member" })).toBeTruthy();
  showGroup("grp_2");
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add member" })).toBeNull());
});

test("the threads tab opens the group's first conversation, follows the picked one and restarts for another group", async () => {
  const { requests, showGroup } = renderGroupTab("threads");
  const transcript = () => requests.filter(({ fields }) => fields.includes("thread_message_feed")).at(-1)?.variables.threadId;
  await waitFor(() => expect(transcript()).toBe("thr_1"));
  expect(requests.find(({ fields }) => fields.includes("space_threads"))?.variables.where).toEqual({ _and: [{ groups: { _eq: "grp_1" } }] });
  fireEvent.click(await screen.findByText("Side thread"));
  await waitFor(() => expect(transcript()).toBe("thr_2"));
  showGroup("grp_2");
  await waitFor(() => expect(transcript()).toBe("thr_3"));
  showGroup("grp_empty");
  expect((await screen.findAllByText("No group conversations in this space yet.")).length).toBeGreaterThan(0);
});
