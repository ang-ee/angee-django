"""Deliver the canonical messaging ingest event through native Trigger admission."""

import logging
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.utils import timezone

from angee.messaging.events import message_ingested
from angee.workflows.models import TriggerKind
from angee.workflows_messaging.models import MESSAGE_INGESTED

logger = logging.getLogger(__name__)


def connect() -> None:
    message_ingested.connect(deliver_message_event, dispatch_uid="workflows.message_ingested")
    for name in ("Message", "Part", "Thread"):
        model = apps.get_model("messaging", name)
        pre_delete.connect(
            protect_retained_source, sender=model, dispatch_uid=f"workflows.messaging.protect_{name.lower()}"
        )


def protect_retained_source(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Prevent deletion of messaging rows retained as native workflow evidence."""

    using = kwargs.get("using") or instance._state.db
    if instance.pk is None:
        return
    artifact_model = apps.get_model("workflows", "StepArtifact")
    run_model = apps.get_model("workflows", "WorkflowRun")
    target_types = ContentType.objects.db_manager(using).get_for_models(
        apps.get_model("messaging", "Message"),
        apps.get_model("messaging", "Part"),
        apps.get_model("messaging", "Thread"),
        for_concrete_models=False,
    )
    targets = [(target_types[sender], instance.pk)]
    if sender._meta.model_name == "thread":
        message_model = apps.get_model("messaging", "Message")
        part_model = apps.get_model("messaging", "Part")
        message_ids = message_model._base_manager.using(using).filter(thread_id=instance.pk).values("pk")
        part_ids = part_model._base_manager.using(using).filter(message__thread_id=instance.pk).values("pk")
        retained = artifact_model._base_manager.using(using).filter(
            models.Q(target_content_type=target_types[message_model], target_object_id__in=message_ids)
            | models.Q(target_content_type=target_types[part_model], target_object_id__in=part_ids)
            | models.Q(target_content_type=target_types[sender], target_object_id=instance.pk)
        )
        retained_runs = run_model._base_manager.using(using).filter(
            models.Q(subject_content_type=target_types[message_model], subject_object_id__in=message_ids)
            | models.Q(subject_content_type=target_types[part_model], subject_object_id__in=part_ids)
            | models.Q(subject_content_type=target_types[sender], subject_object_id=instance.pk)
        )
    else:
        content_type, object_id = targets[0]
        retained = artifact_model._base_manager.using(using).filter(
            target_content_type=content_type,
            target_object_id=object_id,
        )
        retained_runs = run_model._base_manager.using(using).filter(
            subject_content_type=content_type,
            subject_object_id=object_id,
        )
    if retained.exists() or retained_runs.exists():
        raise ProtectedError("Messaging source is retained by workflow evidence.", (retained, retained_runs))


def deliver_message_event(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Synchronously admit one run per matching channel Trigger.

    Admission shares the ingest transaction. Invalid trigger configuration disables
    the trigger without discarding the Message. Infrastructure failures propagate
    so the bridge retries a delivery whose admission could not be persisted.
    """

    del sender, kwargs
    if instance.channel_id is None:
        return
    trigger_model = apps.get_model("workflows", "Trigger")
    triggers = (
        trigger_model._base_manager.using(instance._state.db)
        .filter(
            kind=TriggerKind.EVENT,
            enabled=True,
            event_model_label="messaging.message",
            message_channel_id=instance.channel_id,
        )
        .select_related("workflow", "execution_actor", "message_channel__created_by")
        .order_by("pk")
    )
    for trigger in triggers:
        try:
            if trigger.validated_config(require_publisher=True).source != MESSAGE_INGESTED:
                continue
            actor = trigger.execution_actor or instance.created_by or trigger.message_channel.created_by
            if actor is None:
                raise ValidationError("Message workflow admission requires an execution actor.")
            if not trigger.condition_matches(type(instance), instance):
                continue
            trigger_model.objects.db_manager(instance._state.db).start_event(
                trigger.pk,
                subject=instance,
                occurrence_id=f"message-ingested:{instance.pk}",
                timestamp=timezone.now(),
                actor=actor,
                source=MESSAGE_INGESTED,
            )
        except ValidationError:
            trigger.sudo(reason="workflows_messaging.invalid_admission").disable()
            logger.exception("Disabled message workflow trigger %s after invalid admission.", trigger.pk)
