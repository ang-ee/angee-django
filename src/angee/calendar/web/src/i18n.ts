import { createNamespaceT } from "@angee/ui";

export const enCalendarMessages: Record<string, string> = {
  "form.when": "When",
  "form.description": "Description",
  "form.details": "Details",
  "occurrence.busy": "Busy",
  "source.calendar": "{calendar} · {party}",
  "reschedule.failed": "Could not reschedule the event.",
  "picker.startOwn": "Create my calendar",
};

export const useCalendarT = createNamespaceT("calendar", enCalendarMessages);
