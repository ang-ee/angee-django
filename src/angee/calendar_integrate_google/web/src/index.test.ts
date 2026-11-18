import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import calendar, { SOURCES_TOOLBAR } from "@angee/calendar";
import { describe, expect, test } from "vitest";

import calendarIntegrateGoogle from "./index";

describe("angee.calendar_integrate_google addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(calendarIntegrateGoogle)).not.toThrow();
  });

  test("contributes its connect verb to the calendar sources toolbar", () => {
    // The calendar owner declares the container; this addon adds its verb.
    const { containers } = composeAddons(
      [{ id: calendar.id, containers: calendar.containers }, { ...calendarIntegrateGoogle, dependsOn: ["calendar"] }],
      { canonicalModelLabel: (model) => model },
    );
    expect(containers.children[SOURCES_TOOLBAR]?.map(({ id, sequence }) => ({ id, sequence }))).toEqual([
      { id: "calendar_integrate_google.connect", sequence: 20 },
    ]);
  });
});
