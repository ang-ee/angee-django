"""Translate messaging's native event into workflow admission and watch changes."""

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, to_object_ref

from angee.base.scoping import read_scoped_queryset
from angee.messaging.events import message_ingested
from angee.workflows.triggers import TriggerGrantTarget, TriggerSource


class MessageIngested(TriggerSource):
    """Admit messages from one readable, explicitly granted channel."""

    key = "message_ingested"
    label = "Message ingested"
    model_label = "messaging.Message"
    scope_fields = ("channel",)

    @classmethod
    def grant_targets(cls, trigger: Any) -> tuple[TriggerGrantTarget, ...]:
        """Grant the source channel's declared reader relation."""
        if trigger.channel_id is None:
            raise ValidationError("Message triggers require a channel grant scope.")
        channel = trigger.channel
        return (TriggerGrantTarget(
            to_object_ref(channel), "reader", type(channel).record_access_permission("reader"),
        ),)

    @classmethod
    def matching_triggers(cls, queryset: Any, record: Any) -> Any:
        """Write no ledger row for a message outside an authored channel scope."""
        return queryset.filter(channel_id=record.channel_id)

    @classmethod
    def check_access(cls, trigger: Any, actor: Any, record: Any = None) -> None:
        """Check the channel when enabling and recheck current scope on admission."""
        channel_id = trigger.channel_id
        if channel_id is None:
            raise ValidationError("Message triggers require a channel grant scope.")
        if record is not None:
            if record.channel_id != channel_id:
                raise PermissionDenied("The message no longer belongs to the trigger's channel.")
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
