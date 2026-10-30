"""Contribute the scoped channel through the native GraphQL type extension."""

import strawberry_django
from django.apps import apps

from angee.graphql.relations import actor_scoped_to_one
from angee.messaging.schema import ChannelType


@strawberry_django.type(apps.get_model("workflows", "Trigger"), name="TriggerType", extend=True)
class TriggerMessagingExtension:
    """Expose the scope only when the viewer can read its channel."""

    channel: ChannelType | None = actor_scoped_to_one("channel")


schemas = {"console": {"type_extensions": [TriggerMessagingExtension]}}
