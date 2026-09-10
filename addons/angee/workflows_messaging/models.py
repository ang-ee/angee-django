"""Same-row message-source declaration for a native workflow Trigger."""

from django.core.exceptions import ValidationError
from django.db import models

from angee.workflows.models import TriggerKind
from angee.workflows.trigger_declarations import EventAdmissionPolicy, EventSource, EventTriggerConfig


class MessageTrigger(models.Model):
    """Select the channel whose ingested Messages publish this event trigger."""

    extends = "workflows.Trigger"
    hasura_readable_fields = ("message_channel",)
    hasura_filterable_fields = hasura_readable_fields
    hasura_sortable_fields = hasura_readable_fields
    hasura_aggregatable_fields: tuple[str, ...] = ()
    hasura_groupable_fields = hasura_readable_fields
    hasura_insertable_fields = hasura_readable_fields
    hasura_updatable_fields = hasura_readable_fields
    trigger_protected_fields = ("message_channel", "message_channel_id")
    message_channel = models.ForeignKey(
        "messaging.Channel", null=True, blank=True, on_delete=models.CASCADE, related_name="workflow_triggers"
    )

    class Meta:
        abstract = True
        constraints = (
            models.UniqueConstraint(
                fields=("workflow", "message_channel"),
                condition=models.Q(message_channel__isnull=False),
                name="uniq_workflow_message_channel_trigger",
            ),
        )

    def clean(self) -> None:
        super().clean()
        declaration = self.validated_config()
        message_source = isinstance(declaration, EventTriggerConfig) and declaration.source == EventSource.MESSAGE_INGESTED
        if message_source:
            if self.kind != TriggerKind.EVENT or declaration.model != "messaging.message":
                raise ValidationError({"config": "Message-ingested triggers must target messaging.message."})
            if declaration.admission_policy != EventAdmissionPolicy.ONCE_PER_SUBJECT:
                raise ValidationError({"config": "Message-ingested triggers run once per Message."})
            if declaration.cooldown_seconds is not None or declaration.hourly_cap is not None:
                raise ValidationError({"config": "Required Message processing cannot use admission rate limits."})
            if self.message_channel_id is None:
                raise ValidationError({"message_channel": "Select the channel that publishes Messages."})
            published = type(self.workflow).objects.current_published_for(self.workflow)
            if self.enabled and (published is None or published.subject_declaration != declaration.model):
                raise ValidationError(
                    {"workflow": "A message-ingested trigger requires a published messaging.Message workflow."}
                )
        elif self.message_channel_id is not None:
            raise ValidationError({"message_channel": "A channel applies only to message-ingested event triggers."})
