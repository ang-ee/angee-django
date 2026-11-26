// @vitest-environment happy-dom
// A record verb whose request fails, pressed in its action bar with the real data hook: the bar, the
// record-action hooks, `useActionMutation`, refine's mutation and a real Angee data provider run as a
// record form runs them. Only the request's `fetch`, the toast sink and refine's notification provider
// are faked; both tellers write to one list, so a test reads what the person is told, in order. (The
// bound-verb bar test fakes the data hook itself, so the data layer's own notice is outside it.)

import { testDataResource } from "@angee/metadata/testing";
import { ActiveDataProviderNameProvider, createAngeeHasuraDataProvider, OperationDocumentsProvider } from "@angee/refine";
import { GraphQLError, graphqlWireFetch } from "@angee/refine/testing/wire";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { gql } from "graphql-tag";
import * as React from "react";
import { afterEach, describe, expect, test, vi, type Mock } from "vitest";

import { ModalsHost } from "../../feedback";
import { createUiTestProviders } from "../../testing";
import { RecordActionBar } from "../form/RecordActionBar";
import { useActionResultRun } from "./action-result-run";
import {
  useActionOutcomeMutation,
  useActionResultMutation,
  useRecordAction,
  useRecordActionMutation,
  type RecordAction,
} from "./record-action";

const fake = vi.hoisted(() => {
  const told: { teller: "toast" | "notice"; tone: string; title: string; description?: string }[] = [];
  const tone = (name: string) => (options: { title: unknown; description?: unknown }) => {
    told.push({ teller: "toast", tone: name, title: String(options.title),
      ...(options.description !== undefined ? { description: String(options.description) } : {}) });
    return "toast";
  };
  const toast = Object.assign(tone("info"), {
    info: tone("info"), success: tone("success"), warning: tone("warning"), danger: tone("danger"), close: () => undefined,
  });
  return { told, toast };
});
vi.mock("../../feedback", async (importOriginal) => ({
  ...(await importOriginal<object>()), useToast: () => fake.toast,
}));

const { Provider, clearClients } = createUiTestProviders({
  resources: [testDataResource("example.Project")],
  // The data layer's own notice: refine raises it here for a failed mutation unless the call turns it off.
  notificationProvider: {
    open: ({ type, message, description }) => {
      fake.told.push({ teller: "notice", tone: type, title: message, ...(description !== undefined ? { description } : {}) });
    },
    close: () => undefined,
  },
});
afterEach(() => { cleanup(); clearClients(); fake.told.length = 0; });

const VERB = "create_proposal_track";
const DOCUMENTS = { console: { actions: {
  [VERB]: gql`mutation CreateProposalTrack($id: ID!) { create_proposal_track(id: $id) { ok message id validation_errors } }`,
} } };

/** A transport that answers the verb's request with this GraphQL error. */
function answeredWith(error: GraphQLError): typeof globalThis.fetch {
  return graphqlWireFetch({ [VERB]: () => { throw error; } });
}

/** Each kind of failed request, and the message the real data provider bounds it to. */
const FAILURES: readonly { kind: string; shown: string; fetch: typeof globalThis.fetch }[] = [
  { kind: "a network error", shown: "Request failed.", fetch: async () => { throw new TypeError("Failed to fetch"); } },
  { kind: "a GraphQL error", shown: "Request failed.", fetch: answeredWith(new GraphQLError("Track storage is unavailable.")) },
  { kind: "an authorization error", shown: "You may not change this proposal.",
    fetch: answeredWith(new GraphQLError("You may not change this proposal.", { extensions: { code: "PERMISSION_DENIED" } })) },
  { kind: "a sign-in error", shown: "Sign in to continue.",
    fetch: answeredWith(new GraphQLError("Sign in to continue.", { extensions: { code: "UNAUTHENTICATED" } })) },
];

/** A form's declared verb bound with `useRecordActionMutation`: its bar toasts the failure. */
function useBoundVerb(): RecordAction {
  return useRecordActionMutation(VERB)[0];
}

/** A verb settled through `useActionResultMutation` and run from a form's `<Action run>`, as its callers run it. */
function useSettledVerb(): RecordAction {
  const [settle] = useActionResultMutation(VERB);
  return useRecordAction((id) => settle(id));
}

/** The control: a caller of `useActionOutcomeMutation`, with no option, that settles the outcome itself. */
function useOutcomeVerb(): RecordAction {
  const [mutate] = useActionOutcomeMutation(VERB);
  const settle = useActionResultRun();
  return useRecordAction(async (id) => { await settle(() => mutate(id)); });
}

/** Press the verb over a failing transport; resolve once a toast has told the failure and the button has settled. */
async function press(useVerb: () => RecordAction, fetch: typeof globalThis.fetch): Promise<void> {
  const dataProvider = createAngeeHasuraDataProvider({ url: "https://fixture.invalid/graphql", fetch, auth: (nativeFetch) => nativeFetch });
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  function Form(): React.ReactElement | null {
    const run = useVerb();
    return <RecordActionBar record={{ id: "prp_1" }} actions={[{ id: "create-track", label: "Create track", placement: "toolbar", run }]} />;
  }
  render(<Provider dataProvider={dataProvider}><RouterContextProvider router={router}><ModalsHost>
    <ActiveDataProviderNameProvider name="console"><OperationDocumentsProvider documents={DOCUMENTS}>
      <Form />
    </OperationDocumentsProvider></ActiveDataProviderNameProvider>
  </ModalsHost></RouterContextProvider></Provider>);
  const button = screen.getByRole("button", { name: "Create track" });
  fireEvent.click(button);
  await waitFor(() => expect(told("toast").length).toBeGreaterThan(0));
  await waitFor(() => expect(button.getAttribute("aria-busy")).toBeNull());
}

function told(teller: "toast" | "notice") {
  return fake.told.filter((line) => line.teller === teller);
}

/** The requests the transport received: the verb's own, for the pressed record. */
function sent(fetch: Mock<typeof globalThis.fetch>) {
  return fetch.mock.calls.map(([, init]) => {
    const { operationName, variables } = JSON.parse(String(init?.body)) as { operationName?: string; variables?: unknown };
    return { operationName, variables };
  });
}

describe.each(FAILURES)("$kind", (failure) => {
  test("a verb bound with useRecordActionMutation is told once: its bar's toast under the verb's label, no data-layer notice", async () => {
    const fetch = vi.fn(failure.fetch);
    await press(useBoundVerb, fetch);
    expect(sent(fetch)).toEqual([{ operationName: "CreateProposalTrack", variables: { id: "prp_1" } }]);
    expect(told("toast")).toEqual([{ teller: "toast", tone: "danger", title: "Create track", description: failure.shown }]);
    expect(told("notice")).toEqual([]);
  });

  test("a verb settled through useActionResultMutation is told once: the settle owner's toast, no data-layer notice", async () => {
    const fetch = vi.fn(failure.fetch);
    await press(useSettledVerb, fetch);
    expect(sent(fetch)).toEqual([{ operationName: "CreateProposalTrack", variables: { id: "prp_1" } }]);
    expect(told("toast")).toEqual([{ teller: "toast", tone: "danger", title: failure.shown }]);
    expect(told("notice")).toEqual([]);
  });

  test("control: a caller of useActionOutcomeMutation that settles the failure itself hears the data layer's notice once, then its own toast", async () => {
    const fetch = vi.fn(failure.fetch);
    await press(useOutcomeVerb, fetch);
    expect(sent(fetch)).toEqual([{ operationName: "CreateProposalTrack", variables: { id: "prp_1" } }]);
    expect(fake.told).toEqual([
      { teller: "notice", tone: "error", title: expect.any(String), description: failure.shown },
      { teller: "toast", tone: "danger", title: failure.shown },
    ]);
  });
});
