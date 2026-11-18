import { afterEach, describe, expect, test, vi } from "vitest";

import { calendarDay, formatDate, formatDateTime, formatDayLabel, formatDateTimeStorage } from "./date-format";

// A zone suite: it runs in the runner's zone and again under each zone the shared Vitest defaults pin
// (Asia/Tokyo, America/Los_Angeles), and every assertion holds in each.
describe("date storage across browser zones", () => {
  // The naive `yyyy-MM-ddTHH:mm` this replaces fails wherever the server's zone is not the browser's.
  test("a picked date-time is stored as the instant meant: its local wall time with the browser's offset", () => {
    const date = new Date(2026, 10, 20, 10, 30);
    const stored = formatDateTimeStorage(date);

    expect(stored).toMatch(/^2026-11-20T10:30:00[+-]\d{2}:\d{2}$/);
    // A server in another zone reads the same instant, never the wall time in its own zone.
    expect(Date.parse(stored)).toBe(date.getTime());
  });

  test("a stored instant reads as its day in the zone that owns its dates, whichever zone the browser is in", () => {
    // Chicago's midnight on Nov 20: still Nov 19 in Los Angeles, and Nov 20 in Tokyo.
    const midnight = new Date("2026-11-20T06:00:00Z");

    expect(calendarDay(midnight, "America/Chicago")).toBe("2026-11-20");
    expect(calendarDay(midnight, "America/Los_Angeles")).toBe("2026-11-19");
    expect(calendarDay(midnight, "Asia/Tokyo")).toBe("2026-11-20");
  });
});

describe("a zone this browser does not know", () => {
  afterEach(() => vi.restoreAllMocks());

  // An IANA name newer than the browser's zone data would throw a RangeError from Intl, failing the
  // form or the occurrence query that formats with it.
  test("reads in the viewer's zone, is reported once, and never throws", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const noon = new Date(2026, 10, 20, 12);

    expect(calendarDay(noon, "Mars/Olympus_Mons")).toBe("2026-11-20");
    expect(formatDate(noon, { timeZone: "Mars/Olympus_Mons" })).toBe(formatDate(noon));
    expect(formatDateTime(noon, { timeZone: "Mars/Olympus_Mons" })).toBe(formatDateTime(noon));
    expect(formatDayLabel(noon, { timeZone: "Mars/Olympus_Mons", now: noon })).toBe("Today");

    expect(warn).toHaveBeenCalledOnce();
    expect(warn.mock.calls[0]?.[0]).toContain("Mars/Olympus_Mons");
  });
});
