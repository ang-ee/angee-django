import { createNamespaceT } from "@angee/ui";

export const enCalendarMessages: Record<string, string> = {
  "form.when": "When",
  "form.description": "Description",
  "form.details": "Details",
  "occurrence.busy": "Busy",
  "source.calendar": "{calendar} · {party}",
  "reschedule.failed": "Could not reschedule the event.",
  "source.name": "Name",
  "source.connect.menu": "Add a calendar",
  "source.action.sync": "Sync now",
  "source.action.unsubscribe": "Unsubscribe",
  "source.group.lastSync": "Last sync",
};

export const useCalendarT = createNamespaceT("calendar", enCalendarMessages);
