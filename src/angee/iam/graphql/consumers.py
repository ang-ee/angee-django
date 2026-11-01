"""Native WebSocket actor binding for protected GraphQL."""

from rebac.graphql.strawberry import RebacChannelsConsumerMixin
from angee.graphql.consumers import AngeeGraphQLWSConsumer


class IAMGraphQLWSConsumer(RebacChannelsConsumerMixin, AngeeGraphQLWSConsumer):
    pass
