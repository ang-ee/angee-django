import { describe, expect, test } from "vitest";

import type { UiTranslate } from "../../i18n";
import { calendarModeOptions, calendarPeriodTitle, shiftCalendarAnchor } from "./calendar-view-controls";

const t = ((key: string) => key) as UiTranslate;

describe("calendar view controls", () => {
  test("the list mode steps a month at a time and is titled by its month", () => {
    expect(shiftCalendarAnchor("list", "2026-01-31", 1)).toBe("2026-02-28");
    expect(shiftCalendarAnchor("list", "2026-03-15", -1)).toBe("2026-02-15");
    expect(calendarPeriodTitle("list", "2026-09-28", t)).toBe(calendarPeriodTitle("month", "2026-09-28", t));
  });

  test("offers every mode the calendar kind owns, list included", () => {
    expect(calendarModeOptions(t).map((option) => [option.value, option.label])).toEqual([
      ["month", "calendar.mode.month"],
      ["week", "calendar.mode.week"],
      ["day", "calendar.mode.day"],
      ["list", "calendar.mode.list"],
    ]);
  });
});
