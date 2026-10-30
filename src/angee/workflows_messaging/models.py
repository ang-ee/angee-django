"""Channel scope belongs to the messaging contribution, not the workflow engine."""

from typing import ClassVar

from django.apps import apps
from django.db import models
from django_compose.models import Composable


class TriggerMessaging(Composable, extends="workflows.Trigger"):
    """Declare the protected channel required by a message source."""

    hasura_readable_fields: ClassVar[tuple[str, ...]] = ("channel",)
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields
    hasura_insertable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields

    channel = models.ForeignKey(
        "messaging.Channel", null=True, blank=True, on_delete=models.PROTECT, related_name="workflow_triggers",
    )

    class Meta:
        abstract = True


class ChannelWorkflowTriggers(Composable, extends="messaging.Channel"):
    """Name the protected trigger rows in the channel owner's purge preview."""


    class Meta:
        abstract = True

    def purge_blockers(self) -> list[models.Model]:
        """Compose other blockers with every trigger retaining this channel."""
        return [
            *super().purge_blockers(),
            *apps.get_model("workflows", "Trigger").system_queryset().filter(channel_id=self.pk).order_by("pk"),
        ]
