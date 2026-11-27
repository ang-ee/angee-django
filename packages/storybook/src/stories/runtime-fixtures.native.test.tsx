// @vitest-environment happy-dom
// The workshop's shared runtime fixture, as the stories and the fragments' tests rely on it.

import { act, render } from "@testing-library/react";
import { expect, test } from "vitest";
import { retryAppQuery } from "@angee/app/create-app";
import { useAuthoredMutation, useQueryClient, type AuthoredMutate, type AuthoredMutateOptions, type TypedDocumentNode } from "@angee/refine";
import { SURFACE_RENDERS_FAILURE } from "@angee/ui";
import { gql } from "@angee/ui/lib/gql";
import { ResourceQuery } from "@angee/metadata";
import {
  GraphQLError, RuntimeFixture, resourceSnapshotOperations, storySchema, testDataResource, type RuntimeFixtureProps,
} from "./runtime-fixtures";

type Client = ReturnType<typeof useQueryClient>;

function ReadClient({ onClient }: { onClient: (client: Client) => void }): null {
  onClient(useQueryClient());
  return null;
}

test("a story's failed read is retried by the app's rule, so a refused one is not retried", () => {
  let client: Client | undefined;
  const schemas = storySchema({}, { schema: "console", resources: [] });
  render(<RuntimeFixture activeSchema="console" schemas={schemas}><ReadClient onClient={(found) => { client = found; }} /></RuntimeFixture>);

  // The app's own function, not a copy of it: the workshop cannot drift from the app.
  expect(client?.getDefaultOptions().queries?.retry).toBe(retryAppQuery);
  const refusal = { response: { status: 200, errors: [{ message: "Not allowed.", extensions: { code: "PERMISSION_DENIED" } }] } };
  expect(retryAppQuery(0, refusal)).toBe(false);
});

const ARCHIVE = gql("mutation ArchiveNote($id: ID!) { archive_note(id: $id) { ok } }") as TypedDocumentNode<{ archive_note: { ok: boolean } }, { id: string }>;
// The server refuses the write; the wire fake answers the refusal as a public GraphQL error.
const refusingSchemas = storySchema({ archive_note: () => {
  throw new GraphQLError("Not allowed.", { extensions: { code: "PERMISSION_DENIED" } });
} });

function Write({ onWrite }: { onWrite: (write: AuthoredMutate<typeof ARCHIVE>) => void }): null {
  onWrite(useAuthoredMutation(ARCHIVE)[0]);
  return null;
}

/** Run one write that the server refuses, through the fixture, and wait for it to settle. */
async function refusedWrite(write: AuthoredMutate<typeof ARCHIVE> | undefined, options?: AuthoredMutateOptions): Promise<void> {
  await act(async () => { await expect(write!({ id: "nte_7" }, options)).rejects.toThrow("Not allowed."); });
}

test("a test's notification provider records the data layer's notices: a write that leaves its notice on, not one passing SURFACE_RENDERS_FAILURE", async () => {
  const opened: { type: string; description?: string }[] = [];
  const recorder = {
    open: ({ type, description }: { type: string; description?: string }) => { opened.push({ type, description }); },
    close: () => undefined,
  } satisfies RuntimeFixtureProps["notificationProvider"];
  let write: AuthoredMutate<typeof ARCHIVE> | undefined;
  render(<RuntimeFixture schemas={refusingSchemas} notificationProvider={recorder}><Write onWrite={(found) => { write = found; }} /></RuntimeFixture>);

  // A surface that renders the failure itself turns the data layer's notice off for its call.
  await refusedWrite(write, SURFACE_RENDERS_FAILURE);
  expect(opened).toEqual([]);
  // One that does not: the data layer tells it, to the recorder.
  await refusedWrite(write);
  expect(opened).toEqual([{ type: "error", description: "Not allowed." }]);
});

test("a resource snapshot lists a groups read's groups in the order the read asks: each key's direction, nulls where it says, a label before its identity", () => {
  const contract = ResourceQuery.forRows({ fields: {
    id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" },
    owner: { kind: "relation", identityPath: "owner.id", labelPath: "owner.name" }, "owner.name": { scalar: "String" },
  } }).contract;
  contract.axes.status!.server = { input: "STATUS", key: "status" };
  contract.axes.owner!.server = { input: "OWNER", key: "owner_id", labelInput: "OWNER__NAME", labelKey: "owner__name" };
  const resource = testDataResource("notes.Thing", { schemaName: "public", query: contract,
    roots: { list: "things", groups: "things_groups", groupsCount: "things_groups_count" } });
  // Two owners share a name, the second created first; two things have no status and no owner.
  const rows = [
    { id: "1", title: "One", status: "open", owner: { id: "o4", name: "Ann" } },
    { id: "2", title: "Two", status: null, owner: null },
    { id: "3", title: "Three", status: "closed", owner: { id: "o1", name: "Zed" } },
    { id: "4", title: "Four", status: "open", owner: { id: "o2", name: "Ann" } },
  ];
  const groups = resourceSnapshotOperations([resource], { "notes.Thing": rows }).things_groups!;
  const request = { fields: ["things_groups"], query: "", variables: {} };
  const keys = (args: Record<string, unknown>, ...names: string[]) =>
    (groups(args, request) as { key: Record<string, unknown> }[]).map(({ key }) => names.map((name) => key[name]).join("/"));
  const byStatus = (direction: string, nulls?: string) =>
    keys({ group_by: [{ field: "STATUS" }], order_by: [{ field: "status", direction, ...(nulls ? { nulls } : {}) }] }, "status");

  expect(byStatus("ASC", "LAST")).toEqual(["closed", "open", ""]);
  expect(byStatus("DESC", "LAST")).toEqual(["open", "closed", ""]);
  expect(byStatus("ASC", "FIRST")).toEqual(["", "closed", "open"]);
  // Without a placement, where SQL puts nulls: last ascending, first descending.
  expect(byStatus("ASC")).toEqual(["closed", "open", ""]);
  expect(byStatus("DESC")).toEqual(["", "open", "closed"]);
  // A relation by its label, then its identity, each in the read's direction.
  const owner = (direction: string) => keys({
    group_by: [{ field: "OWNER" }, { field: "OWNER__NAME" }],
    order_by: [{ field: "owner__name", direction, nulls: "LAST" }, { field: "owner_id", direction, nulls: "LAST" }],
  }, "owner__name", "owner_id");
  expect(owner("ASC")).toEqual(["Ann/o2", "Ann/o4", "Zed/o1", "/"]);
  expect(owner("DESC")).toEqual(["Zed/o1", "Ann/o4", "Ann/o2", "/"]);
  // A read that asks no order lists its groups as they first appear.
  expect(keys({ group_by: [{ field: "STATUS" }] }, "status")).toEqual(["open", "", "closed"]);
});
