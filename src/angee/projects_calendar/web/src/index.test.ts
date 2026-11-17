import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import calendar, { OCCURRENCE_SOURCES } from "@angee/calendar";
import { describe, expect, test } from "vitest";

import projectsCalendar from "./index";

describe("angee.projects_calendar addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(projectsCalendar)).not.toThrow();
  });

  test("contributes the due-task source to the calendar page it composes with", () => {
    const composed = composeAddons([calendar, projectsCalendar], { canonicalModelLabel: (model) => model });
    expect(composed.containers.children[OCCURRENCE_SOURCES]?.map((child) => child.id)).toEqual([
      "projects-calendar.due-tasks",
    ]);
  });
});
