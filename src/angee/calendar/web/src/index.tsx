import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
  type MenuDeclarations,
} from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { CalendarCog, CalendarDays, CalendarRange } from "lucide-react";

import { enCalendarMessages } from "./i18n";
import { CALENDAR_MODEL, EVENT_MODEL } from "./resources";
import { OCCURRENCE_SOURCES } from "./sources";
import { CALENDAR_PICKER_WIDGET, calendarPickerWidget } from "./views/CalendarPicker";
import { calendarEventForm } from "./views/CalendarEventForm";
import { calendarForm } from "./views/CalendarForm";

export { CALENDAR_MODEL, EVENT_MODEL } from "./resources";
/** The occurrence-source seam other addons contribute their dated rows to. */
export { OCCURRENCE_SOURCES, type OccurrenceSourceContext, type OccurrenceSourceContribution } from "./sources";

const CALENDAR_ID = "calendar";

const calendarRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "calendar.events",
    "/calendar",
    lazyRouteComponent(() => import("./views/CalendarPage"), "CalendarPage"),
    EVENT_MODEL,
    { menu: "calendar.events" },
  ),
  ...resourcePageRoutes(
    "calendar.calendars",
    "/calendar/calendars",
    lazyRouteComponent(() => import("./views/CalendarsPage"), "CalendarsPage"),
    CALENDAR_MODEL,
    { menu: "calendar.calendars" },
  ),
];

const calendarMenu: MenuDeclarations = {
  [CALENDAR_ID]: { label: "Calendar", icon: "calendar-app" },
  "calendar.events": { parent: CALENDAR_ID, label: "Calendar", icon: "calendar-events", route: "calendar.events" },
  "calendar.calendars": { parent: CALENDAR_ID, label: "Calendars", icon: "calendar-calendars", route: "calendar.calendars" },
};

const calendar = defineBaseAddon({
  id: CALENDAR_ID,
  routes: calendarRoutes,
  menus: calendarMenu,
  forms: {
    [EVENT_MODEL]: calendarEventForm,
    [CALENDAR_MODEL]: calendarForm,
  },
  // An event's calendar: the calendars its viewer writes, or their own on request.
  widgets: { [CALENDAR_PICKER_WIDGET]: calendarPickerWidget },
  i18n: { calendar: enCalendarMessages },
  icons: {
    "calendar-app": CalendarDays,
    "calendar-events": CalendarRange,
    "calendar-calendars": CalendarCog,
  },
  containers: {
    // Other addons overlay their dated rows on the calendar page (projects: due tasks).
    [OCCURRENCE_SOURCES]: {},
  },
});

export default calendar;
