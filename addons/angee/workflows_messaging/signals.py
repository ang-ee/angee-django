"""Deliver the canonical messaging ingest event through native Trigger admission."""

from typing import Any

from django.apps import apps
from django.utils import timezone

from angee.messaging.events import message_ingested
from angee.workflows.models import TriggerKind
from angee.workflows.trigger_declarations import EventSource


def connect() -> None:
    message_ingested.connect(deliver_message_event, dispatch_uid="workflows.message_ingested")


def deliver_message_event(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Synchronously admit one run per matching channel Trigger.

    Admission shares the ingest transaction. A failure propagates so a bridge cursor
    cannot advance past a Message whose required workflow run was not durably created.
    """

    del sender, kwargs
    if instance.channel_id is None:
        return
    trigger_model = apps.get_model("workflows", "Trigger")
    triggers = (
        trigger_model._base_manager.filter(
            kind=TriggerKind.EVENT,
            enabled=True,
            event_model_label="messaging.message",
            message_channel_id=instance.channel_id,
        )
        .select_related("workflow", "execution_actor", "message_channel__created_by")
        .order_by("pk")
    )
    for trigger in triggers:
        if trigger.validated_config(require_publisher=True).source != EventSource.MESSAGE_INGESTED:
            continue
        actor = trigger.execution_actor or instance.created_by or trigger.message_channel.created_by
        if actor is None:
            raise ValueError("Message workflow admission requires an execution actor.")
        if not trigger.condition_matches(type(instance), instance):
            continue
        run = trigger_model.objects.start_event(
            trigger.pk,
            subject=instance,
            occurrence_id=f"message-ingested:{instance.pk}",
            timestamp=timezone.now(),
            actor=actor,
            source=EventSource.MESSAGE_INGESTED,
            message_channel_id=instance.channel_id,
        )
        if run is None:
            raise RuntimeError("Message workflow admission was not durably accepted.")
