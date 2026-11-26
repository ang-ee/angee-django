// @vitest-environment happy-dom
// The workshop's shared runtime fixture, as this fragment's tests and stories rely on it. The workshop
// package runs no rendered tests of its own, so a fragment that tests through the fixture pins the contract.

import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { retryAppQuery } from "@angee/app/create-app";
import { useQueryClient } from "@angee/refine";
import { RuntimeFixture, storySchema } from "@angee/storybook/testing";

type Client = ReturnType<typeof useQueryClient>;

function ReadClient({ onClient }: { onClient: (client: Client) => void }): null {
  onClient(useQueryClient());
  return null;
}

test("a story's failed read is retried by the app's rule, so a refused one is not retried", () => {
  let client: Client | undefined;
  const schemas = { console: { ...storySchema({}).public!, metadata: { angee: { resources: [] } } } };
  render(<RuntimeFixture activeSchema="console" schemas={schemas}><ReadClient onClient={(found) => { client = found; }} /></RuntimeFixture>);

  // The app's own function, not a copy of it: the workshop cannot drift from the app.
  expect(client?.getDefaultOptions().queries?.retry).toBe(retryAppQuery);
  const refusal = { response: { status: 200, errors: [{ message: "Not allowed.", extensions: { code: "PERMISSION_DENIED" } }] } };
  expect(retryAppQuery(0, refusal)).toBe(false);
});
