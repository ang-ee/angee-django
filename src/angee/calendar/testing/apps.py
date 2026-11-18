"""Django app identity for the optional calendar test composition."""

from django.apps import AppConfig


class CalendarTestingConfig(AppConfig):
    """Load the shared concrete calendar models after their source addon."""

    name = "angee.calendar.testing"
    label = "calendar_testing"
