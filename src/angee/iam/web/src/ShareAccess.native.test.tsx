// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { expect, test } from "vitest";
import type { DataResourceMetadata } from "@angee/metadata";
import { RESOURCE_CONTAINERS, RecordChromeProvider, ResourceViewUtilities, containersFromChildren } from "@angee/ui";
import { RoutedRuntimeFixture, RuntimeFixture, storySchema, testDataResource, type GraphQLWireRequest } from "@angee/storybook/testing";

import { ShareAccessDialog, ShareListChrome, ShareRecordChrome, useAccessRole } from "./ShareAccess";

const reader = { relation: "reader", permission: "share", subjects: [{ type: "auth/user", relation: null, resource: "iam.User" }] };
const note = (grantable: DataResourceMetadata["grantable"] = [reader]) => testDataResource("notes.Note", {
  resourceType: "notes/note", recordRepresentation: "title", grantable,
});
const user = testDataResource("iam.User", { recordRepresentation: "display_name" });

/** The IAM access surface over the wire: readers load with the record, direct access when the panel opens. */
function renderShare(content: ReactNode, { resource = note(), access = [] as readonly Record<string, unknown>[], containers, routed = false }: {
  resource?: DataResourceMetadata; access?: readonly Record<string, unknown>[]; containers?: ReturnType<typeof containersFromChildren>;
  /** Mount under the native router, for the add-person picker's route hooks. */
  routed?: boolean;
} = {}): GraphQLWireRequest[] {
  const requests: GraphQLWireRequest[] = [];
  const schemas = { console: { ...storySchema({
    record_readers: () => [{ subject: "auth/user:ada", label: "Ada", you: true }],
    record_access: () => access,
    record_access_options: () => [{ relation: "reader", permission: "share" }],
    users: () => [],
    users_aggregate: () => ({ aggregate: { count: 0 } }),
  }, (request) => requests.push(request)).public!, metadata: { angee: { resources: [resource, user] } } } };
  const runtime = containers ? { containers } : {};
  render(routed
    ? <RoutedRuntimeFixture activeSchema="console" schemas={schemas} runtime={runtime} collectionPath="/notes">{content}</RoutedRuntimeFixture>
    : <RuntimeFixture activeSchema="console" schemas={schemas} runtime={runtime}>{content}</RuntimeFixture>);
  return requests;
}

function OnRecord({ record, children }: { record: Record<string, unknown>; children: ReactNode }): ReactElement {
  return <RecordChromeProvider value={{ resource: "notes.Note", canonicalResource: "notes.Note", dataProviderName: "console",
    recordId: "note-1", record: { id: "note-1", ...record }, formReadOnly: false }}>{children}</RecordChromeProvider>;
}

const accessRead = (requests: readonly GraphQLWireRequest[]) => requests.find(({ fields }) => fields.includes("record_access"));

test("a record's Share reads its readers at once and its direct access only when opened", async () => {
  const requests = renderShare(<OnRecord record={{ title: "Welcome", permissions: ["share"] }}><ShareRecordChrome /></OnRecord>);
  await waitFor(() => expect(requests.find(({ fields }) => fields.includes("record_readers"))?.variables)
    .toEqual({ targetType: "notes/note", targetId: "note-1" }));
  expect(accessRead(requests)).toBeUndefined();
  fireEvent.click(await screen.findByRole("button", { name: "Share" }));
  expect(await screen.findByRole("heading", { name: "Share Welcome" })).toBeTruthy();
  await waitFor(() => expect(accessRead(requests)?.variables).toEqual({ targetType: "notes/note", targetIds: ["note-1"] }));
});

test.each([
  ["the record holds the model's grant permission", note(), { permissions: ["share"] }, true],
  ["the record lacks it", note(), { permissions: [] }, false],
  ["the model projects no permissions", note(), {}, true],
  ["the model declares another permission", note([{ ...reader, permission: "manage" }]), { permissions: ["manage"] }, true],
  ["the model declares no grant surface", note([]), { permissions: ["share"] }, false],
])("Share shows when %s", async (_case, resource, record, shown) => {
  renderShare(<OnRecord record={{ title: "Welcome", ...record }}><ShareRecordChrome /></OnRecord>, { resource });
  // Metadata and the record are synchronous, so the decision is made on first render.
  if (shown) expect(await screen.findByRole("button", { name: "Share" })).toBeTruthy();
  else expect(screen.queryByRole("button", { name: "Share" })).toBeNull();
});

test("a collection shares its selected records in a stable order", async () => {
  const containers = containersFromChildren(RESOURCE_CONTAINERS, {
    "notes.Note#utilities": { "iam.share": { content: <ShareListChrome /> } },
  });
  const requests = renderShare(<ResourceViewUtilities value={{ resource: "notes.Note", fields: [], refresh: () => undefined,
    selectedIds: new Set(["note-2", "note-1"]), selectable: true }} />, { containers });
  fireEvent.click(await screen.findByRole("button", { name: "Share" }));
  expect(await screen.findByRole("heading", { name: "Share 2 selected records" })).toBeTruthy();
  await waitFor(() => expect(accessRead(requests)?.variables).toEqual({ targetType: "notes/note", targetIds: ["note-1", "note-2"] }));
  expect(requests.some(({ fields }) => fields.includes("record_readers"))).toBe(false);
});

test("a collection without selection offers no Share", async () => {
  const containers = containersFromChildren(RESOURCE_CONTAINERS, {
    "notes.Note#utilities": { "iam.share": { content: <ShareListChrome /> } },
  });
  renderShare(<ResourceViewUtilities value={{ resource: "notes.Note", fields: [], refresh: () => undefined, selectable: false }} />, { containers });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Share" })).toBeNull());
});

test("an external action opens the same access panel for its records", async () => {
  const requests = renderShare(<ShareAccessDialog resource="notes.Note" targetIds={["note-1"]} label="Review folder"
    open onOpenChange={() => undefined} trigger={null} />);
  expect(await screen.findByRole("heading", { name: "Share Review folder" })).toBeTruthy();
  await waitFor(() => expect(accessRead(requests)?.variables).toEqual({ targetType: "notes/note", targetIds: ["note-1"] }));
});

test("existing direct access shows the model's declared relation label", async () => {
  renderShare(<OnRecord record={{ title: "Welcome", permissions: ["share"] }}><ShareRecordChrome /></OnRecord>, {
    resource: note([{ ...reader, label: "Can read" }]),
    access: [{ target_id: "note-1", relation: "reader", subject: "auth/user:ada", subject_type: "auth/user", label: "Ada" }],
  });
  fireEvent.click(await screen.findByRole("button", { name: "Share" }));
  expect((await screen.findAllByText(/Can read/)).length).toBeGreaterThan(0);
});

test("a model-scoped role opens Share without direct grant relations", async () => {
  const role = { id: "round.responder", label: "Responder", subjectResource: "iam.User", offered: true };
  const state = { role, people: [], add: async () => true, remove: async () => undefined };
  function ResponderRole(): null { useAccessRole(role.id, state); return null; }
  // IAM's manifest declares its access containers per model; a runtime resolves only declared addresses.
  // ShareAccess keys a registered role by its container child's id, so the child carries the role's id.
  const containers = containersFromChildren([{ address: "iam#access-roles", models: true }, { address: "iam#access-visibility", models: true }],
    { "notes.Note#access-roles": { [role.id]: { content: ResponderRole } } });
  renderShare(<OnRecord record={{ title: "Welcome", permissions: [] }}><ShareRecordChrome /></OnRecord>, { resource: note([]), containers, routed: true });
  fireEvent.click(await screen.findByRole("button", { name: "Share" }));
  // The offered role is the panel's add-person choice.
  const add = await screen.findByRole("region", { name: "Add a person" });
  expect(within(add).getByText("Responder")).toBeTruthy();
});
