"""Translate messaging's native event into workflow admission and watch changes."""

from typing import Any

from django.apps import apps
from django.db import models
from rebac import PermissionDenied

from angee.base.scoping import read_scoped_queryset
from angee.messaging.events import message_ingested
from angee.workflows.triggers import TriggerSource


class MessageIngested(TriggerSource):
    """Admit current messages, optionally scoped to a readable channel."""

    key = "message_ingested"
    label = "Message ingested"
    model_label = "messaging.Message"
    scope_fields = ("channel",)

    @classmethod
    def matching_triggers(cls, queryset: Any, record: Any) -> Any:
        """Write no ledger row for a message outside an authored channel scope."""
        return queryset.filter(models.Q(channel_id=record.channel_id) | models.Q(channel__isnull=True))

    @classmethod
    def check_access(cls, trigger: Any, actor: Any, record: Any = None) -> None:
        """Check the channel when enabling and recheck current scope on admission."""
        channel_id = trigger.channel_id
        if record is not None:
            if channel_id is not None and record.channel_id != channel_id:
                raise PermissionDenied("The message no longer belongs to the trigger's channel.")
            channel_id = record.channel_id
        if channel_id is not None:
            visible = read_scoped_queryset(apps.get_model("messaging", "Channel"), actor)
            if visible is None or not visible.filter(pk=channel_id).exists():
                raise PermissionDenied("The acting user cannot read the channel.")

    @classmethod
    def connect(cls) -> None:
        """Connect once during the source registry's ready lifecycle."""
        message_ingested.connect(cls.record, dispatch_uid="workflows.message_ingested", weak=False)

    @classmethod
    def record(cls, sender: Any, instance: Any, **kwargs: Any) -> None:
        """Share durable source capture inside the message owner's transaction."""
        cls.dispatch(sender, instance)
