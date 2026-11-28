// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/storybook/testing";
import {
  ActionFormDialog, AppRuntimeProvider, ModalsHost, MutationDialog, ToastProvider, defaultWidgets, invalidFormSubmit,
  type DescriptorField,
} from "@angee/ui";
import { createUiTestProviders, recordFocusCalls } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { CALENDAR_MODEL } from "../resources";
import { CALENDAR_PICKER_WIDGET, calendarPickerWidget } from "./CalendarPicker";

const field = { readable: true, aggregatable: false, requiredOnCreate: false } as const;
const resources = [
  testDataResource(CALENDAR_MODEL, {
    fields: [
      { ...field, name: "name", kind: "scalar", scalar: "String", creatable: true, updatable: true },
      { ...field, name: "color", kind: "scalar", scalar: "String", creatable: true, updatable: true },
    ],
  }),
];
const { Provider, clearClients } = createUiTestProviders({ resources });
let recorder: ReturnType<typeof recordFocusCalls> | undefined;
afterEach(() => { recorder?.restore(); recorder = undefined; cleanup(); clearClients(); });

const PERSONAL = { id: "cal_1", name: "Personal", color: "#4f46e5" };

function Harness({ children }: { children: React.ReactNode }) {
  const [{ dataProvider, router }] = React.useState(() => ({
    dataProvider: {
      getOne: vi.fn(async () => ({ data: PERSONAL })),
      // The picker's own read: the calendars the viewer may add events to.
      custom: vi.fn(async () => ({ data: { calendars: [{ id: "cal_1" }, { id: "cal_2" }] } })),
      getList: vi.fn(async () => ({ data: [PERSONAL], total: 1 })),
    },
    router: createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) }),
  }));
  return <Provider dataProvider={dataProvider}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, [CALENDAR_PICKER_WIDGET]: calendarPickerWidget } }}>
        {children}
      </AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>;
}

test("the calendar picker offers inline edit of the chosen calendar, and the field's `relationEdit: null` declines it", async () => {
  const getOne = vi.fn(async () => ({ data: { id: "cal_1", name: "Personal", color: "#4f46e5" } }));
  // The picker's own read: the calendars the viewer may add events to.
  const custom = vi.fn(async () => ({ data: { calendars: [{ id: "cal_1" }, { id: "cal_2" }] } }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const Widget = calendarPickerWidget.edit;
  const widget = (descriptor: { label: string; relationEdit?: null }) =>
    <Provider dataProvider={{ getOne, custom, getList: vi.fn(async () => ({ data: [], total: 0 })) }}>
      <RouterContextProvider router={router}><ModalsHost><ToastProvider>
        <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <Widget value={{ id: "cal_1", name: "Personal", color: "#4f46e5" }} field={descriptor} onChange={vi.fn()} />
        </AppRuntimeProvider>
      </ToastProvider></ModalsHost></RouterContextProvider>
    </Provider>;

  const view = render(widget({ label: "Calendar" }));
  await screen.findByText("Personal");
  expect(screen.getByRole("button", { name: "Edit record" })).toBeTruthy();

  view.rerender(widget({ label: "Calendar", relationEdit: null }));
  await screen.findByText("Personal");
  expect(screen.queryByRole("button", { name: "Edit record" })).toBeNull();
  // The row labelled itself both times.
  expect(getOne).not.toHaveBeenCalled();
});

const REASON = "This calendar takes no events.";
const refused = async () => invalidFormSubmit({ fieldErrors: { calendar: [REASON] }, formErrors: [] });
const calendarField: DescriptorField = { name: "calendar", label: "Calendar", widget: CALENDAR_PICKER_WIDGET };

// While a dialog submits its fields lock, and this picker gives way to the calendar's read-only
// reference: a refusal's focus asked then would reach a control no longer in the page.
test.each([
  ["a mutation dialog", <MutationDialog key="m" open onOpenChange={() => undefined} title="Add event" fields={[calendarField]}
    initialValues={{ calendar: PERSONAL }} submitLabel="Send" parseValues={(values) => values} onSubmit={refused} />],
  ["an action dialog with typed arguments", <ActionFormDialog key="a" open onOpenChange={() => undefined} context={{ record: null, selectedIds: [] }}
    action={{ id: "add", label: "Send", submit: refused, args: [{ name: "calendar", label: "Calendar", widget: CALENDAR_PICKER_WIDGET, optional: true, defaultValue: PERSONAL }] }} />],
  // A field definition renders as a schema dialog does, in a fieldset disabled while it submits.
  ["an action dialog with a field definition", <ActionFormDialog key="d" open onOpenChange={() => undefined} context={{ record: null, selectedIds: [] }}
    action={{ id: "add", label: "Send", submit: refused, args: { fields: [calendarField], defaultValues: { calendar: PERSONAL } } }} />],
] as const)("a calendar refused in %s brings focus to the picker once it takes focus again, not to the banner", async (_name, ui) => {
  render(<Harness>{ui}</Harness>);
  const dialog = await screen.findByRole("dialog");
  const picker = () => within(dialog).queryByRole("combobox", { name: /^Calendar/ });
  await waitFor(() => expect(picker()).not.toBeNull());
  const send = within(dialog).getByRole("button", { name: "Send" }) as HTMLButtonElement;
  await waitFor(() => expect(send.disabled).toBe(false));
  send.focus();
  recorder = recordFocusCalls();
  fireEvent.click(send);
  await within(dialog).findByText(REASON);
  await waitFor(() => expect(document.activeElement).toBe(picker()));
  expect(document.activeElement?.getAttribute("aria-invalid")).toBe("true");
  const calls = recorder.calls.filter((call) => call.target.getAttribute("role") !== "alert");
  expect(calls.length).toBeGreaterThan(0);
  expect(calls.every((call) => call.takesFocus)).toBe(true);
  expect(recorder.calls.some((call) => call.target.getAttribute("role") === "alert")).toBe(false);
});
