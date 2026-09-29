"""Channel scope belongs to the messaging contribution, not the workflow engine."""

from django.apps import apps
from django.db import models

from angee.base.scoping import system_queryset


class TriggerMessaging(models.Model):
    """Optionally restrict one message source to one protected channel."""

    extends = "workflows.Trigger"
    hasura_readable_fields = ("channel",)
    hasura_filterable_fields = hasura_readable_fields
    hasura_insertable_fields = hasura_readable_fields
    hasura_updatable_fields = hasura_readable_fields

    channel = models.ForeignKey(
        "messaging.Channel", null=True, blank=True, on_delete=models.PROTECT, related_name="workflow_triggers",
    )

    class Meta:
        abstract = True


class ChannelWorkflowTriggers(models.Model):
    """Name the protected trigger rows in the channel owner's purge preview."""

    extends = "messaging.Channel"

    class Meta:
        abstract = True

    def purge_blockers(self) -> list[models.Model]:
        """Compose other blockers with every trigger retaining this channel."""
        return [
            *super().purge_blockers(),
            *system_queryset(apps.get_model("workflows", "Trigger")).filter(channel_id=self.pk).order_by("pk"),
        ]
