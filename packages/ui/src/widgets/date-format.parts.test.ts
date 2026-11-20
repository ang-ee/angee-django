import { describe, expect, test } from "vitest";

import { calendarDay, formatDateParts } from "./date-format";

const AT = "2026-09-28T12:00:00Z";

describe("formatDateParts", () => {
  test.each([
    ["en", /^Sep$/, /^28$/],
    // A combined month-and-day pattern would give a bare "9" here.
    ["ja", /^9月$/, /^28日$/],
    ["zh-CN", /^9月$/, /^28日$/],
    ["fi", /^syys/, /^28/],
  ])("formats the month and the day on their own in %s", (locale, month, day) => {
    const parts = formatDateParts(AT, { locale, timeZone: "UTC" })!;
    expect(parts.month).toMatch(month);
    expect(parts.day).toMatch(day);
    expect(Object.keys(parts).sort()).toEqual(["day", "month"]);
  });

  test("reads nothing from an unreadable value", () => {
    expect(formatDateParts("not a date")).toBeNull();
  });
});

describe("calendarDay", () => {
  test("is the day in the given zone as yyyy-MM-dd", () => {
    expect(calendarDay(new Date("2026-09-28T22:30:00Z"), "UTC")).toBe("2026-09-28");
    expect(calendarDay(new Date("2026-09-28T22:30:00Z"), "Pacific/Kiritimati")).toBe("2026-09-29");
  });

  test("writes four year digits and two month and day digits whatever the year", () => {
    expect(calendarDay(new Date("0987-03-04T12:00:00Z"), "UTC")).toBe("0987-03-04");
  });
});
