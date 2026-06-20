"""GraphQL schema for the messaging addon — threads, messages, and the inbox.

The console surface is read-heavy: messages arrive via channel sync, so the inbox
is the ``Message`` aggregate grouped by thread / sender / channel / status / time
(``rebac_aggregate_builder`` + the SDL-derived relation facets), and live updates
ride ``changes``. Thread and message expose ``crud`` for human edits (status moves,
deletes); parts/participants/metrics are read projections reached through their
message/thread.
"""

from __future__ import annotations

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto
from strawberry_django.pagination import OffsetPaginated

from angee.graphql.aggregates import rebac_aggregate_builder
from angee.graphql.crud import crud
from angee.graphql.node import AngeeNode, detail
from angee.graphql.subscriptions import changes

Channel = apps.get_model("messaging", "Channel")
Thread = apps.get_model("messaging", "Thread")
Message = apps.get_model("messaging", "Message")
Part = apps.get_model("messaging", "Part")
Fragment = apps.get_model("messaging", "Fragment")
MessageEdge = apps.get_model("messaging", "MessageEdge")
Participant = apps.get_model("messaging", "Participant")
Reaction = apps.get_model("messaging", "Reaction")
MessageMetrics = apps.get_model("messaging", "MessageMetrics")


@strawberry_django.type(Channel)
class ChannelType(AngeeNode):
    """GraphQL projection of a connected message channel (e.g. an email account)."""

    backend_class: auto
    status: auto
    config: strawberry.scalars.JSON
    last_sync_status: auto
    last_sync_completed_at: auto
    last_sync_items: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Fragment)
class FragmentType(AngeeNode):
    """GraphQL projection of a content-addressed text fragment."""

    kind: auto
    hash: auto
    text: auto


@strawberry_django.type(Part)
class PartType(AngeeNode):
    """GraphQL projection of one message body part."""

    position: auto
    type: auto
    disposition: auto
    role: auto
    cid: auto
    name: auto
    fragment: FragmentType | None
    created_at: auto


@strawberry_django.type(MessageMetrics)
class MessageMetricsType(AngeeNode):
    """GraphQL projection of a message's rolled-up public metrics."""

    view_count: auto
    like_count: auto
    repost_count: auto
    quote_count: auto
    reply_count: auto
    bookmark_count: auto


@strawberry_django.type(Participant)
class ParticipantType(AngeeNode):
    """GraphQL projection of a thread/message participant."""

    role: auto
    joined_at: auto
    left_at: auto


@strawberry_django.type(Message)
class MessageType(AngeeNode):
    """GraphQL projection of a message."""

    platform: auto
    direction: auto
    status: auto
    external_id: auto
    is_original_post: auto
    subject: auto
    preview: auto
    sent_at: auto
    received_at: auto
    thread: "ThreadType | None"
    channel: ChannelType | None
    parts: list[PartType]
    participants: list[ParticipantType]
    metrics: MessageMetricsType | None
    created_at: auto
    updated_at: auto


@strawberry_django.type(Thread)
class ThreadType(AngeeNode):
    """GraphQL projection of a thread."""

    platform: auto
    modality: auto
    visibility: auto
    subject: auto
    subject_url: auto
    message_count: auto
    last_message_at: auto
    channel: ChannelType | None
    messages: list[MessageType]
    participants: list[ParticipantType]
    created_at: auto
    updated_at: auto


@strawberry_django.type(MessageEdge)
class MessageEdgeType(AngeeNode):
    """GraphQL projection of a cross-message edge."""

    kind: auto
    confidence: auto
    created_at: auto


@strawberry_django.type(Reaction)
class ReactionType(AngeeNode):
    """GraphQL projection of an attributed reaction."""

    reaction: auto
    created_at: auto


@strawberry_django.filter_type(Message, lookups=True)
class MessageFilter:
    """Field lookups + relation facets for the inbox."""

    subject: auto
    status: auto
    platform: auto
    direction: auto
    thread: auto
    channel: auto
    sender: auto
    sent_at: auto


@strawberry_django.order_type(Message)
class MessageOrder:
    """Orderings accepted by the messages list."""

    sent_at: auto
    received_at: auto
    created_at: auto


@strawberry_django.filter_type(Thread, lookups=True)
class ThreadFilter:
    """Field lookups + relation facets for threads."""

    subject: auto
    platform: auto
    modality: auto
    visibility: auto
    channel: auto
    last_message_at: auto


@strawberry_django.order_type(Thread)
class ThreadOrder:
    """Orderings accepted by the threads list."""

    last_message_at: auto
    message_count: auto
    created_at: auto


@strawberry.input
class ThreadPatch:
    """Human-editable thread fields."""

    id: strawberry.ID
    subject: str | None = strawberry.UNSET
    visibility: str | None = strawberry.UNSET


@strawberry.input
class MessagePatch:
    """Human-editable message fields (mostly moderation status)."""

    id: strawberry.ID
    status: str | None = strawberry.UNSET
    subject: str | None = strawberry.UNSET


_message_aggregates = rebac_aggregate_builder(
    model=Message,
    name_prefix="MessageAggregate",
    aggregate_fields=["id"],
    group_by_fields=["thread", "sender", "channel", "status", "platform", "sent_at"],
    filter_type=MessageFilter,
    pagination_style="offset",
    enable_filter_echo=True,
).build()
_thread_aggregates = rebac_aggregate_builder(
    model=Thread,
    name_prefix="ThreadAggregate",
    aggregate_fields=["id", "message_count"],
    group_by_fields=["channel", "modality", "visibility", "last_message_at"],
    filter_type=ThreadFilter,
    pagination_style="offset",
    enable_filter_echo=True,
).build()


@strawberry.type
class MessagingQuery:
    """Messaging queries — the inbox, threads, and channels."""

    messages: OffsetPaginated[MessageType] = strawberry_django.offset_paginated(
        filters=MessageFilter,
        order=MessageOrder,
    )
    message: MessageType | None = detail(MessageType)
    threads: OffsetPaginated[ThreadType] = strawberry_django.offset_paginated(
        filters=ThreadFilter,
        order=ThreadOrder,
    )
    thread: ThreadType | None = detail(ThreadType)
    channels: OffsetPaginated[ChannelType] = strawberry_django.offset_paginated()
    channel: ChannelType | None = detail(ChannelType)
    message_aggregate = _message_aggregates.aggregate_field
    message_groups = _message_aggregates.group_by_field
    thread_aggregate = _thread_aggregates.aggregate_field
    thread_groups = _thread_aggregates.group_by_field


_AGGREGATE_TYPES = [
    _message_aggregates.aggregate_type,
    _message_aggregates.grouped_type,
    _message_aggregates.grouped_result_type,
    _message_aggregates.group_key_type,
    _thread_aggregates.aggregate_type,
    _thread_aggregates.grouped_type,
    _thread_aggregates.grouped_result_type,
    _thread_aggregates.group_key_type,
]


_MESSAGING_SCHEMA_BUCKET = {
    "query": [MessagingQuery],
    "mutation": [
        crud(ThreadType, update=ThreadPatch, delete=True),
        crud(MessageType, update=MessagePatch, delete=True),
    ],
    "types": [
        ChannelType,
        ThreadType,
        MessageType,
        PartType,
        FragmentType,
        MessageEdgeType,
        ParticipantType,
        ReactionType,
        MessageMetricsType,
        *_AGGREGATE_TYPES,
    ],
}


schemas = {
    "console": {
        **_MESSAGING_SCHEMA_BUCKET,
        "subscription": [
            changes(Message, field="messageChanged"),
            changes(Thread, field="threadChanged"),
        ],
    },
}
