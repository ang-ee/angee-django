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
      { ...field, name: "start", kind: "scalar", scalar: "DateTime", creatable: true, updatable: true },
      { ...field, name: "end", kind: "scalar", scalar: "DateTime", creatable: true, updatable: true },
      { ...field, name: "all_day", kind: "scalar", scalar: "Boolean", creatable: true, updatable: true },
      // The zone whose days an all-day event spans: read, never written.
      { ...field, name: "effective_timezone", kind: "scalar", scalar: "String", creatable: false, updatable: false },
    ],
  }),
  testDataResource(CALENDAR_MODEL, {
    fields: [
      { ...field, name: "name", kind: "scalar", scalar: "String", creatable: true, updatable: true },
      { ...field, name: "color", kind: "scalar", scalar: "String", creatable: true, updatable: true },
    ],
  }),
];

// A timed event, 10:00 to 11:00 on Nov 20 in whatever zone the browser (the runner) is in.
const SAVED = {
  id: "cev_1",
  title: "Design review",
  permissions: ["write", "delete"],
  calendar: { id: "cal_1", name: "Personal", color: "#4f46e5", is_writable: true },
  start: new Date(2026, 10, 20, 10).toISOString(),
  end: new Date(2026, 10, 20, 11).toISOString(),
  all_day: false,
  effective_timezone: "America/Chicago",
};

// An all-day event on Nov 20 on a Chicago calendar: its bounds are Chicago's midnights, which a browser
// west of Chicago (Los Angeles) reads as Nov 19 and one far east (Tokyo) as the afternoon of Nov 20.
const SAVED_ALL_DAY = {
  ...SAVED,
  start: "2026-11-20T06:00:00+00:00",
  end: "2026-11-21T06:00:00+00:00",
  all_day: true,
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

function renderSavedEvent(saved: Record<string, unknown> = SAVED) {
  const getOne = vi.fn(async ({ meta }: { meta?: { fields?: unknown } }) => ({
    data: selected(saved, (meta?.fields ?? ["id"]) as Selection),
  }));
  const update = vi.fn(async ({ variables }: { variables?: Record<string, unknown> }) => ({ data: { ...saved, ...variables } }));
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

  test("turning a timed event all day picks and sends the days it touches, which the event places in its zone", async () => {
    const { update } = renderSavedEvent();
    await waitFor(async () => expect((await calendarControl()).textContent).toContain("Personal"));

    fireEvent.click(screen.getByRole("switch", { name: /all day/i }));
    // The bounds are now picked as days: the date picker shows the day, with no time.
    await waitFor(() => expect(screen.getByRole("button", { name: "Start" }).textContent).not.toMatch(/AM|PM|:/));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const [[saved]] = update.mock.calls as unknown as [[{ variables: Record<string, unknown> }]];
    // Days, never this browser's midnight as an instant: the server places them at midnight in the event's zone.
    expect(saved.variables).toEqual({ all_day: true, start: "2026-11-20", end: "2026-11-21" });
  });

  // The suite runs in the runner's zone and under each zone the shared Vitest defaults pin: an
  // all-day event is dates in its own zone, so every browser shows and edits the same days.
  test("a saved all-day event shows the days it spans in its own zone, whichever zone the browser is in", async () => {
    const { getOne } = renderSavedEvent(SAVED_ALL_DAY);
    await waitFor(async () => expect((await calendarControl()).textContent).toContain("Personal"));

    await waitFor(() => expect(screen.getByRole("button", { name: "Start" }).textContent).toContain("Nov 20, 2026"));
    expect(screen.getByRole("button", { name: "End" }).textContent).toContain("Nov 21, 2026");
    const [[read]] = getOne.mock.calls as unknown as [[{ meta: { fields: unknown[] } }]];
    expect(read.meta.fields).toContain("effective_timezone");
  });

  test("turning a saved all-day event's all day off gives the viewer's own midnights on its days", async () => {
    const { update } = renderSavedEvent(SAVED_ALL_DAY);
    await waitFor(async () => expect((await calendarControl()).textContent).toContain("Personal"));

    fireEvent.click(screen.getByRole("switch", { name: /all day/i }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const [[saved]] = update.mock.calls as unknown as [[{ variables: Record<string, unknown> }]];
    // Nov 20 and Nov 21 as the event keeps them, at midnight on the viewer's clock: never the stored
    // Chicago instants, which are another day's evening in Los Angeles.
    expect(saved.variables).toEqual({
      all_day: false,
      start: new Date(2026, 10, 20).toISOString(),
      end: new Date(2026, 10, 21).toISOString(),
    });
  });
});
