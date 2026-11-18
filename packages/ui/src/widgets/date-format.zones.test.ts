import { describe, expect, test } from "vitest";

import { formatDateStorage, formatDateTimeStorage } from "./date-format";

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

    expect(formatDateStorage(midnight, "America/Chicago")).toBe("2026-11-20");
    expect(formatDateStorage(midnight, "America/Los_Angeles")).toBe("2026-11-19");
    expect(formatDateStorage(midnight, "Asia/Tokyo")).toBe("2026-11-20");
    expect(formatDateStorage(null, "Asia/Tokyo")).toBeNull();
  });
});
