"""Settings fragments contributed by the Google Calendar source backend addon."""

from __future__ import annotations

SETTINGS = {
    # Dotted key so it merges into the calendar addon's registry rather than replacing it.
    "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES.google": "angee.calendar_integrate_google.backend.GoogleCalendarBackend",
    "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES.google_publishing": (
        "angee.calendar_integrate_google.backend.GooglePublishingCalendarBackend"
    ),
}
