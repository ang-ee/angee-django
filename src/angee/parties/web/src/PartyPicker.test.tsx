// @vitest-environment happy-dom
import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import type { DataResourceFieldMetadata, DataResourceMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import {
  ActionFormDialog, AppRuntimeProvider, FormView, MutationDialog, defaultWidgets, invalidFormSubmit, ModalsHost, ToastProvider,
  type DescriptorField,
} from "@angee/ui";
import { createUiTestProviders, recordFocusCalls } from "@angee/ui/testing";
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
  // A record whose counterparty the picker edits, for the record form.
  testDataResource("probe.Deal", { fields: [text("title"), text("counterparty")] }),
];
const { Provider, clearClients } = createUiTestProviders({ resources });
let recorder: ReturnType<typeof recordFocusCalls> | undefined;
afterEach(() => { recorder?.restore(); recorder = undefined; cleanup(); clearClients(); });

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

const ADA = { id: "pty_1", display_name: "Ada Lovelace" };
const REASON = "This party cannot sign.";
const refused = async () => invalidFormSubmit({ fieldErrors: { counterparty: [REASON] }, formErrors: [] });
const counterparty: DescriptorField = { name: "counterparty", label: "Counterparty", widget: "partyPicker" };

function Harness({ children }: { children: React.ReactNode }) {
  const [{ dataProvider, router }] = React.useState(() => ({
    dataProvider: {
      getOne: vi.fn(async () => ({ data: ADA })),
      getList: vi.fn(async () => ({ data: [ADA], total: 1 })),
    },
    router: createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) }),
  }));
  return <Provider dataProvider={dataProvider}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, partyPicker: partyPickerWidget } }}>{children}</AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>;
}

const surfaces = [
  ["the record form", <FormView key="r" resource="probe.Deal" id="deal-1"
    fields={[{ name: "title", label: "Title", widget: "text" }, counterparty]}
    acknowledgedSource={{ record: { id: "deal-1", title: "Deal", counterparty: ADA }, values: { title: "Deal", counterparty: ADA } } as never}
    submit={refused as never} />, "Save"],
  ["a mutation dialog", <MutationDialog key="m" open onOpenChange={() => undefined} title="Sign" fields={[counterparty]}
    initialValues={{ counterparty: ADA }} submitLabel="Send" parseValues={(values) => values} onSubmit={refused} />, "Send"],
  ["an action dialog with typed arguments", <ActionFormDialog key="a" open onOpenChange={() => undefined} context={{ record: null, selectedIds: [] }}
    action={{ id: "sign", label: "Send", submit: refused, args: [{ name: "counterparty", label: "Counterparty", widget: "partyPicker", optional: true, defaultValue: ADA }] }} />, "Send"],
  // A field definition renders as a schema dialog does, in a fieldset disabled while it submits.
  ["an action dialog with a field definition", <ActionFormDialog key="d" open onOpenChange={() => undefined} context={{ record: null, selectedIds: [] }}
    action={{ id: "sign", label: "Send", submit: refused, args: { fields: [counterparty], defaultValues: { counterparty: ADA } } }} />, "Send"],
] as const;

test.each(surfaces)("a counterparty refused on %s brings focus to the picker, which the field marks invalid", async (_name, ui, submit) => {
  render(<Harness>{ui}</Harness>);
  if (submit === "Save") fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Changed" } });
  const picker = () => screen.queryByRole("combobox", { name: /^Counterparty/ });
  await waitFor(() => expect(picker()).not.toBeNull());
  const button = await screen.findByRole("button", { name: submit }) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  button.focus();
  recorder = recordFocusCalls();
  fireEvent.click(button);
  await screen.findByText(REASON);
  await waitFor(() => expect(document.activeElement).toBe(picker()));
  expect(document.activeElement?.getAttribute("aria-invalid")).toBe("true");
  const fieldCalls = recorder.calls.filter((call) => call.target.getAttribute("role") !== "alert");
  expect(fieldCalls.length).toBeGreaterThan(0);
  // Asked while a dialog's fields are locked, focus is lost to a browser: every call came once the picker took focus.
  expect(fieldCalls.filter((call) => !call.takesFocus).map((call) => call.target.outerHTML.slice(0, 80))).toEqual([]);
  expect(recorder.calls.length).toBe(fieldCalls.length);
});

test.each([
  ["no label", { name: "counterparty" }, "Party"],
  ["a label that is not text", { name: "counterparty", label: <b>Counterparty</b> }, "Party"],
  ["a text label", { name: "counterparty", label: "Counterparty" }, "Counterparty"],
] as const)("the registered widget given %s is named, with no form field around it", async (_name, field, name) => {
  render(<Harness><partyPickerWidget.edit value={null} field={field} onChange={vi.fn()} /></Harness>);
  expect(await screen.findByRole("combobox", { name })).toBeTruthy();
});

/** The widget as a real dialog field's control, and the name its trigger then has. */
async function expectNameInDialogField(label: Partial<DescriptorField>, name: string) {
  const field: DescriptorField = { name: "counterparty", widget: "partyPicker", ...label };
  render(<Harness><MutationDialog open onOpenChange={() => undefined} title="Sign" fields={[field]}
    submitLabel="Send" parseValues={(values) => values} onSubmit={refused} /></Harness>);
  const picker = await screen.findByRole("combobox");
  expect(screen.queryByRole("combobox", { name })).toBe(picker);
}

// A group's label names the widget through the control props' `aria-labelledby`, which the widget
// passes on itself: the relation widget would otherwise overwrite it.
test.each([
  ["a text label", { label: "Counterparty" }, "Counterparty"],
  ["a text label that labels a group", { label: "Counterparty", controlLabelMode: "group" }, "Counterparty"],
  ["a label that is not text and labels a group", { label: <b>Signing party</b>, controlLabelMode: "group" }, "Signing party"],
] as const)("in a dialog field with %s, the registered widget is named by the label the person sees", (_name, label, name) =>
  expectNameInDialogField(label, name));

test.fails("defect: in a dialog field whose label is not text and labels no group, the widget's own \"Party\" overrides the label the person sees, as it does for every widget named through `widgetLabel`; it should be named by that label", () =>
  expectNameInDialogField({ label: <b>Counterparty</b> }, "Counterparty"));
