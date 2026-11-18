"""Settings fragments contributed when the calendar addon is installed."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES": dict[str, str],
        "ANGEE_CALENDAR_SOURCES_PER_OWNER": int,
        "ANGEE_CALENDAR_SUBSCRIBE_RATE": tuple[int, int],
        "CELERY_BEAT_SCHEDULE:append": dict[str, dict[str, object]],
    },
)

SETTINGS: _Settings = {
    # Backends a ``calendar.CalendarSource`` may select. ``manual`` is the null
    # object (``ImplClassField`` requires a non-empty registry). Source addons add
    # theirs with a dotted key, e.g. ``"ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES.ics"``
    # from ``calendar_integrate_ics``.
    "ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES": {
        "manual": "angee.calendar.backends.ManualCalendarBackend",
    },
    # The connected (not disconnected) calendar sources one user may keep.
    "ANGEE_CALENDAR_SOURCES_PER_OWNER": 25,
    # How often one user may connect a calendar: (burst, window in seconds).
    "ANGEE_CALENDAR_SUBSCRIBE_RATE": (10, 3600),
    # Retry, hourly, every purge an unsubscribe left unfinished.
    "CELERY_BEAT_SCHEDULE:append": {
        "calendar.purge_unsubscribed_sources": {
            "task": "calendar.purge_unsubscribed_sources",
            "schedule": 3600.0,
        },
    },
}
"""Django settings contributed when the calendar addon is installed."""
