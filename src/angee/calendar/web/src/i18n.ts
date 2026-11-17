import { createNamespaceT } from "@angee/ui";

export const enCalendarMessages: Record<string, string> = {
  "form.when": "When",
  "form.description": "Description",
  "form.details": "Details",
  "occurrence.busy": "Busy",
  "source.calendar": "{calendar} · {party}",
  "reschedule.failed": "Could not reschedule the event.",
  "connection.name": "Name",
  "connection.connect.menu": "Add a calendar",
  "connection.action.sync": "Sync now",
  "connection.action.unsubscribe": "Unsubscribe",
  "connection.group.lastSync": "Last sync",
};

export const useCalendarT = createNamespaceT("calendar", enCalendarMessages);
