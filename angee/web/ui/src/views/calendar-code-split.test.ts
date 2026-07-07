import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

// A source-contract check (node environment for a `file:` `import.meta.url`):
// the eager `CalendarView` must never statically import FullCalendar, so the
// bundler code-splits the heavy dependency out of the base bundle (§3.1). Every
// `@fullcalendar/*` import lives in the lazily-imported surface below.
function read(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");
}

describe("CalendarView code-splitting", () => {
  test("FullCalendar is reachable only through the dynamic surface import", () => {
    const view = read("./CalendarView.tsx");
    // No static (or dynamic) FullCalendar import in the eager View — only the
    // surface is dynamically imported.
    expect(view).not.toMatch(/import\s*\(?\s*["']@fullcalendar/);
    expect(view).not.toMatch(/from\s*["']@fullcalendar/);
    expect(view).toMatch(/lazy\(\(\)\s*=>\s*import\("\.\/calendar-surface"\)\)/);
  });

  test("the surface module owns the FullCalendar imports", () => {
    const surface = read("./calendar-surface.tsx");
    expect(surface).toMatch(/from "@fullcalendar\/react"/);
    expect(surface).toMatch(/from "@fullcalendar\/daygrid"/);
    expect(surface).toMatch(/from "@fullcalendar\/timegrid"/);
    expect(surface).toMatch(/from "@fullcalendar\/interaction"/);
  });
});
