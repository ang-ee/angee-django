"""Settings fragments contributed when the calendar addon is installed."""

from __future__ import annotations

SETTINGS = {
    # Backends a ``calendar.CalendarSource`` may select. ``manual`` is the null
    # object (``ImplClassField`` requires a non-empty registry). Source addons add
    # theirs with a dotted key, e.g. ``"ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES.ics"``
    # from ``calendar_integrate_ics``.
    "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES": {
        "manual": "angee.calendar.backends.ManualCalendarBackend",
    },
}
"""Django settings contributed when the calendar addon is installed."""
