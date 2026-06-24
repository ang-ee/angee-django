"""GraphQL schema for the messaging addon — threads, messages, and the inbox.

Messages arrive through channel sync and the manager-owned ingest path; the
console browses and moderates them through Hasura resources. Parts,
participants, edges, reactions, and metrics remain nested read projections
reached through their message/thread owners.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data import AngeeHasuraWriteBackend, hasura_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.parties.schema import HandleType

Integration = apps.get_model("integrate", "Integration")
Handle = apps.get_model("parties", "Handle")
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

    @strawberry_django.field(only=["display_name", "vendor", "status"])
    def display_name(self) -> str:
        """Return the channel's operator label so the inbox facet reads by name."""

        return cast(Any, self).display_label


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
    handle: HandleType | None
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
    sender: HandleType | None
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


def _aggregate_queryset(queryset: Any) -> Any:
    """Return the aggregate-safe variant of one REBAC queryset when available."""

    scoped = getattr(queryset, "scoped_for_aggregate", None)
    return scoped() if callable(scoped) else queryset


_CHANNEL_RESOURCE = hasura_resource(
    ChannelType,
    model=Channel,
    name="channels",
    filterable=[
        "id",
        "display_name",
        "backend_class",
        "status",
        "last_sync_status",
        "last_sync_completed_at",
        "updated_at",
    ],
    sortable=["display_name", "backend_class", "status", "last_sync_completed_at", "updated_at"],
    aggregatable=["id", "last_sync_items"],
    groupable=["backend_class", "status", "last_sync_status"],
    insert=False,
    update=False,
    delete=False,
    get_queryset=lambda info: Channel.objects.all(),
    get_aggregate_queryset=lambda info: _aggregate_queryset(Channel.objects.all()),
    write_backend=AngeeHasuraWriteBackend(Channel),
    id_decode=public_pk_decoder(Channel),
)
_MESSAGE_RESOURCE = hasura_resource(
    MessageType,
    model=Message,
    name="messages",
    filterable=[
        "id",
        "subject",
        "status",
        "platform",
        "direction",
        "thread",
        "channel",
        "sender",
        "sent_at",
    ],
    sortable=["sent_at", "received_at", "created_at"],
    aggregatable=["id"],
    groupable=[
        "thread",
        "thread__subject",
        "sender",
        "sender__display_name",
        "channel",
        "channel__display_name",
        "status",
        "platform",
        "sent_at",
    ],
    insert=False,
    updatable=["status", "subject"],
    field_id_decode={
        "thread": public_pk_decoder(Thread),
        "channel": public_pk_decoder(Integration),
        "sender": public_pk_decoder(Handle),
    },
    get_queryset=lambda info: Message.objects.all(),
    get_aggregate_queryset=lambda info: _aggregate_queryset(Message.objects.all()),
    write_backend=AngeeHasuraWriteBackend(Message),
    id_decode=public_pk_decoder(Message),
)
_THREAD_RESOURCE = hasura_resource(
    ThreadType,
    model=Thread,
    name="threads",
    filterable=["id", "subject", "platform", "modality", "visibility", "channel", "last_message_at"],
    sortable=["last_message_at", "message_count", "created_at"],
    aggregatable=["id", "message_count"],
    groupable=["channel", "channel__display_name", "modality", "visibility", "last_message_at"],
    insert=False,
    updatable=["subject", "visibility"],
    field_id_decode={"channel": public_pk_decoder(Integration)},
    get_queryset=lambda info: Thread.objects.all(),
    get_aggregate_queryset=lambda info: _aggregate_queryset(Thread.objects.all()),
    write_backend=AngeeHasuraWriteBackend(Thread),
    id_decode=public_pk_decoder(Thread),
)


_RESOURCE_TYPES = [
    *_CHANNEL_RESOURCE.types,
    *_MESSAGE_RESOURCE.types,
    *_THREAD_RESOURCE.types,
]


_MESSAGING_SCHEMA_BUCKET = {
    "query": [
        _CHANNEL_RESOURCE.query,
        _MESSAGE_RESOURCE.query,
        _THREAD_RESOURCE.query,
    ],
    "mutation": [
        _CHANNEL_RESOURCE.mutation,
        _MESSAGE_RESOURCE.mutation,
        _THREAD_RESOURCE.mutation,
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
        *_RESOURCE_TYPES,
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
