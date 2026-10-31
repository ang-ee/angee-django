"""Django identity for the native recurrence value and field capability."""

from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    """Expose Recurrence and RecurrenceField; their modules own their contracts."""

    default = True
    name = "angee.scheduling"
