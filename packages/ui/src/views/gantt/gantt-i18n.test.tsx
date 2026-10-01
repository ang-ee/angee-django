import { TZDate } from "@date-fns/tz";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { setHumanDateLocale } from "../../widgets/date-format";
import { mergeGanttI18n } from "./gantt-i18n";

beforeEach(() => setHumanDateLocale("en"));
afterEach(() => { vi.useRealTimers(); setHumanDateLocale("en"); });

test("a timed event spanning days reads as one date-time range", () => {
  vi.setSystemTime(new Date(2026, 8, 30));
  const start = new TZDate(Date.parse("2026-09-24T09:00:00Z"), "UTC");
  const end = new TZDate(Date.parse("2026-09-26T17:00:00Z"), "UTC");
  expect(mergeGanttI18n(undefined, "UTC").functions.formatEventTime(start, end, false))
    .toBe("Sep 24, 9:00 AM – Sep 26, 5:00 PM");
});

test("week and day ranges use the chart time zone at a year boundary", () => {
  vi.setSystemTime(new Date("2026-09-30T12:00:00Z"));
  const range = {
    start: new Date("2027-01-01T00:30:00Z"),
    end: new Date("2027-01-02T00:30:00Z"),
  };
  const functions = mergeGanttI18n(undefined, "America/Los_Angeles").functions;
  expect(functions.formatDayRange(range)).toBe("Dec 31 – Jan 1, 2027");
  expect(functions.formatTitle("week", { date: range.start, activeRange: range, visibleRange: range }))
    .toBe("Dec 31 – Jan 1, 2027");
  expect(functions.formatTitle("day", { date: new TZDate(range.start.getTime(), "America/Los_Angeles"), activeRange: range, visibleRange: range }))
    .toBe("Thursday, December 31, 2026");
  expect(functions.formatEventTime(range.start, range.start, true)).toBe("Dec 31");
});
