"""Same-row message-source declaration for a native workflow Trigger."""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.workflows.models import TriggerKind
from angee.workflows.states import RunStatus
from angee.workflows.trigger_declarations import EventAdmissionPolicy, EventTriggerConfig, TriggerConfig

MESSAGE_INGESTED = "message_ingested"


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

    def validated_config(self, *, require_publisher: bool = False) -> TriggerConfig:
        """Apply the same message-source rules to edits, activation, and delivery."""

        declaration = super().validated_config(require_publisher=require_publisher)
        message_source = isinstance(declaration, EventTriggerConfig) and declaration.source == MESSAGE_INGESTED
        if message_source:
            if self.kind != TriggerKind.EVENT or declaration.model != "messaging.message":
                raise ValidationError({"config": "Message-ingested triggers must target messaging.message."})
            if declaration.admission_policy != EventAdmissionPolicy.ONCE_PER_SUBJECT:
                raise ValidationError({"config": "Message-ingested triggers run once per Message."})
            if declaration.cooldown_seconds is not None or declaration.hourly_cap is not None:
                raise ValidationError({"config": "Required Message processing cannot use admission rate limits."})
            if self.message_channel_id is None:
                raise ValidationError({"message_channel": "Select the channel that publishes Messages."})
            if self.enabled:
                with system_context(reason="workflows_messaging.trigger.publication"):
                    published = (
                        type(self.workflow).objects.db_manager(self._state.db).current_published_for(self.workflow)
                    )
                if published is None or published.subject_declaration != declaration.model:
                    raise ValidationError(
                        {"workflow": "A message-ingested trigger requires a published messaging.Message workflow."}
                    )
        elif self.message_channel_id is not None:
            raise ValidationError({"message_channel": "A channel applies only to message-ingested event triggers."})
        return declaration

    def event_publisher_model(self, declaration: EventTriggerConfig) -> type[models.Model]:
        """Resolve the messaging publisher independently of GraphQL change subscriptions."""

        if declaration.source != MESSAGE_INGESTED:
            return super().event_publisher_model(declaration)
        message_model = self._meta.apps.get_model("messaging", "Message")
        if declaration.model != message_model._meta.label_lower:
            raise ValidationError({"config": "Message-ingested triggers must target messaging.message."})
        return message_model

    def event_subject_matches(self, subject: models.Model, *, source: str) -> bool:
        """Match the channel owned by this donor's event declaration."""

        if source == MESSAGE_INGESTED and self.message_channel_id != subject.channel_id:
            return False
        return super().event_subject_matches(subject, source=source)

    def validate_event_admission(self, subject: models.Model, *, source: str, dedup_key: str) -> None:
        """Prevent concurrent message processing by versions of the same lineage."""

        super().validate_event_admission(subject, source=source, dedup_key=dedup_key)
        if source != MESSAGE_INGESTED:
            return
        alias = self._state.db
        workflow_model = self._meta.get_field("workflow").remote_field.model
        run_model = self._meta.apps.get_model("workflows", "WorkflowRun")
        content_type = ContentType.objects.db_manager(alias).get_for_model(subject, for_concrete_model=False)
        versions = (
            system_queryset(workflow_model, using=alias, lock=None)
            .filter(models.Q(pk=self.workflow_id) | models.Q(published_from_id=self.workflow_id))
            .values("pk")
        )
        active = (
            system_queryset(run_model, using=alias, lock=("self",))
            .filter(
                workflow_id__in=models.Subquery(versions),
                subject_content_type_id=content_type.pk,
                subject_object_id=subject.pk,
                status__in=(RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING),
            )
            .exclude(dedup_key=dedup_key)
            .exists()
        )
        if active:
            raise ValidationError({"subject": "This workflow lineage is already processing the Message."})
