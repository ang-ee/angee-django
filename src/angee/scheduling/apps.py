"""Django identity for the native recurrence value and field capability."""

from django.apps import AppConfig, apps


class SchedulingConfig(AppConfig):
    """Expose Recurrence and RecurrenceField; their modules own their contracts."""

    default = True
    name = "angee.scheduling"

    def ready(self) -> None:
        """Register the recurrence wire type when GraphQL is enabled."""
        super().ready()
        if apps.is_installed("angee.graphql"):
            from angee.graphql.field_types import register_field_type
            from angee.scheduling.fields import RecurrenceField

            register_field_type(RecurrenceField, str)
