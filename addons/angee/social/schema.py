"""GraphQL schema for the social addon — feeds and the following surface.

Feeds are polled through the ``Bridge`` scheduler and browsed/managed in the
console through Hasura resources; ``FeedFollow`` exposes the following edge. The
engagement projections (``PostMetrics`` and the reused ``messaging.Reaction``) ride
the owning ``messaging.Message``, so their reader belongs on messaging's
``MessageType`` as a type extension rather than as standalone resources here.
"""

from __future__ import annotations

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data import hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.integrate.schema import BridgeSyncStatusMixin, IntegrationLabelMixin
from angee.parties.schema import HandleType

Handle = apps.get_model("parties", "Handle")
Feed = apps.get_model("social", "Feed")
FeedFollow = apps.get_model("social", "FeedFollow")


@strawberry_django.type(Feed)
class FeedType(IntegrationLabelMixin, BridgeSyncStatusMixin, AngeeNode):
    """GraphQL projection of a connected public-content feed."""

    backend_class: auto
    external_id: auto
    lifecycle: auto
    runtime_status: auto
    config: strawberry.scalars.JSON
    last_sync_status: auto
    last_sync_completed_at: auto
    last_sync_items: auto
    last_sync_summary: strawberry.scalars.JSON
    sync_error: auto
    sync_progress: strawberry.scalars.JSON
    handle: HandleType | None
    created_at: auto
    updated_at: auto


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
        "backend_class",
        "lifecycle",
        "runtime_status",
        "last_sync_status",
        "sync_stage",
        "last_sync_completed_at",
        "updated_at",
    ],
    sortable=["display_name", "lifecycle", "runtime_status", "last_sync_completed_at", "updated_at"],
    aggregatable=["id", "last_sync_items"],
    groupable=["backend_class", "lifecycle", "runtime_status", "last_sync_status", "sync_stage"],
    insert=False,
    update=False,
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


_SOCIAL_SCHEMA_BUCKET = {
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
        **_SOCIAL_SCHEMA_BUCKET,
        "subscription": [
            changes(Feed, field="feedChanged"),
        ],
    },
}
