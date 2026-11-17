"""Concrete calendar sources for native Django test database setup."""

from angee.calendar import models as sources


class Calendar(sources.Calendar):
    """Concrete calendar used by source-addon tests."""

    rebac_grantable = sources.Calendar.rebac_grantable

    class Meta(sources.Calendar.Meta):
        """Django options for the shared calendar test table."""

        abstract = False
        app_label = "calendar"
        db_table = "test_calendar_calendar"
        rebac_resource_type = "calendar/calendar"


class CalendarEvent(sources.CalendarEvent):
    """Concrete calendar event used by source-addon tests."""

    class Meta(sources.CalendarEvent.Meta):
        """Django options for the shared calendar event test table."""

        abstract = False
        app_label = "calendar"
        db_table = "test_calendar_event"
        rebac_resource_type = "calendar/event"


class EventAttendee(sources.EventAttendee):
    """Concrete event attendee used by source-addon tests."""

    class Meta(sources.EventAttendee.Meta):
        """Django options for the shared event attendee test table."""

        abstract = False
        app_label = "calendar"
        db_table = "test_calendar_event_attendee"
        rebac_resource_type = "calendar/event_attendee"
