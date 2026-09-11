"""GraphQL projection for channel-bound native workflow triggers."""

import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.messaging.schema import ChannelType
Trigger = apps.get_model("workflows", "Trigger")


@strawberry_django.type(Trigger, name="TriggerType", extend=True)
class MessageTriggerExtension:
    """Expose the message channel declared on an event Trigger."""

    message_channel: ChannelType | None = auto


schemas = {
    "console": {"type_extensions": [MessageTriggerExtension]},
}
