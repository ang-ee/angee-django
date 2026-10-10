"""GraphQL schema for the posts addon — feeds and the following surface.

Feeds are polled through the ``Bridge`` scheduler and browsed/managed in the
console through Hasura resources; ``FeedFollow`` exposes the following edge. The
reused ``messaging.Reaction`` rows are exposed by messaging's existing nested
``MessageType.reaction_groups`` field. ``PostMetrics`` currently remains model and
permission data only; this schema declares no standalone or message type projection
for it.
"""

from __future__ import annotations

import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data import hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.integrate.schema import BridgeTypeMixin
from angee.parties.schema import HandleType

Handle = apps.get_model("parties", "Handle")
Feed = apps.get_model("posts", "Feed")
FeedFollow = apps.get_model("posts", "FeedFollow")
Message = apps.get_model("messaging", "Message")
Thread = apps.get_model("messaging", "Thread")


@strawberry_django.type(Thread, name="ThreadType", extend=True)
class PublicThreadType:
    """The posts-owned URL on the shared messaging thread projection."""

    subject_url: auto


@strawberry_django.type(Message, name="MessageType", extend=True)
class PublicMessageType:
    """The posts-owned root discriminator on the shared message projection."""

    is_original_post: auto


@strawberry_django.type(Feed)
class FeedType(BridgeTypeMixin, AngeeNode):
    """GraphQL projection of a connected public-content feed."""

    feed_backend_class: auto
    external_id: auto
    reply_hold: auto
    handle: HandleType | None


@strawberry_django.type(FeedFollow)
class FeedFollowType(AngeeNode):
    """GraphQL projection of a feed following/timeline subscription."""

    feed: FeedType | None
    handle: HandleType | None
    started_at: auto
    ended_at: auto
    created_at: auto
    updated_at: auto


_FEED_RESOURCE = hasura_model_resource(
    FeedType,
    model=Feed,
    name="feeds",
    filterable=[
        "id",
        "display_name",
        "feed_backend_class",
        "lifecycle",
        "runtime_status",
        "sync_stage",
        "last_sync_completed_at",
        "updated_at",
    ],
    sortable=["display_name", "lifecycle", "runtime_status", "last_sync_completed_at", "updated_at"],
    aggregatable=["id", "last_sync_items"],
    groupable=["feed_backend_class", "lifecycle", "runtime_status", "sync_stage"],
    insertable=["display_name", "feed_backend_class"],
    updatable=["reply_hold"],
    delete=False,
)
_FEED_FOLLOW_RESOURCE = hasura_model_resource(
    FeedFollowType,
    model=FeedFollow,
    name="feedFollows",
    filterable=["id", "feed", "handle", "ended_at", "started_at"],
    sortable=["started_at", "ended_at", "created_at"],
    aggregatable=["id"],
    groupable=["feed", "feed__display_name", "handle", "handle__display_name"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={
        "feed": public_pk_decoder(Feed),
        "handle": public_pk_decoder(Handle),
    },
)


_RESOURCE_TYPES = [
    *_FEED_RESOURCE.types,
    *_FEED_FOLLOW_RESOURCE.types,
]


_POSTS_SCHEMA_BUCKET: dict[str, list[type]] = {
    "type_extensions": [PublicThreadType, PublicMessageType],
    "query": [
        _FEED_RESOURCE.query,
        _FEED_FOLLOW_RESOURCE.query,
    ],
    "mutation": [
        _FEED_RESOURCE.mutation,
        _FEED_FOLLOW_RESOURCE.mutation,
    ],
    "types": [
        FeedType,
        FeedFollowType,
        *_RESOURCE_TYPES,
    ],
}


schemas = {
    "console": {
        **_POSTS_SCHEMA_BUCKET,
        "subscription": [
            changes(Feed, field="feedChanged"),
        ],
    },
}
