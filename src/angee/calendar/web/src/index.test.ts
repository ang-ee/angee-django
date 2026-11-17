import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import calendar, { OCCURRENCE_SOURCES, SOURCES_TOOLBAR } from "./index";

describe("angee.calendar addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(calendar)).not.toThrow();
  });

  test("claims the event, calendar and source resources on their list routes", () => {
    const resources = (calendar.routes ?? [])
      .map((route) => route.resource)
      .filter((resource): resource is string => Boolean(resource));
    expect(resources).toEqual(["calendar.CalendarEvent", "calendar.Calendar", "calendar.CalendarSource"]);
  });

  test("registers its forms and owns the occurrence-source and source toolbar containers", () => {
    expect(Object.keys(calendar.forms ?? {}).sort()).toEqual([
      "calendar.Calendar",
      "calendar.CalendarEvent",
      "calendar.CalendarSource",
    ]);
    expect(calendar.containers?.[OCCURRENCE_SOURCES]).toEqual({});
    expect(calendar.containers?.[SOURCES_TOOLBAR]).toEqual({});
  });
});
