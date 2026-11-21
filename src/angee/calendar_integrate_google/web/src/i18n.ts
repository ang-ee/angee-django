import { createNamespaceT } from "@angee/ui/i18n";

export const enGoogleCalendarMessages = {
  "connect.button": "Connect Google Calendar",
  "connect.title": "Connect Google Calendar",
  "connect.description":
    "Choose one of your Google accounts. Every calendar it lists syncs into a read-only calendar, checked every 15 minutes.",
  "connect.error": "Could not connect the Google account's calendars.",
};
export const useGoogleCalendarT = createNamespaceT("calendar_integrate_google", enGoogleCalendarMessages);
