import { afterEach, describe, expect, test } from "vitest";
import { enUS, fr } from "date-fns/locale";

import {
  dateFromUnknown,
  dateFromValue,
  formatDate,
  formatDateRange,
  formatDateStorage,
  formatDateTime,
  formatDateTimeStorage,
  formatDayLabel,
  formatMonth,
  formatRelativeTime,
  formatTime,
  formatTimeInput,
  setHumanDateLocale,
  valueLabel,
} from "./date-format";

describe("date formatting", () => {
  afterEach(() => setHumanDateLocale("en"));
  test("formats compact dates in the requested locale across year boundaries", () => {
    const now = new Date(2026, 8, 30);
    expect(formatDate("2026-09-29", { now, locale: enUS })).toBe("Sep 29");
    expect(formatDate("2025-12-31", { now, locale: enUS })).toBe("Dec 31, 2025");
    expect(formatDate("2027-01-01", { now, locale: enUS })).toBe("Jan 1, 2027");
    expect(formatDate("2026-09-29", { now, locale: fr })).toBe("29 sept.");
    expect(formatDate("2025-12-31", { now, locale: fr })).toBe("31 déc. 2025");
    expect(formatDateRange("2026-09-29", "2027-01-01", { now, locale: enUS })).toBe("Sep 29 – Jan 1, 2027");
    expect(formatDateRange("2026-09-29", "2026-10-24", { now, locale: fr })).toBe("29 sept. – 24 oct.");
  });

  test("formats elapsed time through its locale owners", () => {
    const now = new Date(2026, 8, 30, 12);
    const earlier = new Date(2026, 8, 30, 10);
    expect(formatRelativeTime(earlier, { now, locale: enUS })).toBe("about 2 hours ago");
    expect(formatRelativeTime(earlier, { now, locale: fr })).toContain("2 heures");
  });

  test("formats clock times and month buckets", () => {
    expect(formatTime("2026-06-18T13:45:00", { locale: enUS })).toBe("1:45 PM");
    expect(formatTime(new Date("2026-06-18T13:45:12Z"), { locale: "fr-FR", timeZone: "UTC" })).toBe("13:45");
    expect(formatMonth("2026-09-01", { locale: enUS })).toBe("September 2026");
    expect(formatMonth("2026-09-01", { locale: "fr-FR" })).toBe("septembre 2026");
    expect(formatTime(null)).toBe("");
    expect(formatMonth("")).toBe("");
  });

  test("reads a GraphQL time of day as written, in any locale and zone", () => {
    expect(formatTime("13:45", { locale: enUS })).toBe("1:45 PM");
    expect(formatTime("13:45:30.123456", { locale: "fr-FR" })).toBe("13:45");
    expect(formatTime("00:05:00", { locale: enUS, timeZone: "America/Los_Angeles" })).toBe("12:05 AM");
    for (const value of ["24:00", "9:05", "12:60", "12:34:56Z", "12:34:56+02:00", "noon"]) {
      expect(formatTime(value)).toBe("");
    }
  });

  test("labels day buckets as today, yesterday, a weekday, then the date", () => {
    const now = new Date(2026, 8, 30, 12);
    expect(formatDayLabel(new Date(2026, 8, 30, 8), { now, locale: enUS })).toBe("Today");
    expect(formatDayLabel(new Date(2026, 8, 29, 23), { now, locale: enUS })).toBe("Yesterday");
    expect(formatDayLabel(new Date(2026, 8, 28, 9), { now, locale: enUS })).toBe("Monday");
    expect(formatDayLabel(new Date(2026, 8, 23, 9), { now, locale: enUS })).toBe("Sep 23");
    expect(formatDayLabel(new Date(2025, 8, 23, 9), { now, locale: enUS })).toBe("Sep 23, 2025");
    expect(formatDayLabel(new Date(2026, 8, 29, 23), { now, locale: "fr-FR" })).toBe("Hier");
    expect(formatDayLabel(new Date(2026, 9, 1, 9), { now, locale: enUS })).toBe("Oct 1");
    expect(formatDayLabel("bad", { now })).toBe("");
  });

  test("day buckets follow the requested time zone", () => {
    const now = new Date("2026-09-30T12:00:00Z");
    const instant = new Date("2026-09-30T02:00:00Z");
    expect(formatDayLabel(instant, { now, locale: enUS, timeZone: "UTC" })).toBe("Today");
    expect(formatDayLabel(instant, { now, locale: enUS, timeZone: "America/Los_Angeles" })).toBe("Yesterday");
  });

  test("accepts a fixed app language tag without consulting the browser locale", () => {
    const now = new Date(2026, 8, 30, 12);
    expect(formatDate("2026-09-29", { now, locale: "fr-FR" })).toBe("29 sept.");
    expect(formatDateRange("2026-09-29", "2026-10-24", { now, locale: "fr-FR" })).toBe("29 sept. – 24 oct.");
    expect(formatRelativeTime(new Date(2026, 8, 30, 10), { now, locale: "fr-FR" })).toContain("il y a 2 heures");
  });

  test("uses the runtime language by default and lets an explicit locale win", () => {
    const now = new Date(2026, 8, 30, 12);
    setHumanDateLocale("fr");
    expect(formatDate("2026-09-29", { now })).toBe("29 sept.");
    expect(formatDateRange("2026-09-29", "2026-10-24", { now })).toBe("29 sept. – 24 oct.");
    expect(formatRelativeTime(new Date(2026, 8, 30, 10), { now })).toContain("il y a 2 heures");
    expect(formatDayLabel(new Date(2026, 8, 30, 8), { now })).toBe("Aujourd’hui");
    expect(formatDate("2026-09-29", { now, locale: enUS })).toBe("Sep 29");
  });

  test("uses the requested time zone for calendar days and current-year decisions", () => {
    const now = new Date("2026-09-30T12:00:00Z");
    const instant = new Date("2027-01-01T00:30:00Z");
    expect(formatDate(instant, { now, locale: enUS, timeZone: "America/Los_Angeles" })).toBe("Dec 31");
    expect(formatDate(instant, { now, locale: enUS, timeZone: "UTC" })).toBe("Jan 1, 2027");
  });

  test("formats local ISO date-time strings", () => {
    expect(formatDateTime("2026-06-18T13:45:00")).toBe(
      "Jun 18, 2026, 1:45 PM",
    );
    expect(formatDateTime(new Date("2026-06-18T13:45:12Z"), { locale: enUS, timeZone: "UTC" }))
      .toBe("Jun 18, 2026, 1:45:12 PM");
  });

  test("list density omits time and the current year while the full label keeps it", () => {
    const now = new Date(2026, 8, 30);
    expect(formatDate("2026-09-28", { now, density: "list" })).toBe("Sep 28");
    expect(formatDate("2026-09-28", { now, density: "full" })).toBe("Sep 28, 2026");
    expect(formatDateTime("2026-09-28T09:16:00", { now, density: "list" })).toBe("Sep 28");
    expect(formatDateTime("2025-09-28T09:16:00", { now, density: "list" })).toBe("Sep 28, 2025");
  });

  test("empty and invalid values render empty", () => {
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
    expect(formatDate("")).toBe("");
    expect(formatDate("not a date")).toBe("");
    expect(formatDateTime(new Date(Number.NaN))).toBe("");
    expect(formatDateRange("2026-09-29", null, { now: new Date(2026, 8, 30) })).toBe("Sep 29");
    expect(formatRelativeTime("bad")).toBe("");
    expect(dateFromValue("not a date")).toBeNull();
    expect(valueLabel(new Date(Number.NaN))).toBe("");
  });

  test("formats storage and time-control values through date-fns", () => {
    const date = new Date(2026, 5, 18, 9, 7);

    expect(dateFromUnknown({})).toBeNull();
    expect(dateFromValue(date.getTime())?.getTime()).toBe(date.getTime());
    expect(formatDateStorage(date)).toBe("2026-06-18");
    expect(formatDateStorage(null)).toBeNull();
    expect(formatTimeInput(date)).toBe("09:07");
  });

  // Run under several process zones (TZ=Asia/Tokyo, TZ=America/Los_Angeles and the default): each
  // assertion holds in every one, and the naive `yyyy-MM-ddTHH:mm` it replaces fails the first.
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

    expect(formatDateStorage(midnight, "America/Chicago")).toBe("2026-11-20");
    expect(formatDateStorage(midnight, "America/Los_Angeles")).toBe("2026-11-19");
    expect(formatDateStorage(midnight, "Asia/Tokyo")).toBe("2026-11-20");
    expect(formatDateStorage(null, "Asia/Tokyo")).toBeNull();
  });

  test("a GraphQL time of day round-trips through the native time control", () => {
    // The control's `HH:mm` is itself a `Time` input, so it reads back unchanged.
    for (const wire of ["00:00", "09:07", "23:59"]) expect(formatTimeInput(wire)).toBe(wire);
    expect(formatTimeInput("09:07:00")).toBe("09:07");
    expect(formatTimeInput("09:07:59.999999")).toBe("09:07");
    expect(formatTimeInput("25:00")).toBe("");
    expect(formatTimeInput(null)).toBe("");
  });
});
