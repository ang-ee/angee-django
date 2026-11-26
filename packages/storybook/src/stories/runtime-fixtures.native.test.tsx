// @vitest-environment happy-dom
// The workshop's shared runtime fixture, as the stories and the fragments' tests rely on it.

import { act, render } from "@testing-library/react";
import { expect, test } from "vitest";
import { retryAppQuery } from "@angee/app/create-app";
import { useAuthoredMutation, useQueryClient, type AuthoredMutate, type AuthoredMutateOptions, type TypedDocumentNode } from "@angee/refine";
import { SURFACE_RENDERS_FAILURE } from "@angee/ui";
import { gql } from "@angee/ui/lib/gql";
import { GraphQLError, RuntimeFixture, storySchema, type RuntimeFixtureProps } from "./runtime-fixtures";

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
