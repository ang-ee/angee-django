"""Django config for Angee's calendar addon."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class CalendarConfig(AppConfig):
    """Source app manifest for calendars, their events and attendees."""

    default = True
    name = "angee.calendar"
    label = "calendar"

    def ready(self) -> None:
        """Bind the calendar's deletion refusals and its settings check after app population."""

        super().ready()
        # Deferred to ready(): a settings module resolves this app config before its
        # merge finishes, so module scope must not configure django.conf.settings.
        from angee.calendar import signals
        from angee.calendar.checks import check_publish_targets

        signals.connect()
        checks.register(check_publish_targets, checks.Tags.compatibility)
