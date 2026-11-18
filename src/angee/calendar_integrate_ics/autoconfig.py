"""Settings fragments contributed by the ICS calendar source backend addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the ICS backend into the calendar source registry. Dotted key so it
    # merges into the calendar addon's default rather than replacing it.
    "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES.ics": "angee.calendar_integrate_ics.backend.IcsCalendarBackend",
}
