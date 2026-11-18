import { createNamespaceT } from "@angee/ui/i18n";

export const enIcsMessages = {
  "subscribe.button": "Subscribe to calendar",
  "subscribe.title": "Subscribe to a calendar feed",
  "subscribe.description":
    "Enter the address of an ICS (iCalendar) feed. Its events are polled hourly into a read-only calendar.",
  "subscribe.name": "Name",
  "subscribe.namePlaceholder": "Team holidays",
  "subscribe.url": "Feed address",
  "subscribe.urlPlaceholder": "https://example.com/calendar.ics or webcal://…",
  "subscribe.submit": "Subscribe",
  "subscribe.submitting": "Subscribing…",
  "subscribe.error": "Could not subscribe to the calendar feed.",
};
export const useIcsT = createNamespaceT("calendar_integrate_ics", enIcsMessages);
