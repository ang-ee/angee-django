// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/storybook/testing";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, describe, expect, test, vi } from "vitest";

import { CALENDAR_MODEL, EVENT_MODEL } from "../resources";
import { calendarEventForm } from "./CalendarEventForm";
import { CALENDAR_PICKER_WIDGET, calendarPickerWidget } from "./CalendarPicker";

// The real form over a native data provider fake: the record read receives the
// form's own selection, so a test sees what the form asks for as well as what it shows.
const field = { readable: true, aggregatable: false, requiredOnCreate: false } as const;
const resources = [
  testDataResource(EVENT_MODEL, {
    modelName: "calendar_event",
    roots: { list: "calendar_events", detail: "calendar_events_by_pk", update: "update_calendar_events_by_pk" },
    fields: [
      { ...field, name: "title", kind: "scalar", scalar: "String", creatable: true, updatable: true },
      // An event never moves to another calendar: the column is set on insert only.
      {
        ...field, name: "calendar", kind: "relation", relationModelLabel: CALENDAR_MODEL, relationObject: true,
        creatable: true, updatable: false, requiredOnCreate: true,
      },
    ],
  }),
  testDataResource(CALENDAR_MODEL, {
    fields: [
      { ...field, name: "name", kind: "scalar", scalar: "String", creatable: true, updatable: true },
      { ...field, name: "color", kind: "scalar", scalar: "String", creatable: true, updatable: true },
    ],
  }),
];

const SAVED = {
  id: "cev_1",
  title: "Design review",
  permissions: ["write", "delete"],
  calendar: { id: "cal_1", name: "Personal", color: "#4f46e5", is_writable: true },
};

const { Provider, clearClients } = createUiTestProviders({ resources, apiUrl: "test://calendar-event-form" });
afterEach(() => { cleanup(); clearClients(); });

type Selection = readonly (string | Record<string, Selection>)[];

/** Answer only what the read selects, as the server does. */
function selected(row: Record<string, unknown>, fields: Selection): Record<string, unknown> {
  return Object.fromEntries(fields.flatMap((entry) => typeof entry === "string"
    ? [[entry, row[entry]]]
    : Object.entries(entry).map(([name, nested]) => [name, selected(row[name] as Record<string, unknown>, nested)])));
}

function renderSavedEvent() {
  const getOne = vi.fn(async ({ meta }: { meta?: { fields?: unknown } }) => ({
    data: selected(SAVED, (meta?.fields ?? ["id"]) as Selection),
  }));
  const update = vi.fn(async ({ variables }: { variables?: Record<string, unknown> }) => ({ data: { ...SAVED, ...variables } }));
  // The picker's own read: the calendars the viewer may add events to.
  const custom = vi.fn(async () => ({ data: { calendars: [{ id: "cal_1" }, { id: "cal_2" }] } }));
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const Form = calendarEventForm.Component;
  render(<Provider dataProvider={{ getOne, update, custom }}>
    <RouterContextProvider router={router}><ModalsHost><ToastProvider>
      <AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, [CALENDAR_PICKER_WIDGET]: calendarPickerWidget } }}>
        <Form resource={EVENT_MODEL} id="cev_1" />
      </AppRuntimeProvider>
    </ToastProvider></ModalsHost></RouterContextProvider>
  </Provider>);
  return { getOne, update };
}

async function calendarControl(): Promise<HTMLElement> {
  return screen.findByRole("combobox", { name: /Calendar/ });
}

describe("CalendarEventForm", () => {
  test("a saved event reads and shows its calendar, beside the writability its form reads", async () => {
    const { getOne } = renderSavedEvent();

    await waitFor(async () => expect((await calendarControl()).textContent).toContain("Personal"));
    const [[read]] = getOne.mock.calls as unknown as [[{ meta: { fields: unknown[] } }]];
    expect(read.meta.fields).toContainEqual({ calendar: ["id", "name", "color", "is_writable"] });
  });

  test("editing a saved event keeps its calendar", async () => {
    const { update } = renderSavedEvent();
    await waitFor(async () => expect((await calendarControl()).textContent).toContain("Personal"));

    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Design review v2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const [[saved]] = update.mock.calls as unknown as [[{ variables: Record<string, unknown> }]];
    expect(saved.variables).toEqual({ title: "Design review v2" });
    await waitFor(async () => expect((await calendarControl()).textContent).toContain("Personal"));
  });
});
