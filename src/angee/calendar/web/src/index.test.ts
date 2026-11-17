import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import calendar, { OCCURRENCE_SOURCES } from "./index";
import { CALENDAR_PICKER_WIDGET, calendarPickerWidget } from "./views/CalendarPicker";

describe("angee.calendar addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(calendar)).not.toThrow();
  });

  test("claims the event and calendar resources on their list routes", () => {
    const resources = (calendar.routes ?? [])
      .map((route) => route.resource)
      .filter((resource): resource is string => Boolean(resource));
    expect(resources).toEqual(["calendar.CalendarEvent", "calendar.Calendar"]);
  });

  test("registers both forms and owns the occurrence-source container", () => {
    expect(Object.keys(calendar.forms ?? {}).sort()).toEqual(["calendar.Calendar", "calendar.CalendarEvent"]);
    expect(calendar.containers?.[OCCURRENCE_SOURCES]).toEqual({});
  });

  test("registers the event form's calendar picker", () => {
    expect(calendar.widgets?.[CALENDAR_PICKER_WIDGET]).toBe(calendarPickerWidget);
  });
});
