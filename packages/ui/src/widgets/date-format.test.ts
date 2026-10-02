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
  formatDuration,
  formatRelativeTime,
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

  test("formats elapsed time and duration through their locale owners", () => {
    const now = new Date(2026, 8, 30, 12);
    const earlier = new Date(2026, 8, 30, 10);
    expect(formatRelativeTime(earlier, { now, locale: enUS })).toBe("about 2 hours ago");
    expect(formatRelativeTime(earlier, { now, locale: fr })).toContain("2 heures");
    expect(formatDuration(2.5, "week", { locale: enUS })).toBe("2.5 wk");
    expect(formatDuration(2.5, "week", { locale: enUS, style: "full" })).toBe("2.5 weeks");
    expect(formatDuration(2.5, "week", { locale: fr, style: "full" })).toContain("2,5");
  });

  test("accepts a fixed app language tag without consulting the browser locale", () => {
    const now = new Date(2026, 8, 30, 12);
    expect(formatDate("2026-09-29", { now, locale: "fr-FR" })).toBe("29 sept.");
    expect(formatDateRange("2026-09-29", "2026-10-24", { now, locale: "fr-FR" })).toBe("29 sept. – 24 oct.");
    expect(formatRelativeTime(new Date(2026, 8, 30, 10), { now, locale: "fr-FR" })).toContain("il y a 2 heures");
    expect(formatDuration(2.5, "week", { locale: "fr-FR", style: "full" })).toContain("2,5");
  });

  test("uses the runtime language by default and lets an explicit locale win", () => {
    const now = new Date(2026, 8, 30, 12);
    setHumanDateLocale("fr");
    expect(formatDate("2026-09-29", { now })).toBe("29 sept.");
    expect(formatDateRange("2026-09-29", "2026-10-24", { now })).toBe("29 sept. – 24 oct.");
    expect(formatRelativeTime(new Date(2026, 8, 30, 10), { now })).toContain("il y a 2 heures");
    expect(formatDuration(2.5, "week", { style: "full" })).toContain("2,5");
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
    expect(formatDuration(Number.NaN, "week")).toBe("");
    expect(dateFromValue("not a date")).toBeNull();
    expect(valueLabel(new Date(Number.NaN))).toBe("");
  });

  test("formats storage and time-control values through date-fns", () => {
    const date = new Date(2026, 5, 18, 9, 7);

    expect(dateFromUnknown({})).toBeNull();
    expect(dateFromValue(date.getTime())?.getTime()).toBe(date.getTime());
    expect(formatDateStorage(date)).toBe("2026-06-18");
    expect(formatDateStorage(null)).toBeNull();
    expect(formatDateTimeStorage(date)).toBe("2026-06-18T09:07");
    expect(formatTimeInput(date)).toBe("09:07");
  });
});
