import { describe, expect, test } from "vitest";

import {
  dateFromUnknown,
  dateFromValue,
  formatDate,
  formatDateStorage,
  formatDateTime,
  formatDateTimeStorage,
  formatTimeInput,
  valueLabel,
} from "./date-format";

describe("date formatting", () => {
  test("formats ISO date-only strings", () => {
    expect(formatDate("2026-06-18")).toBe("Jun 18, 2026");
  });

  test("formats local ISO date-time strings", () => {
    expect(formatDateTime("2026-06-18T13:45:00")).toBe(
      "Jun 18, 2026, 1:45 PM",
    );
  });

  test("empty and invalid values render empty", () => {
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
    expect(formatDate("")).toBe("");
    expect(formatDate("not a date")).toBe("");
    expect(formatDateTime(new Date(Number.NaN))).toBe("");
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
