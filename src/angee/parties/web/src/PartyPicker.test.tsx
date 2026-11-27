// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import type { DataResourceFieldMetadata, DataResourceMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, defaultWidgets, ModalsHost, ToastProvider } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { PartyPicker, partyPickerWidget } from "./PartyPicker";

const text = (name: string): DataResourceFieldMetadata => ({
  name, kind: "scalar", scalar: "String", readable: true,
  aggregatable: false, creatable: true, updatable: true, requiredOnCreate: false,
});
const kind = (label: string, list: string): DataResourceMetadata => testDataResource(label, {
  roots: { list }, canonicalLabel: "parties.Party", recordRepresentation: "display_name", fields: [text("display_name")],
});
const resources = [
  testDataResource("parties.Party", {
    roots: { list: "parties", create: null }, recordRepresentation: "display_name",
    concreteKinds: ["parties.Organization", "parties.Person"], fields: [text("display_name")],
  }),
  kind("parties.Organization", "organizations"),
  kind("parties.Person", "people"),
];
const { Provider, clearClients } = createUiTestProviders({ resources });
afterEach(() => { cleanup(); clearClients(); });

test("creates a party as one of its kinds through the shared kind switcher", async () => {
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<Provider dataProvider={{ getList: vi.fn(async () => ({ data: [], total: 0 })) }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <PartyPicker label="Counterparty" value={null} onChange={vi.fn()} />
      </AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);

  fireEvent.click(screen.getByRole("combobox", { name: "Counterparty" }));
  fireEvent.change(await screen.findByPlaceholderText("Search…"), { target: { value: "Ada" } });
  fireEvent.click(await screen.findByText("Create “Ada”"));

  const dialog = await screen.findByRole("dialog", { name: "New party" });
  const kinds = within(dialog).getByRole("group", { name: "Kind" });
  expect(within(kinds).getAllByRole("button").map((button) => button.textContent)).toEqual(["Organization", "Person"]);
});

test("the registered widget shows the label of a row it is given without reading it, and declines inline edit with the field's `relationEdit: null`", async () => {
  const getOne = vi.fn(async () => ({ data: { id: "pty_1", display_name: "Ada Lovelace" } }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const Widget = partyPickerWidget.edit;
  const widget = (field: { label: string; relationEdit?: null }) =>
    <Provider dataProvider={{ getOne, getList: vi.fn(async () => ({ data: [], total: 0 })) }}>
      <RouterContextProvider router={router}><ModalsHost><ToastProvider>
        <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <Widget value={{ id: "pty_1", display_name: "Ada Lovelace" }} field={field} onChange={vi.fn()} />
        </AppRuntimeProvider>
      </ToastProvider></ModalsHost></RouterContextProvider>
    </Provider>;

  const view = render(widget({ label: "Counterparty" }));
  await screen.findByText("Ada Lovelace");
  expect(screen.getByRole("button", { name: "Edit record" })).toBeTruthy();
  expect(getOne).not.toHaveBeenCalled();

  view.rerender(widget({ label: "Counterparty", relationEdit: null }));
  await screen.findByText("Ada Lovelace");
  expect(screen.queryByRole("button", { name: "Edit record" })).toBeNull();
  expect(getOne).not.toHaveBeenCalled();
});
