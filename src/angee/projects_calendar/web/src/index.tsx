import { defineBaseAddon } from "@angee/app";
import { OCCURRENCE_SOURCES } from "@angee/calendar";

import { dueTasksCalendarSource } from "./calendar-source";
import { enProjectsCalendarMessages } from "./i18n";

const PROJECTS_CALENDAR_ID = "projects-calendar";

/** Projects and calendar meet here, so neither depends on the other. */
const projectsCalendar = defineBaseAddon({
  id: PROJECTS_CALENDAR_ID,
  i18n: { [PROJECTS_CALENDAR_ID]: enProjectsCalendarMessages },
  containers: {
    // The viewer's open tasks by due date overlay the calendar page.
    [OCCURRENCE_SOURCES]: {
      "projects-calendar.due-tasks": { content: dueTasksCalendarSource },
    },
  },
});

export default projectsCalendar;
