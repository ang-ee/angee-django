// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import type { Row } from "@angee/metadata";
import { AppRuntimeProvider, RecordActionBar, ToastProvider, createRouteHref } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, describe, expect, test } from "vitest";

import { enPartiesMessages } from "./i18n";
import parties from "./index";
import { useMergeIntoAction } from "./merge-into";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://parties-merge-into" });
afterEach(() => {
  cleanup();
  clearClients();
});

const MERGE_INTO = enPartiesMessages["person.action.merge"]!;

/** The person form's record bar for `record`, carrying the form's own "Merge into…" verb. */
function PersonVerbs({ record }: { record: Row | null }) {
  return <>
    <span>Person verbs</span>
    <RecordActionBar record={record} actions={[useMergeIntoAction()]} />
  </>;
}

async function renderVerbs(record: Row | null): Promise<void> {
  const root = createRootRoute({ component: () => <PersonVerbs record={record} /> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Provider>
      <AppRuntimeProvider runtime={{ routeHref: createRouteHref(parties.routes ?? []) }}>
        <ToastProvider><RouterProvider router={router} /></ToastProvider>
      </AppRuntimeProvider>
    </Provider>,
  );
  await screen.findByText("Person verbs");
}

describe("the person form's Merge into…", () => {
  test("is offered on a record whose projected permissions hold merge_away", async () => {
    await renderVerbs({ id: "pty_ada", permissions: ["merge_away", "write"] });

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));

    expect(await screen.findByRole("menuitem", { name: MERGE_INTO })).toBeTruthy();
  });

  test.each([
    // A contact manager writes a person but merges none away: the server would refuse the merge.
    ["a record whose projected permissions lack merge_away", { id: "pty_ada", permissions: ["write"] }],
    ["a create form, which has no record yet", null],
  ])("is absent from %s", async (_case, record) => {
    await renderVerbs(record);

    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: MERGE_INTO })).toBeNull();
  });
});
