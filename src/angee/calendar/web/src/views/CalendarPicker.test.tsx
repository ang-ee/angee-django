// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { testDataResource } from "@angee/storybook/testing";
import { AppRuntimeProvider, ModalsHost, ToastProvider, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, expect, test, vi } from "vitest";

import { CALENDAR_MODEL } from "../resources";
import { calendarPickerWidget } from "./CalendarPicker";

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
afterEach(() => { cleanup(); clearClients(); });

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
