"""Django config for Angee's calendar addon."""

from __future__ import annotations

from django.apps import AppConfig


class CalendarConfig(AppConfig):
    """Source app manifest for calendars, their events and attendees."""

    default = True
    name = "angee.calendar"
    label = "calendar"

    def ready(self) -> None:
        """Bind the calendar's deletion refusals after app population."""

        super().ready()
        from angee.calendar import signals

        signals.connect()
