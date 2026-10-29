"""GraphQL schema for the messaging addon — threads, messages, and the inbox.

Messages arrive through channel sync and the manager-owned ingest path; the
console browses and moderates them through Hasura resources. Parts,
participants and edges expose read-only collections; ingest and relation
producers retain their writes. Reactions remain nested message projections.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from enum import StrEnum
from typing import Any, Self, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.views.decorators.debug import sensitive_variables
from graphql import GraphQLError
from rebac import PermissionDenied, current_actor
from rebac.resources import model_resource_type
from strawberry import auto
from strawberry_django.utils.requests import get_request

from angee.base.models.identity import instance_from_public_id
from angee.graphql.actions import (
    ActionResult,
    ActionSelectionInput,
    action_guard,
    action_target,
    authorized_permission_target,
    resolve_action_target,
)
from angee.graphql.capabilities import held_permissions, permissions_field
from angee.graphql.data.hasura import (
    AngeeHasuraWriteBackend,
    SortAlias,
    declared_hasura_resource_fields,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata
from angee.graphql.ids import PublicID, instance_for_id, require_instance_for_id
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.relations import (
    RecordPointerType,
    RecordReferenceNode,
    actor_scoped_to_many,
    actor_scoped_to_one,
    with_record_reference_access,
)
from angee.graphql.subscriptions import changes
from angee.iam.audit import TrashedRefMixin
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES
from angee.iam.schema import UserType
from angee.integrate.live import PairingProjection, PairingState
from angee.integrate.schema import BridgeTypeMixin
from angee.messaging import connect
from angee.messaging.managers import (
    ChannelQuerySet,
    MessageQuerySet,
    ThreadQuerySet,
    message_subtype_options,
    strip_null_bytes,
)
from angee.messaging.models import Message as MessageModel
from angee.messaging.models import Part as PartModel
from angee.messaging.models import Thread as ThreadModel
from angee.messaging.models import ThreadedModelMixin, chatter_record
from angee.parties.schema import HandleType, PartyType
from angee.storage.schema import FileType
from angee.tags.schema import TaggedNode, tags_input_extensions

Integration = apps.get_model("integrate", "Integration")
Handle = apps.get_model("parties", "Handle")
File = apps.get_model("storage", "File")
Channel = apps.get_model("messaging", "Channel")
Thread = apps.get_model("messaging", "Thread")
ThreadAttachment = apps.get_model("messaging", "ThreadAttachment")
ThreadFollower = apps.get_model("messaging", "ThreadFollower")
ThreadNotification = apps.get_model("messaging", "ThreadNotification")
MessageSubtype = apps.get_model("messaging", "MessageSubtype")
Message = apps.get_model("messaging", "Message")
Part = apps.get_model("messaging", "Part")
Fragment = apps.get_model("messaging", "Fragment")
MessageEdge = apps.get_model("messaging", "MessageEdge")
Participant = apps.get_model("messaging", "Participant")
Reaction = apps.get_model("messaging", "Reaction")
MessageStar = apps.get_model("messaging", "MessageStar")

_CHANNEL_EXTENSION_READ_FIELDS = declared_hasura_resource_fields(
    Channel,
    "hasura_readable_fields",
)
_CHANNEL_EXTENSION_FILTER_FIELDS = declared_hasura_resource_fields(
    Channel,
    "hasura_filterable_fields",
)
_CHANNEL_EXTENSION_ORDER_FIELDS = declared_hasura_resource_fields(
    Channel,
    "hasura_sortable_fields",
)
_CHANNEL_EXTENSION_AGGREGATE_FIELDS = declared_hasura_resource_fields(
    Channel,
    "hasura_aggregatable_fields",
)
_CHANNEL_EXTENSION_GROUP_FIELDS = declared_hasura_resource_fields(
    Channel,
    "hasura_groupable_fields",
)
_CHANNEL_EXTENSION_UPDATE_FIELDS = declared_hasura_resource_fields(
    Channel,
    "hasura_updatable_fields",
)
_CHANNEL_EXTENSION_PUBLIC_ID_FIELDS = tuple(
    name for name in _CHANNEL_EXTENSION_UPDATE_FIELDS if Channel._meta.get_field(name).is_relation
)


@strawberry_django.type(Channel)
class ChannelType(BridgeTypeMixin, AngeeNode):
    """GraphQL projection of a connected message channel (e.g. an email account)."""

    @strawberry_django.field
    def pairing_state(self) -> PairingState | None:
        """Return the live bridge's actual pairing state, or null for poll channels."""

        try:
            return connect.channel_pairing(cast(Any, self)).state
        except ValueError:
            return None


def _channel_webform_extension() -> type[Any] | None:
    """Project the messaging-owned donor only when this graph composes it."""

    required_fields = {
        "slug",
        "is_published",
        "form_schema_version",
        "form_schema",
        "max_body_bytes",
        "max_field_bytes",
    }
    if not required_fields.issubset(field.name for field in Channel._meta.get_fields()):
        return None

    @strawberry_django.type(Channel, name="ChannelType", extend=True)
    class ChannelWebformExtension:
        """Contribute public-form configuration onto messaging's channel node."""

        slug: auto
        is_published: auto
        form_schema_version: auto
        form_schema: strawberry.scalars.JSON
        max_body_bytes: auto
        max_field_bytes: auto

    return ChannelWebformExtension


_CHANNEL_WEBFORM_EXTENSION = _channel_webform_extension()


def _pairing_result(operation: Any, *args: Any, **kwargs: Any) -> Any:
    """Expose only pairing-owner failures whose text is deliberately public."""

    try:
        return operation(*args, **kwargs)
    except connect.PairingActionError as error:
        raise GraphQLError(str(error), extensions={"code": "BAD_USER_INPUT"}) from error


@strawberry.type
class MessagingPairingQuery:
    """Admin pairing state for live message channels."""

    @strawberry.field(permission_classes=ADMIN_PERMISSION_CLASSES)
    def channel_pairing(self, id: PublicID) -> PairingProjection:
        """Return pairing state in the caller's channel scope."""

        channel = resolve_action_target(
            Channel,
            id,
            queryset=Channel.objects.of_concrete_type(),
            reason="messaging.graphql.channel_pairing",
        )
        return _pairing_result(connect.channel_pairing, channel)


@strawberry.type
class MessagingPairingMutation:
    """Admin lifecycle verbs for live message-channel pairing."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def resume_channel_pairing(self, id: PublicID) -> ActionResult:
        """Resume retained pairing material or start a new pairing session."""

        with action_target(
            Channel, id, queryset=Channel.objects.of_concrete_type(), reason="messaging.graphql.resume_channel_pairing"
        ) as channel:
            _pairing_result(connect.resume_channel_pairing, channel)
        return ActionResult(ok=True, message="Channel connection started.")

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    @sensitive_variables("password", "material")
    def submit_channel_password(self, id: PublicID, password: str) -> ActionResult:
        """Submit one consume-once account password to the live channel session."""

        with action_target(
            Channel, id, queryset=Channel.objects.of_concrete_type(), reason="messaging.graphql.submit_channel_password"
        ) as channel:
            _pairing_result(connect.submit_channel_password, channel, password)
        return ActionResult(ok=True, message="Password submitted.")

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def skip_channel_password(self, id: PublicID) -> ActionResult:
        """Skip one optional consume-once secret round."""

        with action_target(
            Channel, id, queryset=Channel.objects.of_concrete_type(), reason="messaging.graphql.skip_channel_password"
        ) as channel:
            _pairing_result(connect.skip_channel_password, channel)
        return ActionResult(ok=True, message="Password skipped.")

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def reset_channel_pairing(self, id: PublicID) -> ActionResult:
        """Wipe released pairing material and restart with a fresh session."""

        with action_target(
            Channel, id, queryset=Channel.objects.of_concrete_type(), reason="messaging.graphql.reset_channel_pairing"
        ) as channel:
            _pairing_result(connect.reset_channel_pairing, channel)
        return ActionResult(ok=True, message="Pairing reset; link the channel again.")

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def disconnect_channel(self, id: PublicID) -> ActionResult:
        """Stop the live session while retaining reusable pairing material."""

        with action_target(
            Channel, id, queryset=Channel.objects.of_concrete_type(), reason="messaging.graphql.disconnect_channel"
        ) as channel:
            _pairing_result(connect.disconnect_channel, channel)
        return ActionResult(ok=True, message="Disconnected channel.")


@strawberry.type
class MessagingChannelMutation:
    """Owner/admin console mutation for deleting a connected channel."""

    @strawberry.mutation(name="delete_channel")
    def delete_channel(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview, then optionally purge, one channel and everything it ingested.

        Deleting a channel is a purge: its threads and messages FK the shared
        ``integrate.Integration`` parent with ``SET_NULL``, so removing the channel row
        alone would orphan them. The preview forecasts the channel + its Integration row
        + the channel's thread/message totals **and their CASCADE children** as
        **deleted** — the single inventory :meth:`ChannelManager.inventory` counts with
        ``.count()``, never materialized, so it stays fast on a 90k-message channel — and
        ``confirm`` runs that same scope through :meth:`ChannelManager.purge`
        (:meth:`ThreadManager.teardown_for_channel` first, then the channel + Integration
        row). Gated by the channel's own ``delete`` permission (its integration owner or a
        platform admin): a caller who cannot write the channel never resolves it, and
        :meth:`ChannelManager.purge` re-checks ``delete`` before any destructive work. A
        ``PROTECT``/``RESTRICT`` blocker on the confirm delete is surfaced as a blocked
        preview rather than a raw error.
        """

        with transaction.atomic():
            channel = require_instance_for_id(Channel, str(id), queryset=Channel.objects.for_write().of_concrete_type())
            preview = DeletePreview.from_counts(
                channel, Channel.objects.inventory(channel), blockers=channel.purge_blockers(),
            )
            if confirm and not preview.has_blockers:
                try:
                    Channel.objects.purge(channel)
                except (ProtectedError, RestrictedError) as error:
                    preview = DeletePreview.blocked_from_error(channel, error)
                else:
                    preview.deleted_instance = channel
        return preview


attach_delete_preview_metadata(
    MessagingChannelMutation,
    model=Channel,
    node=ChannelType,
    field="delete_channel",
)


@strawberry_django.type(Fragment)
class FragmentType(AngeeNode):
    """GraphQL projection of a content-addressed text fragment."""

    kind: auto
    hash: auto
    text: auto

    @strawberry.field(name="part_count")
    def part_count(self) -> int:
        """How many actor-readable parts share this fragment."""

        return cast(Any, self).part_count()

    @strawberry.field(name="message_count")
    def message_count(self) -> int:
        """How many distinct messages reference this fragment through their parts."""

        return cast(Any, self).message_count()


@strawberry_django.type(Part)
class PartType(AngeeNode):
    """GraphQL projection of one message body part."""

    position: auto
    type: auto
    disposition: auto
    role: auto
    cid: auto
    name: auto
    parent: "PartType | None" = actor_scoped_to_one("parent")
    fragment: FragmentType | None
    file: FileType | None = actor_scoped_to_one("file")
    created_at: auto


@strawberry_django.type(MessageSubtype)
class MessageSubtypeType(AngeeNode):
    """GraphQL projection of an Odoo-style message subtype."""

    key: auto
    model_label: auto
    name: auto
    description: auto
    internal: auto
    default: auto
    sequence: auto
    hidden: auto


@strawberry_django.type(Participant)
class ParticipantType(AngeeNode):
    """GraphQL projection of a thread/message participant."""

    role: auto
    handle: HandleType | None = actor_scoped_to_one("handle")
    joined_at: auto
    left_at: auto


@strawberry.type
class MessageReactionGroupType:
    """Odoo-style grouped reactions for one message."""

    reaction: str
    count: int
    self_reacted: bool = strawberry.field(name="self_reacted")
    handles: list[HandleType] = strawberry.field(default_factory=list)


@strawberry_django.type(Handle)
class RecordHandleType(AngeeNode):
    """Envelope identity exposed by record chatter, without party backedges."""

    platform: auto
    value: auto
    display_name: auto


@strawberry.type
class RecordMessageReactionGroupType:
    """A record-message reaction summary with narrowed handle projections."""

    reaction: str
    count: int
    self_reacted: bool = strawberry.field(name="self_reacted")
    handles: list[RecordHandleType] = strawberry.field(default_factory=list)


@strawberry_django.type(Message)
class MessageType(TrashedRefMixin, TaggedNode, AngeeNode):
    """GraphQL projection of a message; its managers see trash stamps and ``delete``."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["preview"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )

    @strawberry_django.field(only=["sent_at", "created_at"])
    def feed_order_key(self) -> str:
        """Opaque server key; descending ASCII comparison preserves feed order."""

        return cast(Any, self).feed_order_key

    @strawberry_django.field(only=["direction", "status", "is_trashed"])
    def is_held(self) -> bool:
        """Project the same held predicate used by delivery verbs and filters."""

        return cast(Any, self).is_held

    platform: auto
    direction: auto
    status: auto
    message_type: auto
    external_id: auto
    preview: auto
    sent_at: auto
    received_at: auto
    scheduled_at: auto
    revision: auto
    is_trashed: auto
    permissions = permissions_field(("write", "delete", "send_held"))
    sender: HandleType | None = actor_scoped_to_one("sender")

    @strawberry_django.field(
        only=["sender_id"],
        annotate={"_sender_name": lambda info: Message.objects.sender_name_expression()},
    )
    def sender_name(self) -> str:
        """Return the actor-visible sender name used by inbox ordering."""

        return cast(Any, self).sender_name()

    @strawberry_django.field(
        only=["thread_id"],
        annotate={"_thread_title": lambda info: Message.objects.thread_title_expression()},
    )
    def thread_title(self) -> str:
        """Return the actor-visible thread title used by inbox ordering."""

        return cast(Any, self).thread_title()

    @strawberry_django.field(
        only=["channel_id"],
        annotate={"_channel_vendor_name": lambda info: Message.objects.channel_vendor_name_expression()},
    )
    def channel_vendor_name(self) -> str:
        """Return the actor-visible channel vendor used by inbox ordering."""

        return cast(Any, self).channel_vendor_name()

    parent: "MessageType | None" = actor_scoped_to_one("parent")
    subtype: MessageSubtypeType | None
    thread: "ThreadType | None" = actor_scoped_to_one("thread")
    channel: ChannelType | None = actor_scoped_to_one("channel")
    participants: list[ParticipantType] = actor_scoped_to_many("participants")
    created_at: auto
    updated_at: auto

    @strawberry_django.field(
        graphql_type=list[PartType], prefetch_related=["parts__fragment", "parts__file__mime_type"]
    )
    @staticmethod
    def parts(parent: strawberry.Parent[MessageModel]) -> list[PartModel]:
        """Return this message's MIME parts in depth-first reading order."""

        return Part.objects.reading_order_for_message(parent)

    @strawberry.field
    def title(self) -> str:
        """Return the message's title text — its ``TITLE`` part's fragment, or ``""``.

        No optimizer prefetch hint here: a query that also selects ``parts`` makes
        the optimizer build its own ``Prefetch("parts", …)``, and Django rejects a
        second queryset for the same lookup. The model method rides an existing
        parts prefetch when one is loaded and otherwise costs one indexed probe.
        """

        return cast(Any, self).title()

    @strawberry.field
    def edges(self) -> list["MessageEdgeType"]:
        """Return this message's cross-message edges, both directions.

        The fragment-derived quote graph plus post relations — the "how it
        interconnects" read the content data view renders. Actor-scoped end to
        end: the owner (``MessageEdgeManager.for_message``) keeps only edges
        whose far endpoint the actor may read, because quote edges cross
        channels by construction.
        """

        return cast(list["MessageEdgeType"], MessageEdge.objects.for_message(cast(Any, self)))

    @strawberry_django.field(prefetch_related=["reactions__handle"])
    def reaction_groups(self, info: strawberry.Info) -> list[MessageReactionGroupType]:
        """Return reactions grouped by content, with current-user state."""

        return _message_reaction_groups(self, _request_user(info))

    @strawberry_django.field(prefetch_related=[lambda info: MessageStar.objects.prefetch_for(_request_user(info))])
    def starred(self, info: strawberry.Info) -> bool:
        """Return whether the current user has starred this message."""

        return bool(MessageStar.objects.is_starred(self, user=_request_user(info)))

    @strawberry.field
    def needaction(self, info: strawberry.Info) -> bool:
        """Return whether this message sits past the current user's read receipt."""

        resolved = getattr(self, "_current_user_needaction", None)
        if resolved is not None:
            return bool(resolved)
        return bool(ThreadFollower.objects.needaction_for_message(self, user=_request_user(info)))

    @strawberry_django.field(
        only=["thread_id", "sender_id", "created_by_id", "message_type", "direction"],
    )
    def can_edit(self, info: strawberry.Info) -> bool:
        """Return the message-owned edit rule using cached record permissions."""

        return cast(Any, self).can_edit(**_message_access(self, info))

    @strawberry_django.field(
        only=["thread_id", "sender_id", "created_by_id", "message_type"],
    )
    def can_delete(self, info: strawberry.Info) -> bool:
        """Return the message-owned deletion rule using cached record permissions."""

        return cast(Any, self).can_delete(**_message_access(self, info))


@strawberry_django.type(Message)
class RecordMessageParentType(AngeeNode):
    """Minimal parent-message context shown in record chatter."""

    preview: auto
    message_type: auto
    subtype: MessageSubtypeType | None


@strawberry_django.type(Message)
class RecordMessageType(TrashedRefMixin, AngeeNode):
    """Record-gated message projection without generic inbox relation backedges.

    A trashed message appears only in its moderators' removed list, where the
    trash stamps say who removed it, when and why.
    """

    direction: auto
    status: auto
    message_type: auto
    is_trashed: auto
    preview: auto
    sent_at: auto
    created_at: auto
    sender: "RecordHandleType | None" = actor_scoped_to_one("sender")
    parent: "RecordMessageParentType | None" = actor_scoped_to_one("parent")
    subtype: MessageSubtypeType | None

    @strawberry_django.field(only=["edit_history"])
    def edited_at(self) -> datetime | None:
        """The latest content edit, from the message's newest-first history."""

        history = cast(Any, self).edit_history or []
        return datetime.fromisoformat(history[0]["edited_at"]) if history else None

    @strawberry_django.field(only=["created_by_id"])
    def is_self(self, info: strawberry.Info) -> bool:
        """Whether the current reader created this message."""

        user = _request_user(info)
        return user is not None and cast(Any, self).created_by_id == user.pk

    @strawberry_django.field(only=["parent_id"])
    def is_reply(self) -> bool:
        """Reply shape without requiring the reader to see the parent projection."""

        return cast(Any, self).parent_id is not None

    @strawberry_django.field(
        only=["sender_id"],
        annotate={"_sender_name": lambda info: Message.objects.sender_name_expression()},
    )
    def author_label(self) -> str | None:
        """Actor-readable sender label; the web owns the withheld-identity copy."""

        return cast(Any, self).sender_name() or None

    @strawberry.field(graphql_type=list[PartType])
    @staticmethod
    def parts(parent: strawberry.Parent[MessageModel]) -> list[PartModel]:
        """Return this record message's MIME parts in depth-first reading order."""

        return Part.objects.reading_order_for_message(parent)

    @strawberry.field
    def title(self) -> str:
        """Return the message title text from its sparse title part."""

        return cast(Any, self).title()

    @strawberry.field
    def reaction_groups(self, info: strawberry.Info) -> list[RecordMessageReactionGroupType]:
        """Return reaction groups with record-safe handle projections."""

        return _record_message_reaction_groups(self, _request_user(info))

    @strawberry.field
    def starred(self, info: strawberry.Info) -> bool:
        """Return whether the current user has starred this message."""

        return bool(MessageStar.objects.is_starred(self, user=_request_user(info)))

    @strawberry.field
    def needaction(self, info: strawberry.Info) -> bool:
        """Return whether this message sits past the current user's read receipt."""

        resolved = getattr(self, "_current_user_needaction", None)
        if resolved is not None:
            return bool(resolved)
        return bool(ThreadFollower.objects.needaction_for_message(self, user=_request_user(info)))

    @strawberry_django.field(
        only=["thread_id", "sender_id", "created_by_id", "message_type", "direction"],
    )
    def can_edit(self, info: strawberry.Info) -> bool:
        """Return the model-owned edit capability for this record message."""

        return cast(Any, self).can_edit(**_message_access(self, info))

    @strawberry_django.field(
        only=["thread_id", "sender_id", "created_by_id", "message_type"],
    )
    def can_delete(self, info: strawberry.Info) -> bool:
        """Return the model-owned delete capability for this record message."""

        return cast(Any, self).can_delete(**_message_access(self, info))

    @strawberry_django.field(
        only=["thread_id", "message_type", "is_trashed"],
    )
    def can_trash(self, info: strawberry.Info) -> bool:
        """Return whether the reader moderates this record and may trash this comment."""

        return cast(Any, self).can_trash(moderate_access=_message_access(self, info)["moderate_access"])


@strawberry_django.type(Thread)
class ThreadType(TaggedNode, AngeeNode):
    """GraphQL projection of a thread."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name,
        only=["title__text"],
        select_related=["title"],
        description=NODE_DISPLAY_NAME_DESCRIPTION,
    )

    @strawberry_django.field(
        only=["title__text", "modality"],
        select_related=["title"],
        description=(
            "The thread title, else the people in the conversation for this viewer; empty when "
            "neither is known. An untitled thread costs a few queries: select it on single "
            "threads, never per row of a list."
        ),
    )
    def conversation_label(self) -> str:
        """Resolve one thread's label through :meth:`ThreadQuerySet.conversation_labels`."""

        thread = cast(Any, self)
        return thread.display_title or Thread.objects.filter(pk=thread.pk).conversation_labels().get(thread.pk, "")

    platform: auto
    modality: auto
    visibility: auto
    permissions = permissions_field(("write",))
    # Deliberate shape fork vs MessageType.title (a String): the thread's title
    # IS a fragment pointer (grouping keys on it), so the projection exposes the
    # content-addressed row; a message's title is derived text from its part.
    title: FragmentType | None
    message_count: auto
    last_message_at: auto
    channel: "ChannelType | None" = actor_scoped_to_one("channel")
    messages: list[MessageType] = actor_scoped_to_many("messages")
    participants: list[ParticipantType] = actor_scoped_to_many("participants")
    created_at: auto
    updated_at: auto


@strawberry_django.type(Thread)
class RecordThreadType(AngeeNode):
    """Record-gated thread summary without generic inbox backedges."""

    platform: auto
    modality: auto
    visibility: auto
    title: FragmentType | None
    @strawberry_django.field(only=["merged_into_id"])
    def message_count(self) -> int:
        return int(cast(Any, self).chatter_totals()["count"] or 0)

    @strawberry_django.field(only=["merged_into_id"])
    def last_message_at(self) -> datetime | None:
        return cast(Any, self).chatter_totals()["latest"]
    messages: list[RecordMessageType]
    created_at: auto
    updated_at: auto


_FOLLOWER_PARTY_FIELD = actor_scoped_to_one("party")
_PERSON_USER_FIELD = actor_scoped_to_one("user")


@strawberry.type
class FollowerIdentity:
    """Party identity and an optional readable account, shared by follower projections."""

    party: PartyType | None = _FOLLOWER_PARTY_FIELD

    @strawberry_django.field(only=["party_id"], prefetch_related=["party__person__user"])
    def user(self) -> UserType | None:
        """Project the person's account through the shared relation guard; never store it."""

        party = _FOLLOWER_PARTY_FIELD.base_resolver(self)
        if party is None:
            return None
        cached_person = party._state.fields_cache.get("person")
        if "person" not in party._state.fields_cache or (
            cached_person is not None and getattr(cached_person, "_rebac_actor", None) != current_actor()
        ):
            person = apps.get_model("parties", "Person").objects.with_actor(current_actor()).filter(pk=party.pk).first()
            party._meta.get_field("person").set_cached_value(party, person)
        person = party._state.fields_cache["person"]
        if person is None:
            return None
        return cast(UserType | None, _PERSON_USER_FIELD.base_resolver(person))


@strawberry_django.type(ThreadFollower)
class RecordThreadFollowerType(FollowerIdentity, AngeeNode):
    """Record-scoped follower projection without message or thread backedges."""

    notification_policy: auto
    subtype_keys: strawberry.scalars.JSON
    metadata: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(ThreadNotification)
class RecordThreadNotificationType(AngeeNode):
    """Record-scoped notification projection with narrowed message context."""

    message: "RecordMessageParentType | None" = actor_scoped_to_one("message")
    follower: "RecordThreadFollowerType | None" = actor_scoped_to_one("follower")
    user: "UserType | None" = actor_scoped_to_one("user")
    notification_type: auto
    notification_status: auto
    read_at: auto
    failure_type: auto
    failure_reason: auto
    metadata: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(ThreadAttachment)
class ThreadAttachmentType(RecordReferenceNode):
    """GraphQL projection of a model-record thread attachment.

    The attached record's identity projects only while the reader can read it.
    """

    role: auto
    label: auto
    metadata: strawberry.scalars.JSON
    thread: "ThreadType | None" = actor_scoped_to_one("thread")
    created_at: auto
    updated_at: auto

    record_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["content_type_id", "object_id"],
    )
    record_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["content_type_id", "object_id"],
    )


@strawberry_django.type(ThreadFollower)
class ThreadFollowerType(FollowerIdentity, AngeeNode):
    """GraphQL projection of a party's thread subscription — policy plus read receipt."""

    thread: "ThreadType | None" = actor_scoped_to_one("thread")
    attachment: "ThreadAttachmentType | None" = actor_scoped_to_one("attachment")
    notification_policy: auto
    subtype_keys: strawberry.scalars.JSON
    last_read_message: "MessageType | None" = actor_scoped_to_one("last_read_message")
    metadata: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(Message)
class NotificationMessagePointerType:
    """Record-readable inbox context without message bodies or generic backedges."""

    id: PublicID = strawberry.field(resolver=AngeeNode.id)
    message_type: auto
    subtype: MessageSubtypeType | None
    created_at: auto
    sender: RecordHandleType | None = actor_scoped_to_one("sender")

    @strawberry.field
    def attachment(self) -> RecordPointerType:
        """Return the attachment-owned pointer of the record this notification is about."""

        return RecordPointerType.from_reference(
            cast(Any, self)._inbox_attachment.record_ref, label=cast(Any, self)._inbox_attachment.label,
        )


@strawberry_django.type(ThreadNotification)
class ThreadNotificationType(AngeeNode):
    """GraphQL projection of a recipient's delivery and per-item acknowledgement."""

    thread: "ThreadType | None" = actor_scoped_to_one("thread")
    attachment: "ThreadAttachmentType | None" = actor_scoped_to_one("attachment")
    follower: "ThreadFollowerType | None" = actor_scoped_to_one("follower")

    @strawberry_django.field(only=["message_id", "attachment_id", "thread_id", "user_id"])
    def message(self) -> NotificationMessagePointerType | None:
        """Project only recipient context authorized by the attached record's read owner."""

        row = cast(Any, self)
        if not hasattr(row, "_inbox_message_pointer"):
            ThreadNotification.objects.prime_message_pointers([row], current_actor())
        return cast(NotificationMessagePointerType | None, row._inbox_message_pointer)

    user: "UserType | None" = actor_scoped_to_one("user")
    notification_type: auto
    notification_status: auto
    read_at: auto
    failure_type: auto
    failure_reason: auto
    metadata: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(MessageEdge)
class MessageEdgeType(AngeeNode):
    """GraphQL projection of a cross-message edge (quote/mention/crosspost/forward)."""

    kind: auto
    confidence: auto
    src: "MessageType | None" = actor_scoped_to_one("src")
    dst: "MessageType | None" = actor_scoped_to_one("dst")
    fragment: FragmentType | None
    created_at: auto


@strawberry_django.type(Reaction)
class ReactionType(AngeeNode):
    """GraphQL projection of an attributed reaction."""

    reaction: auto
    handle: "HandleType | None" = actor_scoped_to_one("handle")
    created_at: auto


@strawberry_django.type(MessageStar)
class MessageStarType(AngeeNode):
    """GraphQL projection of a user's starred message marker."""

    message: "MessageType | None" = actor_scoped_to_one("message")
    user: "UserType | None" = actor_scoped_to_one("user")
    created_at: auto


@strawberry.type
class MessageSubtypeOptionType:
    """Follower-selectable chatter subtype option."""

    key: str
    name: str
    description: str
    internal: bool = False
    default: bool = True
    sequence: int = 100


@strawberry.type
class SuggestedRecipientType:
    """One Odoo-style suggested recipient for a record chatter composer."""

    user: UserType
    reason: str
    source: str


@strawberry.input
class RecordReferenceInput:
    """Record identity for a model-backed chatter thread."""

    model_label: str = strawberry.field(name="model_label")
    record_id: strawberry.ID = strawberry.field(name="record_id")
    role: str = "chatter"


@strawberry.input
class RecordThreadInput(RecordReferenceInput):
    """Fields accepted when fetching a model-backed chatter thread."""

    search: str = ""
    message_limit: int = strawberry.field(name="message_limit", default=50)
    before: strawberry.ID | None = None
    after: strawberry.ID | None = None
    around: strawberry.ID | None = None
    message_types: list[str] = strawberry.field(name="message_types", default_factory=list)


@strawberry.enum
class RecordMessagePostKind(StrEnum):
    """Kinds of author-created record chatter."""

    COMMENT = "comment"
    NOTE = "note"


@strawberry.input
class RecordMessagePostInput(RecordReferenceInput):
    """Fields accepted when posting an internal chatter message."""

    body: str
    kind: RecordMessagePostKind = RecordMessagePostKind.COMMENT
    parent_message_id: strawberry.ID | None = strawberry.field(name="parent_message_id", default=None)
    attachment_ids: list[strawberry.ID] = strawberry.field(name="attachment_ids", default_factory=list)
    recipient_user_ids: list[strawberry.ID] = strawberry.field(name="recipient_user_ids", default_factory=list)
    autofollow_recipients: bool = strawberry.field(name="autofollow_recipients", default=False)


@strawberry.input
class RecordMessageUpdateInput(RecordReferenceInput):
    """Fields accepted when editing an existing chatter comment."""

    message_id: strawberry.ID = strawberry.field(name="message_id")
    body: str


@strawberry.input
class RecordMessageDeleteInput(RecordReferenceInput):
    """Fields accepted when deleting an existing chatter message."""

    message_id: strawberry.ID = strawberry.field(name="message_id")


@strawberry.input
class RecordMessageTrashInput(RecordReferenceInput):
    """Fields accepted when a moderator moves a chatter comment to the trash."""

    message_id: strawberry.ID = strawberry.field(name="message_id")
    confirm: bool
    reason: str = ""


@strawberry.input
class RecordMessageRestoreInput(RecordReferenceInput):
    """Fields accepted when a moderator restores a trashed chatter comment."""

    message_id: strawberry.ID = strawberry.field(name="message_id")
    confirm: bool


@strawberry.input
class RecordMessageReactionInput(RecordReferenceInput):
    """Fields accepted when reacting to a chatter message."""

    message_id: strawberry.ID = strawberry.field(name="message_id")
    reaction: str
    action: str = "toggle"


@strawberry.input
class RecordMessageStarInput(RecordReferenceInput):
    """Fields accepted when starring or unstarring a chatter message."""

    message_id: strawberry.ID = strawberry.field(name="message_id")
    starred: bool | None = None


@strawberry.input
class RecordMessageDoneInput(RecordReferenceInput):
    """Fields accepted when marking a chatter message done for the user."""

    message_id: strawberry.ID = strawberry.field(name="message_id")


@strawberry.input
class RecordFollowInput(RecordReferenceInput):
    """Fields accepted when following or unfollowing a record chatter thread."""

    following: bool
    notification_policy: str = "inbox"
    subtype_keys: list[str] = strawberry.field(default_factory=list)


@strawberry.type
class RecordErrorPayload:
    """The established record-chatter error envelope, preserved across commands."""

    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)

    @classmethod
    def from_error(cls, error: PermissionDenied | ValueError | ValidationError, *, invalid_code: str) -> Self:
        """Project one caught chatter error without changing its legacy envelope."""

        code = "PERMISSION_DENIED" if isinstance(error, PermissionDenied) else invalid_code
        if isinstance(error, ValidationError):
            code = getattr(error, "code", None) or code
        message = " ".join(error.messages) if isinstance(error, ValidationError) else str(error)
        return cls(error=message, error_code=code)


@strawberry.type
class RecordThreadStatePayload:
    """Refreshed thread state shared by reads and message write results."""

    thread: ThreadModel | None = strawberry.field(graphql_type=RecordThreadType | None, default=None)
    followers: list[RecordThreadFollowerType] = strawberry.field(default_factory=list)
    follower_count: int = strawberry.field(name="follower_count", default=0)
    is_following: bool = strawberry.field(name="is_following", default=False)
    notifications: list[RecordThreadNotificationType] = strawberry.field(default_factory=list)
    unread_count: int = strawberry.field(name="unread_count", default=0)
    needaction_count: int = strawberry.field(name="needaction_count", default=0)
    message_has_error: bool = strawberry.field(name="message_has_error", default=False)
    message_has_error_counter: int = strawberry.field(name="message_has_error_counter", default=0)
    attachment_count: int = strawberry.field(name="attachment_count", default=0)

    @classmethod
    def from_thread_state(cls, state: RecordThreadStatePayload, **overrides: Any) -> Self:
        """Shallowly project declared thread fields; preserve model/queryset values."""

        values = {field.name: getattr(state, field.name) for field in fields(RecordThreadStatePayload)}
        return cls(**(values | overrides))


@strawberry.enum
class RecordPostKind(StrEnum):
    """The two native record-thread composer acts."""

    COMMENT = "comment"
    NOTE = "note"


@strawberry.type
class RecordThreadPayload(RecordThreadStatePayload, RecordErrorPayload):
    """A record chatter thread, or the error that prevented resolving it."""

    # The record model is polymorphic: project its own posting permission name,
    # rather than assuming every threaded model uses "write".
    thread_post_access: str | None = strawberry.field(name="thread_post_access", default=None)
    permissions: list[str] = strawberry.field(default_factory=list)
    messages: list[RecordMessageType] = strawberry.field(default_factory=list)
    message_result_count: int = strawberry.field(name="message_result_count", default=0)
    _reply_search: strawberry.Private[str] = ""
    _reply_message_types: strawberry.Private[tuple[str, ...]] = ()
    audience_label: str | None = strawberry.field(name="audience_label", default=None)
    post_kinds: list[RecordPostKind] = strawberry.field(name="post_kinds", default_factory=list)
    self_follower: RecordThreadFollowerType | None = strawberry.field(name="self_follower", default=None)
    suggested_recipients: list[SuggestedRecipientType] = strawberry.field(
        name="suggested_recipients",
        default_factory=list,
    )
    subtypes: list[MessageSubtypeOptionType] = strawberry.field(default_factory=list)
    removed_message_count: int | None = strawberry.field(name="removed_message_count", default=None)
    """Trashed comments awaiting moderation; null for readers who do not moderate the record."""

    @strawberry.field(name="reply_count")
    def reply_count(self) -> int:
        """Count matching replies only when a caller selects this annotation."""

        if self.thread is None:
            return 0
        replies = Message.objects.sudo(reason="messaging.record_thread.reply_count").for_thread(self.thread)
        kinds = {
            strip_null_bytes(value or "").strip().lower()
            for value in self._reply_message_types
            if strip_null_bytes(value or "").strip()
        }
        if kinds:
            replies = replies.filter(message_type__in=kinds)
        for term in strip_null_bytes(self._reply_search or "").split():
            replies = replies.searching(term)
        return replies.untrashed().filter(parent__isnull=False).distinct().count()


@strawberry.type
class RecordMessagePostPayload(RecordThreadStatePayload, RecordErrorPayload):
    """A posted chatter message, or the error that prevented posting it."""

    message: RecordMessageType | None = None


@strawberry.type
class RecordMessageUpdatePayload(RecordThreadStatePayload, RecordErrorPayload):
    """An updated chatter message, or the error that prevented editing it."""

    message: RecordMessageType | None = None


@strawberry.type
class RecordMessageDeletePayload(RecordThreadStatePayload, RecordErrorPayload):
    """A deleted chatter message id plus refreshed thread state, or an error."""

    deleted_message_id: strawberry.ID | None = strawberry.field(name="deleted_message_id", default=None)
    messages: list[RecordMessageType] = strawberry.field(default_factory=list)
    message_result_count: int = strawberry.field(name="message_result_count", default=0)


@strawberry.type
class RecordMessageTrashPayload(RecordThreadStatePayload, RecordErrorPayload):
    """A trashed or restored chatter comment plus refreshed thread state, or an error."""

    message: RecordMessageType | None = None
    messages: list[RecordMessageType] = strawberry.field(default_factory=list)
    message_result_count: int = strawberry.field(name="message_result_count", default=0)
    removed_message_count: int | None = strawberry.field(name="removed_message_count", default=None)


@strawberry.type
class RecordRemovedMessagesPayload(RecordErrorPayload):
    """A record's trashed comments for its moderators, newest last, or an error."""

    messages: list[RecordMessageType] = strawberry.field(default_factory=list)
    message_result_count: int = strawberry.field(name="message_result_count", default=0)


@strawberry.type
class RecordMessageReactionPayload(RecordErrorPayload):
    """A reacted chatter message, or the error that prevented reacting."""

    message: RecordMessageType | None = None
    reaction_groups: list[RecordMessageReactionGroupType] = strawberry.field(
        name="reaction_groups",
        default_factory=list,
    )


@strawberry.type
class RecordMessageStarPayload(RecordErrorPayload):
    """A starred/unstarred chatter message result, or the error that prevented it."""

    message: RecordMessageType | None = None
    starred: bool = False


@strawberry.type
class RecordMessageDonePayload(RecordErrorPayload):
    """A message marked done for the user, or the error that prevented it."""

    message: RecordMessageType | None = None
    thread: ThreadModel | None = strawberry.field(graphql_type=RecordThreadType | None, default=None)
    notifications: list[RecordThreadNotificationType] = strawberry.field(default_factory=list)
    unread_count: int = strawberry.field(name="unread_count", default=0)
    needaction_count: int = strawberry.field(name="needaction_count", default=0)


@strawberry.type
class RecordFollowPayload(RecordErrorPayload):
    """A record follower update result, or the error that prevented it."""

    follower: RecordThreadFollowerType | None = None
    thread: ThreadModel | None = strawberry.field(graphql_type=RecordThreadType | None, default=None)
    followers: list[RecordThreadFollowerType] = strawberry.field(default_factory=list)
    follower_count: int = strawberry.field(name="follower_count", default=0)
    is_following: bool = strawberry.field(name="is_following", default=False)


@strawberry.type
class MessageFeedPage:
    """A newest-first, currently authorized message page with stable cursors."""

    messages: list[MessageType]
    count: int
    older_cursor: str | None
    newer_cursor: str | None
    has_older: bool
    has_newer: bool
    has_more_in_window: bool
    has_older_than_through: bool
    has_newer_than_before: bool

    @classmethod
    def from_scope(cls, queryset: MessageQuerySet, **options: Any) -> Self:
        """Project a domain-scoped message window without re-deciding its scope."""

        page = queryset.feed_page(**options)
        return cls(
            messages=cast(list[MessageType], page.rows),
            count=page.count,
            older_cursor=page.older_cursor,
            newer_cursor=page.newer_cursor,
            has_older=page.has_older,
            has_newer=page.has_newer,
            has_more_in_window=page.has_more_in_window,
            has_older_than_through=page.has_older_than_through,
            has_newer_than_before=page.has_newer_than_before,
        )


@strawberry.type
class MessageFeedRevalidation:
    """Complete survivor/absent partition under the current feed scope."""

    messages: list[MessageType]
    absent_ids: list[strawberry.ID]

    @classmethod
    def from_scope(
        cls, queryset: MessageQuerySet, ids: list[strawberry.ID], *, search: str = "", all_copies: bool = False,
        anchor: str = "",
    ) -> Self:
        """Revalidate the same readable domain scope using public message IDs."""

        return cls(**queryset.feed_revalidate(
            [str(value) for value in ids], search=search, all_copies=all_copies, anchor=anchor,
        ))


@strawberry.type
class MessageTextType:
    """Bounded single-message text, deliberately absent from shared list projections."""

    body_text: str


@strawberry.type
class MessagingQuery:
    """Record- and actor-scoped chatter queries."""

    @strawberry.field(name="message_text")
    def message_text(self, id: PublicID) -> MessageTextType | None:
        """Project one actor-readable message for the bounded MCP body read."""

        row = instance_for_id(Message, id, queryset=Message.objects.inbox().scoped())
        return MessageTextType(body_text=row.body_text(max_bytes=4096)) if row is not None else None

    @strawberry.field
    def thread_message_feed(
        self,
        thread_id: strawberry.ID,
        search: str = "",
        all_copies: bool = False,
        before_cursor: str | None = None,
        after_cursor: str | None = None,
        through_cursor: str | None = None,
        anchor: str = "",
        limit: int = 50,
    ) -> MessageFeedPage:
        """Page an inbox thread through the current actor's message scope."""

        thread = require_instance_for_id(
            Thread,
            thread_id,
            queryset=Thread.objects.all().scoped().inbox(),
            not_found="thread not found",
        )
        return MessageFeedPage.from_scope(
            Message.objects.inbox().untrashed().for_thread(thread),
            scope=("thread", str(thread.sqid)),
            search=search,
            all_copies=all_copies,
            before_cursor=before_cursor,
            after_cursor=after_cursor,
            through_cursor=through_cursor,
            anchor=anchor,
            limit=limit,
        )

    @strawberry.field
    def thread_message_feed_revalidate(
        self,
        thread_id: strawberry.ID,
        ids: list[strawberry.ID],
        search: str = "",
        all_copies: bool = False,
        anchor: str = "",
    ) -> MessageFeedRevalidation:
        """Revalidate loaded inbox messages through the current readable thread."""

        thread = require_instance_for_id(
            Thread,
            thread_id,
            queryset=Thread.objects.all().scoped().inbox(),
            not_found="thread not found",
        )
        return MessageFeedRevalidation.from_scope(
            Message.objects.inbox().untrashed().for_thread(thread), ids,
            search=search, all_copies=all_copies, anchor=anchor,
        )

    @strawberry.field(name="message_source_copies", graphql_type=list[MessageType])
    def message_source_copies(self, id: strawberry.ID) -> models.QuerySet[MessageModel]:
        """The readable channel occurrences of one message: its survivor and every copy folded into it."""

        rows = Message.objects.inbox().untrashed()
        survivor = require_instance_for_id(Message, id, queryset=rows).canonical()
        return rows.redirect_group(survivor).order_by("channel_id", "pk")

    @strawberry.field(name="record_thread")
    def record_thread(self, info: strawberry.Info, input: RecordThreadInput) -> RecordThreadPayload:
        """Return the existing chatter thread attached to a model record."""

        try:
            record = chatter_record(input.model_label, input.record_id)
        except ValueError as error:
            return RecordThreadPayload(error=str(error), error_code="BAD_RECORD")
        if record is None:
            return RecordThreadPayload(error="record not found", error_code="NOT_FOUND")
        return _record_thread_payload(
            record,
            info,
            role=input.role,
            search=input.search,
            message_limit=input.message_limit,
            before=input.before,
            after=input.after,
            around=input.around,
            message_types=tuple(input.message_types),
        )

    @strawberry.field(name="record_removed_messages")
    def record_removed_messages(self, info: strawberry.Info, input: RecordThreadInput) -> RecordRemovedMessagesPayload:
        """Return a record's trashed comments when the reader moderates the record."""

        if _request_user(info) is None:
            return RecordRemovedMessagesPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = chatter_record(input.model_label, input.record_id)
        except ValueError as error:
            return RecordRemovedMessagesPayload(error=str(error), error_code="BAD_RECORD")
        if record is None:
            return RecordRemovedMessagesPayload(error="record not found", error_code="NOT_FOUND")
        try:
            messages, count = cast(Any, record).message_removed(role=input.role, limit=input.message_limit)
        except PermissionDenied as error:
            return RecordRemovedMessagesPayload.from_error(error, invalid_code="BAD_MESSAGE")
        _prime_record_messages(messages)
        return RecordRemovedMessagesPayload(messages=messages, message_result_count=count)

    @strawberry.field(name="record_source_threads")
    def record_source_threads(self, input: RecordReferenceInput) -> list[ThreadAttachmentType]:
        """Return readable source conversations linked to one readable record."""

        record = _referenced_record(input)
        if record is None:
            return []
        edges = ThreadAttachment.objects.source_threads_for_record(record).order_by("-created_at", "pk")
        return list(with_record_reference_access(edges))

@strawberry.type
class MessagingMutation:
    """Inbox lifecycle verbs and record-backed chatter mutations."""

    @strawberry.mutation(name="identify_message_source_copy")
    def identify_message_source_copy(self, left: strawberry.ID, right: strawberry.ID, same: bool) -> bool:
        """Confirm or dismiss two occurrences as one message; the decision folds or splits the redirect."""

        rows = Message.objects.inbox().untrashed()
        first = require_instance_for_id(Message, left, queryset=rows)
        other = require_instance_for_id(Message, right, queryset=rows)
        first.identify_source_copy(other, same=same)
        return True

    @strawberry.mutation(name="acknowledge_message_delivery")
    @action_guard("Delivery acknowledgement refused.")
    def acknowledge_message_delivery(self, info: strawberry.Info, id: PublicID, expected_revision: int) -> ActionResult:
        """Operator confirmation without another provider effect."""

        row = authorized_permission_target(info, Message, id, "send_held")
        row.acknowledge_delivery(actor=_request_user(info), expected_revision=expected_revision)
        return ActionResult(ok=True, message="Delivery acknowledged as sent.")

    @strawberry.mutation(name="retry_message_delivery")
    @action_guard("Delivery retry refused.")
    def retry_message_delivery(self, info: strawberry.Info, id: PublicID, expected_revision: int) -> ActionResult:
        """Explicit retry of FAILED, or acceptance of repeat-effect risk for UNCERTAIN."""

        row = authorized_permission_target(info, Message, id, "send_held")
        row.retry_delivery(actor=_request_user(info), expected_revision=expected_revision)
        return ActionResult(ok=True, message="Delivery retry queued.")

    @strawberry.mutation(name="send_held_drafts")
    def send_held_drafts(self, info: strawberry.Info, selection: list[ActionSelectionInput]) -> list[ActionResult]:
        """Operator send-now, preserving each observed revision and refusal."""

        return Message.objects.send_held_drafts(selection, actor=_request_user(info))

    @strawberry.mutation(name="discard_held_drafts")
    def discard_held_drafts(self, info: strawberry.Info, selection: list[ActionSelectionInput]) -> list[ActionResult]:
        """Operator discard through the shared trash owner."""

        return Message.objects.discard_held_drafts(selection, actor=_request_user(info))

    @strawberry.mutation(name="mark_thread_notification_read")
    def mark_thread_notification_read(self, info: strawberry.Info, id: strawberry.ID) -> ThreadNotificationType:
        """Acknowledge one item from the recipient-scoped notification resource."""

        actor = _request_user(info)
        if actor is None:
            raise PermissionDenied("Authentication required.")
        notification = authorized_permission_target(info, ThreadNotification, id, "read_inbox")
        return cast(ThreadNotificationType, ThreadNotification.objects.mark_read(notification, actor))

    @strawberry.mutation
    def set_inbox_message_starred(self, info: strawberry.Info, id: strawberry.ID, starred: bool) -> MessageType:
        """Set the viewer's star after authorizing the personal-message read."""

        user = _request_user(info)
        if user is None:
            raise PermissionDenied("authentication required")
        message = Message.objects.all().explorer().message(str(id))
        apps.get_model("messaging", "MessageStar").objects.set_starred(message, user=user, starred=starred)
        return cast(MessageType, message)

    @strawberry.mutation(name="post_record_message")
    def post_record_message(
        self,
        info: strawberry.Info,
        input: RecordMessagePostInput,
        idempotency_key: str | None = None,
    ) -> RecordMessagePostPayload:
        """Post an internal comment to the record's chatter thread."""

        try:
            record = chatter_record(input.model_label, input.record_id)
        except ValueError as error:
            return RecordMessagePostPayload(error=str(error), error_code="BAD_RECORD")
        if record is None:
            return RecordMessagePostPayload(error="record not found", error_code="NOT_FOUND")
        try:
            attachments = _storage_files(input.attachment_ids)
            recipient_user_ids = tuple(user.pk for user in _users_from_public_ids(input.recipient_user_ids))
            parent = _message(input.parent_message_id) if input.parent_message_id is not None else None
            if input.kind is RecordMessagePostKind.NOTE:
                if recipient_user_ids or input.autofollow_recipients:
                    raise ValueError("Internal notes cannot target recipients.")
                message = cast(Any, record).message_log(
                    input.body, attachments=attachments, parent=parent, idempotency_key=idempotency_key
                )
            else:
                message = cast(Any, record).message_post(
                    input.body,
                    attachments=attachments,
                    recipient_user_ids=recipient_user_ids,
                    autofollow_recipients=input.autofollow_recipients,
                    parent=parent,
                    idempotency_key=idempotency_key,
                )
        except (PermissionDenied, ValueError, ValidationError) as error:
            return RecordMessagePostPayload.from_error(error, invalid_code="BAD_MESSAGE")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordMessagePostPayload.from_thread_state(
            payload,
            message=message,
            thread=payload.thread,
        )

    @strawberry.mutation(name="update_record_message")
    def update_record_message(
        self,
        info: strawberry.Info,
        input: RecordMessageUpdateInput,
    ) -> RecordMessageUpdatePayload:
        """Update an existing internal comment in the record's chatter thread."""

        if _request_user(info) is None:
            return RecordMessageUpdatePayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = chatter_record(input.model_label, input.record_id)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageUpdatePayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageUpdatePayload(error="record not found", error_code="NOT_FOUND")
        try:
            message = cast(Any, record).message_update_content(message, body=input.body)
        except (PermissionDenied, ValueError) as error:
            return RecordMessageUpdatePayload.from_error(error, invalid_code="BAD_MESSAGE")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordMessageUpdatePayload.from_thread_state(
            payload,
            message=message,
            thread=payload.thread,
        )

    @strawberry.mutation(name="delete_record_message")
    def delete_record_message(
        self,
        info: strawberry.Info,
        input: RecordMessageDeleteInput,
    ) -> RecordMessageDeletePayload:
        """Delete a message from the record's chatter thread."""

        if _request_user(info) is None:
            return RecordMessageDeletePayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = chatter_record(input.model_label, input.record_id)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageDeletePayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageDeletePayload(error="record not found", error_code="NOT_FOUND")
        deleted_message_id = input.message_id
        try:
            thread = cast(Any, record).message_unlink(message)
        except (PermissionDenied, ValueError) as error:
            return RecordMessageDeletePayload.from_error(error, invalid_code="BAD_MESSAGE")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordMessageDeletePayload.from_thread_state(
            payload,
            deleted_message_id=deleted_message_id,
            thread=thread,
            messages=payload.messages,
            message_result_count=payload.message_result_count,
        )

    @strawberry.mutation(name="trash_record_message")
    def trash_record_message(self, info: strawberry.Info, input: RecordMessageTrashInput) -> RecordMessageTrashPayload:
        """Move a chatter comment to the trash as a moderator of its record."""

        return _moderate_record_message(info, input)

    @strawberry.mutation(name="restore_record_message")
    def restore_record_message(
        self,
        info: strawberry.Info,
        input: RecordMessageRestoreInput,
    ) -> RecordMessageTrashPayload:
        """Restore a trashed chatter comment as a moderator of its record."""

        return _moderate_record_message(info, input)

    @strawberry.mutation(name="set_record_message_reaction")
    def set_record_message_reaction(
        self,
        info: strawberry.Info,
        input: RecordMessageReactionInput,
    ) -> RecordMessageReactionPayload:
        """Add, remove, or toggle the current user's reaction on a chatter message."""

        user = _request_user(info)
        if user is None:
            return RecordMessageReactionPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = chatter_record(input.model_label, input.record_id)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageReactionPayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageReactionPayload(error="record not found", error_code="NOT_FOUND")
        try:
            message = cast(Any, record).message_reaction(
                message, reaction=input.reaction, action=input.action, user=user
            )
        except (PermissionDenied, ValueError) as error:
            return RecordMessageReactionPayload.from_error(error, invalid_code="BAD_REACTION")
        return RecordMessageReactionPayload(
            message=message,
            reaction_groups=_record_message_reaction_groups(message, user),
        )

    @strawberry.mutation(name="set_record_message_starred")
    def set_record_message_starred(
        self,
        info: strawberry.Info,
        input: RecordMessageStarInput,
    ) -> RecordMessageStarPayload:
        """Set or toggle the current user's star on a chatter message."""

        user = _request_user(info)
        if user is None:
            return RecordMessageStarPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = chatter_record(input.model_label, input.record_id)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageStarPayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageStarPayload(error="record not found", error_code="NOT_FOUND")
        try:
            starred = cast(Any, record).message_set_starred(message, user=user, starred=input.starred)
        except (PermissionDenied, ValueError) as error:
            return RecordMessageStarPayload.from_error(error, invalid_code="BAD_MESSAGE")
        return RecordMessageStarPayload(message=message, starred=starred)

    @strawberry.mutation(name="mark_record_message_done")
    def mark_record_message_done(
        self,
        info: strawberry.Info,
        input: RecordMessageDoneInput,
    ) -> RecordMessageDonePayload:
        """Mark one chatter message done for the current user."""

        user = _request_user(info)
        if user is None:
            return RecordMessageDonePayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = chatter_record(input.model_label, input.record_id)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageDonePayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageDonePayload(error="record not found", error_code="NOT_FOUND")
        try:
            cast(Any, record).message_set_done(message, user=user)
        except (PermissionDenied, ValueError) as error:
            return RecordMessageDonePayload.from_error(error, invalid_code="BAD_MESSAGE")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordMessageDonePayload(
            message=next(
                (candidate for candidate in payload.messages if cast(Any, candidate).pk == message.pk),
                message,
            ),
            thread=payload.thread,
            notifications=payload.notifications,
            unread_count=payload.unread_count,
            needaction_count=payload.needaction_count,
        )

    @strawberry.mutation(name="set_record_following")
    def set_record_following(self, info: strawberry.Info, input: RecordFollowInput) -> RecordFollowPayload:
        """Follow or unfollow a record's chatter thread as the current user."""

        user = _request_user(info)
        if user is None:
            return RecordFollowPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = chatter_record(input.model_label, input.record_id)
        except ValueError as error:
            return RecordFollowPayload(error=str(error), error_code="BAD_RECORD")
        if record is None:
            return RecordFollowPayload(error="record not found", error_code="NOT_FOUND")
        try:
            if input.following:
                follower = cast(Any, record).message_subscribe(
                    user=user,
                    notification_policy=input.notification_policy,
                    subtype_keys=tuple(input.subtype_keys),
                )
            else:
                cast(Any, record).message_unsubscribe(user=user)
                follower = None
        except (ValueError, PermissionDenied) as error:
            return RecordFollowPayload(error=str(error), error_code="BAD_FOLLOWER")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordFollowPayload(
            follower=follower,
            thread=payload.thread,
            followers=payload.followers,
            follower_count=payload.follower_count,
            is_following=payload.is_following,
        )

    @strawberry.mutation(name="mark_record_thread_read")
    def mark_record_thread_read(self, info: strawberry.Info, input: RecordReferenceInput) -> RecordThreadPayload:
        """Advance the current follower's record cursor without acknowledging inbox items."""

        user = _request_user(info)
        if user is None:
            return RecordThreadPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = chatter_record(input.model_label, input.record_id)
        except ValueError as error:
            return RecordThreadPayload(error=str(error), error_code="BAD_RECORD")
        if record is None:
            return RecordThreadPayload(error="record not found", error_code="NOT_FOUND")
        ThreadFollower.objects.mark_read_for_record(record, user=user, role=input.role)
        return _record_thread_payload(record, info, role=input.role)


def _thread_inbox_queryset(info: strawberry.Info) -> Any:
    """Scope the generic ``threads`` resource to non-record (channel) threads.

    Record-attached chatter is reachable only through ``record_thread`` — the parent
    record's read is the one gate — so it is excluded from this owner-scoped inbox
    list, aggregate, and by-pk lookup.
    """

    del info
    return Thread.objects.inbox()


def _message_inbox_queryset(info: strawberry.Info) -> Any:
    """Scope the generic ``messages`` resource to non-record (channel) messages.

    Record chatter surfaces only through ``record_thread`` (gated on the parent
    record's read); it is excluded from this owner-scoped inbox list, aggregate, and
    by-pk lookup.
    """

    # The title annotation serves list rows in SQL; Message.title() prefers it,
    # so a title column on the grid costs no per-row probe.
    del info
    return Message.objects.inbox().with_title_text()


def _part_inbox_queryset(info: strawberry.Info) -> Any:
    """Scope the generic ``parts`` resource to inbox messages' parts.

    The same record-chatter exclusion the ``messages`` resource applies, mirrored
    onto its parts so the structural data view cannot reach chatter content the
    generic surface deliberately hides.
    """

    del info
    return Part.objects.inbox()


class _InboxWriteBackend(AngeeHasuraWriteBackend):
    """Write backend that keeps record-attached chatter off the generic mutations.

    The read side scopes ``threads``/``messages`` to ``.inbox()`` through
    ``get_queryset``; the write side must match, or a creator who lost record access
    could still reach a record-attached row through ``update_<res>_by_pk`` /
    ``delete_<res>_by_pk`` through the thread's ownership or channel access.
    Narrowing the write-target queryset to ``.inbox()`` makes the by-pk lookup miss
    a record-attached row, so the generic mutation reports it as not found while the
    inbox rows keep their update/delete surfaces. The ``.inbox()`` verb resolves
    polymorphically on each model's queryset, so one backend serves both.
    """

    def write_target_queryset(self) -> Any:
        """Scope the update/delete/save target queryset to non-record rows."""

        queryset = super().write_target_queryset()
        if not isinstance(queryset, (MessageQuerySet, ThreadQuerySet)):
            raise ImproperlyConfigured("The inbox write backend requires a message or thread queryset.")
        return queryset.inbox()


class _ChannelWriteBackend(AngeeHasuraWriteBackend):
    """Make the generic ``delete_channels_by_pk`` a purge, not an orphan.

    ``delete=True`` on the channel resource is what lights the console's delete button
    (the generic views gate on the presence of the delete root). Its default backend
    would ``SET_NULL`` the channel's threads/messages — the shared-Integration-parent
    orphan this whole feature exists to prevent — so the auto path routes through the
    same :meth:`ChannelManager.purge` as the authored ``delete_channel`` preview mutation
    the console actually drives; both are thin dispatchers to that one owner.
    """

    def delete(self, info: strawberry.Info, pk: str) -> Any:
        """Purge, then delete, one public-id-addressed channel; return the deleted row."""

        del info
        queryset = self.write_target_queryset()
        if not isinstance(queryset, ChannelQuerySet):
            raise ImproperlyConfigured("The channel write backend requires a channel queryset.")
        channel = require_instance_for_id(Channel, str(pk), queryset=queryset.of_concrete_type())
        Channel.objects.purge(channel)
        return channel


_CHANNEL_RESOURCE = hasura_model_resource(
    ChannelType,
    model=Channel,
    name="channels",
    get_queryset=lambda info: Channel.objects.of_concrete_type(),
    filterable=[
        "id",
        "display_name",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "sync_stage",
        "last_sync_completed_at",
        "updated_at",
        *_CHANNEL_EXTENSION_FILTER_FIELDS,
    ],
    sortable=[
        "display_name",
        "backend_class",
        "lifecycle",
        "runtime_status",
        "last_sync_completed_at",
        "updated_at",
        *_CHANNEL_EXTENSION_ORDER_FIELDS,
    ],
    aggregatable=["id", "last_sync_items", *_CHANNEL_EXTENSION_AGGREGATE_FIELDS],
    groupable=[
        "backend_class",
        "lifecycle",
        "runtime_status",
        "sync_stage",
        *_CHANNEL_EXTENSION_GROUP_FIELDS,
    ],
    insert=False,
    # The operator label is the one channel fact a human owns; everything else on
    # the row is runtime truth a session writes. Blank falls back to the
    # vendor-derived `Integration.display_label`, so clearing it is a real intent.
    updatable=["display_name", *_CHANNEL_EXTENSION_UPDATE_FIELDS],
    # Deleting a channel purges everything it ingested (see `delete_channel` /
    # `_ChannelWriteBackend`); `delete=True` lights the console's delete button.
    delete=True,
    write_backend=_ChannelWriteBackend(
        Channel,
        public_id_fields=_CHANNEL_EXTENSION_PUBLIC_ID_FIELDS,
    ),
)
_MESSAGE_RESOURCE = hasura_model_resource(
    MessageType,
    model=Message,
    name="messages",
    filterable=[
        "id",
        "status",
        "message_type",
        "subtype",
        "platform",
        "direction",
        "thread",
        "channel",
        "sender",
        "sent_at",
        "is_trashed",
        "scheduled_at",
        *declared_hasura_resource_fields(Message, "hasura_filterable_fields"),
        # The transcript's keyset "load older" cursors on (sent_at, created_at).
        "created_at",
    ],
    sortable=[
        "sent_at",
        "received_at",
        "created_at",
        "id",
        "title",
        "sender_name",
        "thread_title",
        "channel_vendor_name",
        "status",
    ],
    sortable_aliases={
        "title": "_title_text",
        "sender_name": SortAlias(
            "_sender_name", lambda _info, queryset: cast(MessageQuerySet, queryset).sender_name_expression()
        ),
        "thread_title": SortAlias(
            "_thread_title", lambda _info, queryset: cast(MessageQuerySet, queryset).thread_title_expression()
        ),
        "channel_vendor_name": SortAlias(
            "_channel_vendor_name",
            lambda _info, queryset: cast(MessageQuerySet, queryset).channel_vendor_name_expression(),
        ),
    },
    aggregatable=["id"],
    groupable=[
        "thread",
        "thread__title__text",
        "sender",
        "sender__display_name",
        "channel",
        "channel__display_name",
        "status",
        "message_type",
        "subtype",
        "subtype__key",
        "platform",
        "metadata.mailbox",
        "sent_at",
    ],
    json_paths={"metadata.mailbox": "str"},
    insert=False,
    update=False,
    field_id_decode={
        "thread": public_pk_decoder(Thread),
        "channel": public_pk_decoder(Integration),
        "sender": public_pk_decoder(Handle),
        "subtype": public_pk_decoder(MessageSubtype),
    },
    get_queryset=_message_inbox_queryset,
    write_backend=_InboxWriteBackend(Message),
)
_THREAD_RESOURCE = hasura_model_resource(
    ThreadType,
    model=Thread,
    name="threads",
    filterable=["id", "platform", "modality", "visibility", "channel", "last_message_at"],
    sortable=["last_message_at", "message_count", "created_at"],
    aggregatable=["id", "message_count"],
    groupable=["channel", "channel__display_name", "modality", "visibility", "last_message_at"],
    insert=False,
    updatable=["visibility"],
    field_id_decode={"channel": public_pk_decoder(Integration)},
    get_queryset=_thread_inbox_queryset,
    write_backend=_InboxWriteBackend(Thread),
)


_PART_RESOURCE = hasura_model_resource(
    PartType,
    model=Part,
    name="parts",
    filterable=["id", "message", "parent", "role", "type", "disposition", "fragment"],
    sortable=["position", "created_at"],
    aggregatable=["id"],
    groupable=[
        "role",
        "type",
        "disposition",
        "message",
        # Grouping by the shared fragment IS the interconnection view: every
        # group with more than one row is text deduplicated across messages.
        "fragment",
        "fragment__hash",
    ],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={
        "message": public_pk_decoder(Message),
        "parent": public_pk_decoder(Part),
        "fragment": public_pk_decoder(Fragment),
    },
    get_queryset=_part_inbox_queryset,
)

# Read-only collections expose canonical model metadata and live interests for
# authored explorer reads; ingest and relation producers remain the write owners.
_PARTICIPANT_RESOURCE = hasura_model_resource(
    ParticipantType,
    model=Participant,
    name="participants",
    filterable=["id", "message", "thread", "handle", "role"],
    sortable=["created_at"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={
        "message": public_pk_decoder(Message),
        "thread": public_pk_decoder(Thread),
        "handle": public_pk_decoder(Handle),
    },
)
_MESSAGE_EDGE_RESOURCE = hasura_model_resource(
    MessageEdgeType,
    model=MessageEdge,
    name="message_edges",
    filterable=["id", "src", "dst", "kind", "fragment"],
    sortable=["created_at"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={
        "src": public_pk_decoder(Message),
        "dst": public_pk_decoder(Message),
        "fragment": public_pk_decoder(Fragment),
    },
)


def _notification_inbox_queryset(info: strawberry.Info) -> Any:
    """Pin every generated inbox read, including by-pk and aggregates, to its recipient.

    A notification about a trashed message leaves the inbox with its message.
    """

    del info
    return (
        ThreadNotification.objects.with_action("read_inbox")
        .exclude(message__is_trashed=True)
        .with_message_pointers()
    )


_NOTIFICATION_INBOX_RESOURCE = hasura_model_resource(
    ThreadNotificationType,
    model=ThreadNotification,
    name="thread_notifications",
    get_queryset=_notification_inbox_queryset,
    filterable=["id", "notification_type", "notification_status", "read_at", "created_at"],
    sortable=["created_at", "read_at"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)


_RESOURCE_TYPES = [
    *_CHANNEL_RESOURCE.types,
    *_MESSAGE_RESOURCE.types,
    *_THREAD_RESOURCE.types,
    *_PART_RESOURCE.types,
    *_PARTICIPANT_RESOURCE.types,
    *_MESSAGE_EDGE_RESOURCE.types,
    *_NOTIFICATION_INBOX_RESOURCE.types,
]


_MESSAGING_SCHEMA_BUCKET = {
    "query": [
        MessagingPairingQuery,
        MessagingQuery,
        _CHANNEL_RESOURCE.query,
        _MESSAGE_RESOURCE.query,
        _THREAD_RESOURCE.query,
        _PART_RESOURCE.query,
        _PARTICIPANT_RESOURCE.query,
        _MESSAGE_EDGE_RESOURCE.query,
        _NOTIFICATION_INBOX_RESOURCE.query,
    ],
    "mutation": [
        MessagingPairingMutation,
        MessagingChannelMutation,
        MessagingMutation,
        _CHANNEL_RESOURCE.mutation,
        _MESSAGE_RESOURCE.mutation,
        _THREAD_RESOURCE.mutation,
        _PART_RESOURCE.mutation,
    ],
    "types": [
        ChannelType,
        PairingProjection,
        ThreadType,
        ThreadAttachmentType,
        ThreadFollowerType,
        RecordPointerType,
        ThreadNotificationType,
        MessageSubtypeType,
        MessageType,
        PartType,
        FragmentType,
        MessageEdgeType,
        ParticipantType,
        ReactionType,
        MessageStarType,
        MessageSubtypeOptionType,
        SuggestedRecipientType,
        MessageReactionGroupType,
        RecordHandleType,
        RecordMessageReactionGroupType,
        RecordMessageParentType,
        RecordMessageType,
        RecordThreadType,
        RecordThreadFollowerType,
        RecordThreadNotificationType,
        RecordThreadPayload,
        RecordMessagePostPayload,
        RecordMessageUpdatePayload,
        RecordMessageDeletePayload,
        RecordMessageReactionPayload,
        RecordMessageStarPayload,
        RecordMessageDonePayload,
        RecordFollowPayload,
        *_RESOURCE_TYPES,
    ],
    "type_extensions": ([] if _CHANNEL_WEBFORM_EXTENSION is None else [_CHANNEL_WEBFORM_EXTENSION]),
}


schemas = {
    "console": {
        **_MESSAGING_SCHEMA_BUCKET,
        "input_extensions": [*tags_input_extensions(_MESSAGE_RESOURCE), *tags_input_extensions(_THREAD_RESOURCE)],
        "subscription": [
            changes(Channel, field="channelChanged"),
            changes(Message, field="messageChanged"),
            changes(Thread, field="threadChanged"),
            changes(ThreadNotification, field="threadNotificationChanged"),
            changes(MessageStar, field="messageStarChanged"),
            changes(Part, field="partChanged"),
            changes(Participant, field="participantChanged"),
            changes(MessageEdge, field="messageEdgeChanged"),
        ],
    },
}


def _referenced_record(input: RecordReferenceInput) -> Any | None:
    """Return any readable model record addressed by a source-link request."""

    try:
        model = apps.get_model(input.model_label)
    except (LookupError, ValueError) as error:
        raise ValueError(f"Unknown model {input.model_label!r}.") from error
    try:
        record = instance_from_public_id(model, str(input.record_id))
    except ImproperlyConfigured as error:
        raise ValueError(str(error)) from error
    return None if record is None else _readable_record(record)


def _message_reaction_groups(message: Any, user: Any | None) -> list[MessageReactionGroupType]:
    """Project a message's model-owned reaction groups onto the GraphQL type.

    The grouping/count/self-state logic is owned by ``Message.reaction_groups``; this
    resolver only maps that domain shape to the GraphQL projection.
    """

    return [
        MessageReactionGroupType(
            reaction=group.reaction,
            count=group.count,
            self_reacted=group.self_reacted,
            handles=list(group.handles),
        )
        for group in message.reaction_groups(user)
    ]


def _record_message_reaction_groups(message: Any, user: Any | None) -> list[RecordMessageReactionGroupType]:
    """Project model-owned reaction groups without generic handle backedges."""

    return [
        RecordMessageReactionGroupType(
            reaction=group.reaction,
            count=group.count,
            self_reacted=group.self_reacted,
            handles=list(group.handles),
        )
        for group in message.reaction_groups(user)
    ]


def _record_message_access_cache(info: strawberry.Info | None) -> dict[Any, tuple[bool, bool]]:
    """Memoize record post and moderation permissions once per thread and request."""

    request = get_request(info) if info is not None else None
    if request is None:
        return {}
    cache = getattr(request, "_messaging_record_access", None)
    if cache is None:
        cache = {}
        setattr(request, "_messaging_record_access", cache)
    return cache


def _record_message_access(record: ThreadedModelMixin, user: Any) -> tuple[bool, bool]:
    """Compose record predicates with the shared permission evaluator."""

    if user is not None and not user.is_authenticated:
        return False, False
    if not model_resource_type(record):
        return record.can_post(user), record.can_moderate(user)
    permissions = held_permissions(record, (record.thread_post_access, "write"))
    return record.thread_post_access in permissions, "write" in permissions


def _message_access(message: Any, info: strawberry.Info | None) -> dict[str, Any]:
    """Pass the actor and the record's access facts to Message's predicates."""

    user = _request_user(info)
    post_access, moderate_access = False, False
    if user is not None and message.thread_id is not None:
        cache = _record_message_access_cache(info)
        if message.thread_id not in cache:
            record = message.threaded_record()
            cache[message.thread_id] = _record_message_access(record, user) if record is not None else (False, False)
        post_access, moderate_access = cache[message.thread_id]
    return {"post_access": post_access, "moderate_access": moderate_access, "actor_id": getattr(user, "pk", None)}


def _prime_follower_identities(followers: list[Any], actor: Any) -> None:
    """Batch readable identities into Django's relation caches for the shared guards."""

    if not followers or actor is None:
        return
    party_model = apps.get_model("parties", "Party")
    person_model = apps.get_model("parties", "Person")
    parties = {
        row.pk: row for row in party_model.objects.with_actor(actor).filter(pk__in={f.party_id for f in followers})
    }
    people = {row.pk: row for row in person_model.objects.with_actor(actor).filter(pk__in=parties)}
    users = {
        row.pk: row
        for row in get_user_model()
        .objects.with_actor(actor)
        .filter(pk__in={p.user_id for p in people.values() if p.user_id})
    }
    for person in people.values():
        person._meta.get_field("user").set_cached_value(
            person, users.get(person.user_id) if person.user_id is not None else None
        )
    for party in parties.values():
        party._meta.get_field("person").set_cached_value(party, people.get(party.pk))
    for follower in followers:
        follower._meta.get_field("party").set_cached_value(follower, parties.get(follower.party_id))


def _moderate_record_message(
    info: strawberry.Info,
    input: RecordMessageTrashInput | RecordMessageRestoreInput,
) -> RecordMessageTrashPayload:
    """Dispatch one confirmed trash or restore to the record's moderation verbs.

    The record's read gate authorizes the message lookup, as for the transcript.
    """

    if _request_user(info) is None:
        return RecordMessageTrashPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
    if not input.confirm:
        return RecordMessageTrashPayload(error="Confirm the change to apply it.", error_code="CONFIRMATION_REQUIRED")
    try:
        record = chatter_record(input.model_label, input.record_id)
    except ValueError as error:
        return RecordMessageTrashPayload(error=str(error), error_code="BAD_MESSAGE")
    if record is None:
        return RecordMessageTrashPayload(error="record not found", error_code="NOT_FOUND")
    try:
        if isinstance(input, RecordMessageTrashInput):
            message = cast(Any, record).record_message(str(input.message_id), role=input.role)
            message = cast(Any, record).message_trash(message, reason=input.reason)
        else:
            message = cast(Any, record).record_message(str(input.message_id), role=input.role, trashed=True)
            message = cast(Any, record).message_restore(message)
    except (PermissionDenied, ValueError, ValidationError) as error:
        return RecordMessageTrashPayload.from_error(error, invalid_code="BAD_MESSAGE")
    payload = _record_thread_payload(record, info, role=input.role)
    return RecordMessageTrashPayload.from_thread_state(
        payload,
        message=message,
        messages=payload.messages,
        message_result_count=payload.message_result_count,
        removed_message_count=payload.removed_message_count,
    )


def _prime_record_messages(messages: list[Any]) -> None:
    """Project every page message's actor-readable sender label in one query.

    Keep the projection on each message so ``author_label`` never performs a
    per-row fallback lookup.
    """

    if not messages:
        return
    owner = Message.objects.db_manager(messages[0]._state.db)
    sender_names = dict(
        owner.sudo(reason="messaging.record_thread.sender_labels")
        .filter(pk__in=[message.pk for message in messages])
        .annotate(_sender_name=owner.sender_name_expression())
        .values_list("pk", "_sender_name")
    )
    for message in messages:
        setattr(message, "_sender_name", sender_names.get(message.pk, ""))


def _record_thread_payload(
    record: Any,
    info: strawberry.Info | None,
    *,
    role: str = "chatter",
    search: str = "",
    message_limit: int = 50,
    before: strawberry.ID | None = None,
    after: strawberry.ID | None = None,
    around: strawberry.ID | None = None,
    message_types: tuple[str, ...] = (),
) -> RecordThreadPayload:
    """Return a record thread payload with follower state for the request user."""

    attachment = ThreadAttachment.objects.for_record(record, role=role)
    thread = attachment.thread if attachment is not None else None
    messages, message_result_count = (
        Message.objects.for_record(
            record,
            role=role,
            search=search,
            limit=message_limit,
            before=before,
            after=after,
            around=around,
            message_types=message_types,
        )
        if thread is not None
        else ([], 0)
    )
    _prime_record_messages(messages)
    followers = list(cast(Any, record).message_followers()) if thread is not None else []
    _prime_follower_identities(followers, current_actor())
    attachment_count = (
        apps.get_model("messaging", "Part")
        .objects.using(thread._state.db)
        .filter(message__thread_id__in=thread.member_threads().values("pk"), message__is_trashed=False)
        .attachments()
        .count()
        if thread is not None
        else 0
    )
    user = _request_user(info) if info is not None else None
    user_id = getattr(user, "pk", None)
    suggested_recipients = [
        SuggestedRecipientType(
            user=suggestion["user"],
            reason=str(suggestion["reason"]),
            source=str(suggestion["source"]),
        )
        for suggestion in cast(Any, record).message_suggested_recipients(
            role=role,
            user=user,
        )
    ]
    self_party_id = ThreadFollower.objects.get_party_id(user=user) if user_id is not None else None
    self_follower = next((follower for follower in followers if follower.party_id == self_party_id), None)
    is_following = self_follower is not None
    notifications = (
        list(
            ThreadNotification.objects.for_record(record, user=user, role=role).select_related(
                "thread", "attachment", "follower", "message", "message__subtype", "user"
            )[:50]
        )
        if thread is not None and user is not None
        else []
    )
    unread_count = (
        ThreadFollower.objects.unread_count_for_record(record, user=user, role=role)
        if thread is not None and user is not None
        else 0
    )
    record_access = _record_message_access(record, user)
    if messages:
        # Posting and moderation are record-level facts shared by the whole page.
        # Prime both through their owner instead of re-walking each message's record.
        _record_message_access_cache(info)[messages[0].thread_id] = record_access
    if messages and thread is not None and user is not None:
        # One receipt-anchored scan primes the page's needaction flags — the same
        # unread set the badge counts, restricted to the rows on this page.
        needaction_message_ids = set(
            ThreadFollower.objects.unread_messages(thread, user=user)
            .filter(pk__in=[message.pk for message in messages])
            .values_list("pk", flat=True)
        )
        for message in messages:
            setattr(message, "_current_user_needaction", message.pk in needaction_message_ids)
    message_has_error_counter = (
        ThreadNotification.objects.error_count_for_record(record, user=user, role=role)
        if thread is not None and user is not None
        else 0
    )
    removed_message_count = None
    if record_access[1]:
        removed_message_count = (
            Message.objects.sudo(reason="messaging.record_thread.removed_count").for_thread(thread).trashed().count()
            if thread is not None
            else 0
        )
    return RecordThreadPayload(
        thread_post_access=record.thread_post_access,
        permissions=[record.thread_post_access] if record_access[0] else [],
        thread=thread,
        messages=messages,
        message_result_count=message_result_count,
        _reply_search=search,
        _reply_message_types=message_types,
        post_kinds=[RecordPostKind.COMMENT, RecordPostKind.NOTE] if record_access[0] else [],
        followers=followers,
        self_follower=self_follower,
        suggested_recipients=suggested_recipients,
        subtypes=[MessageSubtypeOptionType(**option) for option in message_subtype_options(record._meta.label)],
        follower_count=len(followers),
        is_following=is_following,
        notifications=notifications,
        unread_count=unread_count,
        needaction_count=unread_count,
        message_has_error=message_has_error_counter > 0,
        message_has_error_counter=message_has_error_counter,
        attachment_count=attachment_count,
        removed_message_count=removed_message_count,
    )


def _request_user(info: strawberry.Info | None) -> Any | None:
    """Return the authenticated request user, if there is one."""

    if info is None:
        return None
    request = get_request(info)
    user = getattr(request, "user", None)
    if user is None or getattr(user, "is_authenticated", False) is False:
        return None
    return user


def _users_from_public_ids(user_ids: list[strawberry.ID]) -> tuple[Any, ...]:
    """Return users addressed by public id for direct chatter recipients."""

    try:
        return tuple(
            require_instance_for_id(
                get_user_model(),
                user_id,
                queryset=get_user_model()._default_manager.all(),
                not_found="recipient user not found",
            )
            for user_id in user_ids
        )
    except ImproperlyConfigured as error:
        raise ValueError(str(error)) from error


def _readable_record(record: Any) -> Any | None:
    """Return ``record`` re-resolved under the request actor's read scope, else None.

    The ``record_thread`` gate: record chatter — its thread and messages — is
    reachable only when the actor can read the parent record, so a row reached
    elevated is authorized *through* the record's read. Re-resolving on the actor-scoped default manager (the exact
    scope :func:`threaded_record` uses) yields ``None`` when the record is unreadable,
    which the caller surfaces as ``NOT_FOUND``.
    """

    return type(record)._default_manager.filter(pk=record.pk).first()


def _message(message_id: strawberry.ID) -> Any:
    """Return a message by public id."""

    try:
        return require_instance_for_id(
            Message, message_id, queryset=Message.objects.all(), not_found="message not found"
        )
    except ImproperlyConfigured as error:
        raise ValueError(str(error)) from error


def _storage_files(file_ids: list[strawberry.ID]) -> tuple[Any, ...]:
    """Return readable storage files addressed by public id."""

    files = []
    for file_id in file_ids:
        file = require_instance_for_id(
            File,
            file_id,
            queryset=File.objects.all(),
            not_found="attachment not found",
        )
        files.append(file)
    return tuple(files)
