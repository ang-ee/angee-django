// @vitest-environment happy-dom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { RecordChromeProvider } from "@angee/ui";
import { RuntimeFixture, storySchema, testDataResource, type GraphQLWireRequest } from "@angee/storybook/testing";

import { ProjectRoundRecord } from "./round-record";

const round = (overrides: Record<string, unknown> = {}) => ({
  id: "round-1", name: "Review", status: "COLLECTING", opening_policy: "ANSWERS",
  permissions: ["write"], can_open: true, can_admit: true, revision: 4, roster: [], ...overrides,
});

const resources = ["projects.Project", "proposals.Round", "proposals.Proposal"].map((label) => testDataResource(label));

/** A project record whose server projection carries its active round. */
function renderProject(activeRound: Record<string, unknown>): GraphQLWireRequest[] {
  const requests: GraphQLWireRequest[] = [];
  const schemas = { console: { ...storySchema({
    open_proposal_round: (args) => ({ ok: true, message: "Round opened.", id: args.round, code: null, validation_errors: null }),
  }, (request) => requests.push(request)).public!, metadata: { angee: { resources } } } };
  render(<RuntimeFixture activeSchema="console" schemas={schemas}>
    <RecordChromeProvider value={{ resource: "projects.Project", canonicalResource: "projects.Project", dataProviderName: "console",
      recordId: "project-1", record: { id: "project-1", active_proposal_round: activeRound }, formReadOnly: false }}>
      <ProjectRoundRecord surface="primary" />
    </RecordChromeProvider>
  </RuntimeFixture>);
  return requests;
}

test("the project's Open round verb opens the returned round at its revision", async () => {
  const requests = renderProject(round());
  fireEvent.click(await screen.findByRole("button", { name: "Open round" }));
  const confirmation = await screen.findByRole("alertdialog");
  fireEvent.click(within(confirmation).getByRole("button", { name: "Open round" }));
  await waitFor(() => expect(requests.find(({ fields }) => fields.includes("open_proposal_round"))?.variables)
    .toEqual({ round: "round-1", revision: 4 }));
  expect(await screen.findByText("Round opened.")).toBeTruthy();
});

test("Open round stays hidden when the server withholds it", () => {
  renderProject(round({ can_open: false }));
  expect(screen.queryByRole("button", { name: "Open round" })).toBeNull();
});
