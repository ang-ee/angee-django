// @vitest-environment happy-dom

import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, ToastProvider, createAngeeI18nInstance, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { useList } from "@refinedev/core";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  // A transport fixture only: the real dialog, mutation and list owners run below even
  // when the host's generated documents lag this checkout.
  document: { kind: "Document", definitions: [] },
  custom: vi.fn(),
  getList: vi.fn(),
}));

vi.mock("./documents", () => ({ ConnectHubSpotDirectory: mocks.document }));

import { ConnectHubSpotAction } from "./ConnectHubSpotAction";
import { enHubSpotMessages } from "./i18n";

const SERVICE_KEY = "invented-service-key";
const REFUSAL = "HubSpot rejected this service key: enter a valid one.";
const directory = testDataResource("parties.Directory");
const { Provider, clearClients, clients } = createUiTestProviders({
  resources: [directory],
  apiUrl: "test://hubspot-connect",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } },
});
// The vendor's words join the parties namespace; the shared ones come from the parties bundle.
const i18n = createAngeeI18nInstance({ parties: enHubSpotMessages });
let connected: { id: string; display_name: string }[] = [];

/** The directories list a connect refreshes: a resource read of `parties.Directory`. */
function Directories(): React.ReactElement {
  const list = useList<{ id: string; display_name: string }>({
    resource: directory.roots.list!,
    dataProviderName: "console",
  });
  return (
    <ul aria-label="Directories">
      {(list.result.data ?? []).map((row) => <li key={row.id}>{row.display_name}</li>)}
    </ul>
  );
}

function renderAction(): void {
  // Under a router, as an app page is: the dialog guards route moves while it holds work.
  render(
    <Provider router dataProvider={{ custom: mocks.custom, getList: mocks.getList }}>
      <AppRuntimeProvider runtime={{ i18n, widgets: defaultWidgets }}>
        <ToastProvider>
          <Directories />
          <ConnectHubSpotAction />
        </ToastProvider>
      </AppRuntimeProvider>
    </Provider>,
  );
}

async function openAndFill(): Promise<HTMLElement> {
  fireEvent.click(screen.getByRole("button", { name: "HubSpot account" }));
  const dialog = await screen.findByRole("dialog", { name: "Connect a HubSpot account" });
  fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "  Sales contacts " } });
  fireEvent.change(within(dialog).getByLabelText("Service key"), { target: { value: ` ${SERVICE_KEY}\n` } });
  const submit = within(dialog).getByRole("button", { name: "Connect" }) as HTMLButtonElement;
  await waitFor(() => expect(submit.disabled).toBe(false));
  fireEvent.click(submit);
  return dialog;
}

/** The connect requests that reached the data provider: the mutation's variables as sent. */
function sentVariables(): unknown[] {
  return mocks.custom.mock.calls.map(([request]) => request.meta.gqlVariables);
}

/** Mutations the query client still holds that carry the key. */
function retainedKeys(): unknown[] {
  return clients.flatMap((client) => client.getMutationCache().getAll())
    .filter((mutation) => JSON.stringify(mutation.state.variables ?? null).includes(SERVICE_KEY));
}

describe("ConnectHubSpotAction", () => {
  afterEach(() => {
    cleanup();
    clearClients();
  });

  beforeEach(() => {
    connected = [];
    mocks.getList.mockImplementation(async () => ({ data: connected, total: connected.length }));
    mocks.custom.mockImplementation(async () => {
      connected = [{ id: "directory-1", display_name: "Sales contacts" }];
      return { data: { connect_hubspot_directory: { id: "directory-1", lifecycle: "CONNECTED", runtime_status: "OK" } } };
    });
  });

  test("asks for a name and a masked service key, sends both trimmed, and refreshes the directories", async () => {
    renderAction();
    await waitFor(() => expect(mocks.getList).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "HubSpot account" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect a HubSpot account" });
    expect((within(dialog).getByLabelText("Service key") as HTMLInputElement).type).toBe("password");
    expect((within(dialog).getByLabelText("Name") as HTMLInputElement).placeholder).toBe("Sales contacts");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await openAndFill();

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sentVariables()).toEqual([{ name: "Sales contacts", serviceKey: SERVICE_KEY }]);
    // The directories page is a resource list: the connect refreshes it without a live event.
    await waitFor(() => expect(mocks.getList).toHaveBeenCalledTimes(2));
    expect(within(screen.getByRole("list", { name: "Directories" })).getByText("Sales contacts")).toBeTruthy();
    // The key leaves the mutation cache once the connect settles.
    await waitFor(() => expect(retainedKeys()).toEqual([]));
  });

  test("shows HubSpot's refusal in the dialog and refreshes nothing", async () => {
    mocks.custom.mockRejectedValueOnce(Object.assign(new Error(REFUSAL), {
      graphQLErrors: [{ message: REFUSAL, extensions: { code: "VALIDATION", formErrors: [REFUSAL] } }],
    }));
    renderAction();
    await waitFor(() => expect(mocks.getList).toHaveBeenCalledOnce());

    const dialog = await openAndFill();

    expect(await within(dialog).findByText(REFUSAL)).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Connect a HubSpot account" })).toBe(dialog);
    expect(sentVariables()).toEqual([{ name: "Sales contacts", serviceKey: SERVICE_KEY }]);
    expect(mocks.getList).toHaveBeenCalledOnce();
    await waitFor(() => expect(retainedKeys()).toEqual([]));
  });
});
