"""GraphQL schema for the messaging addon — threads, messages, and the inbox.

Messages arrive through channel sync and the manager-owned ingest path; the
console browses and moderates them through Hasura resources. Parts,
participants, edges, and reactions remain nested read projections reached
through their message/thread owners.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from rebac import PermissionDenied
from strawberry import auto

from angee.base.models import instance_from_public_id
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import request_from_info
from angee.iam.schema import UserType
from angee.integrate.schema import BridgeSyncStatusMixin, IntegrationLabelMixin, IntegrationType
from angee.messaging.managers import message_subtype_options
from angee.messaging.models import ThreadedModelMixin
from angee.parties.schema import HandleType
from angee.storage.schema import FileType

Integration = apps.get_model("integrate", "Integration")
Handle = apps.get_model("parties", "Handle")
File = apps.get_model("storage", "File")
Channel = apps.get_model("messaging", "Channel")
Thread = apps.get_model("messaging", "Thread")
ThreadAttachment = apps.get_model("messaging", "ThreadAttachment")
ThreadFollower = apps.get_model("messaging", "ThreadFollower")
ThreadActivity = apps.get_model("messaging", "ThreadActivity")
ThreadNotification = apps.get_model("messaging", "ThreadNotification")
MessageSubtype = apps.get_model("messaging", "MessageSubtype")
Message = apps.get_model("messaging", "Message")
TrackingValue = apps.get_model("messaging", "TrackingValue")
Part = apps.get_model("messaging", "Part")
Fragment = apps.get_model("messaging", "Fragment")
MessageEdge = apps.get_model("messaging", "MessageEdge")
Participant = apps.get_model("messaging", "Participant")
Reaction = apps.get_model("messaging", "Reaction")
MessageStar = apps.get_model("messaging", "MessageStar")


@strawberry_django.type(Channel)
class ChannelType(IntegrationLabelMixin, BridgeSyncStatusMixin, AngeeNode):
    """GraphQL projection of a connected message channel (e.g. an email account)."""

    backend_class: auto
    lifecycle: auto
    runtime_status: auto
    config: strawberry.scalars.JSON
    last_sync_status: auto
    last_sync_completed_at: auto
    last_sync_items: auto
    last_sync_summary: strawberry.scalars.JSON
    sync_error: auto
    sync_progress: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(Fragment)
class FragmentType(AngeeNode):
    """GraphQL projection of a content-addressed text fragment."""

    kind: auto
    hash: auto
    text: auto

    @strawberry.field(name="part_count")
    def part_count(self) -> int:
        """How many parts (across all messages) share this fragment — the dedup fact."""

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
    parent: "PartType | None"
    fragment: FragmentType | None
    file: FileType | None
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


@strawberry_django.type(TrackingValue)
class TrackingValueType(AngeeNode):
    """GraphQL projection of one tracked field change on a chatter message."""

    position: auto
    field_name: auto
    field_label: auto
    field_type: auto
    old_value: strawberry.scalars.JSON | None
    new_value: strawberry.scalars.JSON | None
    old_display: auto
    new_display: auto
    metadata: strawberry.scalars.JSON


@strawberry_django.type(Participant)
class ParticipantType(AngeeNode):
    """GraphQL projection of a thread/message participant."""

    role: auto
    handle: HandleType | None
    joined_at: auto
    left_at: auto


@strawberry.type
class MessageReactionGroupType:
    """Odoo-style grouped reactions for one message."""

    reaction: str
    count: int
    self_reacted: bool = strawberry.field(name="self_reacted")
    handles: list[HandleType] = strawberry.field(default_factory=list)


@strawberry_django.type(Message)
class MessageType(AngeeNode):
    """GraphQL projection of a message."""

    platform: auto
    direction: auto
    status: auto
    message_type: auto
    external_id: auto
    preview: auto
    sent_at: auto
    received_at: auto
    sender: HandleType | None
    parent: "MessageType | None"
    subtype: MessageSubtypeType | None
    thread: "ThreadType | None"
    # The FK targets the Integration MTI parent (a messaging Channel or a social
    # Feed both produce messages), so the projection is the parent type — a
    # ChannelType declaration would crash resolving a Feed-ingested row.
    channel: IntegrationType | None
    parts: list[PartType]
    tracking_values: list[TrackingValueType]
    participants: list[ParticipantType]
    created_at: auto
    updated_at: auto

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

        The fragment-derived quote graph plus social relations — the "how it
        interconnects" read the content data view renders. Actor-scoped end to
        end: the owner (``MessageEdgeManager.for_message``) keeps only edges
        whose far endpoint the actor may read, because quote edges cross
        channels by construction.
        """

        return cast(list["MessageEdgeType"], MessageEdge.objects.for_message(cast(Any, self)))

    @strawberry.field
    def reaction_groups(self, info: strawberry.Info) -> list[MessageReactionGroupType]:
        """Return reactions grouped by content, with current-user state."""

        return _message_reaction_groups(self, _request_user(info))

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

    @strawberry_django.field(prefetch_related=["tracking_values"])
    def can_edit(self, info: strawberry.Info) -> bool:
        """Return whether the current actor may edit this message's body.

        Delegates to the message's own :meth:`Message.can_edit` owner (post access plus
        the mail edit rule), passing the record post access resolved and memoized once
        per thread by :func:`_message_post_access`. The ``tracking_values`` prefetch
        hint lets the optimizer batch the edit-rule predicate instead of an ``exists()``
        per row.
        """

        return cast(Any, self).can_edit(post_access=_message_post_access(self, info))

    @strawberry.field
    def can_delete(self, info: strawberry.Info) -> bool:
        """Return whether the current actor may delete this message.

        Delegates to :meth:`Message.can_delete` (the record thread's post access;
        deletion carries no mail-kind restriction of its own), passing the post access
        memoized once per thread by :func:`_message_post_access`.
        """

        return cast(Any, self).can_delete(post_access=_message_post_access(self, info))


@strawberry_django.type(Thread)
class ThreadType(AngeeNode):
    """GraphQL projection of a thread."""

    platform: auto
    modality: auto
    visibility: auto
    # Deliberate shape fork vs MessageType.title (a String): the thread's title
    # IS a fragment pointer (grouping keys on it), so the projection exposes the
    # content-addressed row; a message's title is derived text from its part.
    title: FragmentType | None
    message_count: auto
    last_message_at: auto
    # Integration parent, same reason as MessageType.channel.
    channel: IntegrationType | None
    messages: list[MessageType]
    participants: list[ParticipantType]
    created_at: auto
    updated_at: auto


@strawberry_django.type(ThreadAttachment)
class ThreadAttachmentType(AngeeNode):
    """GraphQL projection of a model-record thread attachment."""

    role: auto
    label: auto
    metadata: strawberry.scalars.JSON
    thread: ThreadType
    created_at: auto
    updated_at: auto

    @strawberry.field(name="model_label")
    def model_label(self) -> str:
        """Return the attached target's model label (``app_label.ModelName``)."""

        return cast(Any, self).record_model_label

    @strawberry.field(name="record_id")
    def record_id(self) -> strawberry.ID:
        """Return the attached target's stable public id (its sqid).

        A parent pointer back to the record — the ``ID`` scalar every record reference
        uses (``RecordReferenceInput.record_id``); navigating it re-gates through the
        target's own record read (an assignee not granted the record may 404).
        """

        return cast(strawberry.ID, cast(Any, self).record_public_id)


@strawberry_django.type(ThreadFollower)
class ThreadFollowerType(AngeeNode):
    """GraphQL projection of a user's thread membership — policy plus read receipt."""

    thread: ThreadType
    attachment: ThreadAttachmentType | None
    user: UserType
    notification_policy: auto
    subtype_keys: strawberry.scalars.JSON
    last_read_message: MessageType | None
    metadata: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(ThreadActivity)
class ThreadActivityType(AngeeNode):
    """GraphQL projection of a scheduled record chatter activity."""

    thread: ThreadType
    attachment: ThreadAttachmentType
    user: UserType
    activity_type: auto
    summary: auto
    note: auto
    due_date: auto
    completed_at: auto
    feedback: auto
    status: auto
    metadata: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto

    @strawberry.field
    def state(self) -> str:
        """Return the Odoo-style activity state."""

        return cast(Any, self).activity_state


@strawberry.type
class RecordPointerType:
    """Minimal, read-only pointer back to the record a chatter row is attached to.

    The record's human ``label``, its ``app_label.ModelName``, and its stable public id —
    the three facts a cross-record agenda needs to link back, each computed from the
    owning ``ThreadAttachment`` row alone (no target load, no parent-record read).
    Navigating the link re-gates through the record's own read (an assignee not granted
    the record may 404).
    """

    label: str
    model_label: str = strawberry.field(name="model_label")
    record_id: strawberry.ID = strawberry.field(name="record_id")


@strawberry_django.type(ThreadActivity)
class AgendaActivityType(AngeeNode):
    """The actor's own scheduled activity, narrowed for the cross-record agenda.

    ``activity_agenda`` delivers ``ThreadActivity`` rows to a bare assignee through the
    ``messaging/thread_activity.read`` ``user`` (assignee) arm — an assignment is itself
    the grant. Because that assignee holds no read on the parent record, thread, or
    attachment, this projection exposes only the activity's own fields, the assignee's own
    identity (``user`` — always the actor, by the agenda filter), and the minimal record
    pointer (``attachment`` → :class:`RecordPointerType`); it deliberately drops the parent
    ``thread`` and the attachment's ``metadata``, which a to-one traversal would resolve
    unguarded and so leak to a bare assignee through the agenda. The record-scoped
    ``record_thread`` surface projects the full :class:`ThreadActivityType` for a user who
    can already read the parent record.
    """

    user: UserType
    activity_type: auto
    summary: auto
    note: auto
    due_date: auto
    completed_at: auto
    feedback: auto
    status: auto
    metadata: strawberry.scalars.JSON
    created_at: auto
    updated_at: auto

    @strawberry.field
    def state(self) -> str:
        """Return the derived activity state (planned/today/overdue/done/canceled)."""

        return cast(Any, self).activity_state

    @strawberry.field
    def attachment(self) -> RecordPointerType:
        """Return the minimal pointer to the record this activity is attached to.

        The ``attachment`` FK is primed elevated and by id on the agenda queryset
        (:meth:`ThreadActivityQuerySet.with_record_pointers`), so this reads no extra
        row; the three pointer facts come from the ``ThreadAttachment`` owner without
        loading or re-gating the target record.
        """

        attachment = cast(Any, self).attachment
        record_ref = attachment.record_ref
        return RecordPointerType(
            label=attachment.label,
            model_label=record_ref.model_label,
            record_id=cast(strawberry.ID, record_ref.public_id),
        )


@strawberry_django.type(ThreadNotification)
class ThreadNotificationType(AngeeNode):
    """GraphQL projection of a per-recipient delivery row (read state lives on receipts)."""

    thread: ThreadType
    attachment: ThreadAttachmentType | None
    follower: ThreadFollowerType | None
    message: MessageType
    user: UserType
    notification_type: auto
    notification_status: auto
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
    src: "MessageType"
    dst: "MessageType"
    fragment: FragmentType | None
    created_at: auto


@strawberry_django.type(Reaction)
class ReactionType(AngeeNode):
    """GraphQL projection of an attributed reaction."""

    reaction: auto
    handle: HandleType | None
    created_at: auto


@strawberry_django.type(MessageStar)
class MessageStarType(AngeeNode):
    """GraphQL projection of a user's starred message marker."""

    message: MessageType
    user: UserType
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


@strawberry.input
class RecordMessagePostInput(RecordReferenceInput):
    """Fields accepted when posting an internal chatter message."""

    body: str
    kind: str = "comment"
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


@strawberry.input
class RecordActivityScheduleInput(RecordReferenceInput):
    """Fields accepted when scheduling a record activity."""

    summary: str
    note: str = ""
    due_date: date | None = strawberry.field(name="due_date", default=None)
    activity_type: str = strawberry.field(name="activity_type", default="todo")
    user_id: strawberry.ID | None = strawberry.field(name="user_id", default=None)


@strawberry.input
class RecordActivityFeedbackInput:
    """Fields accepted when completing a record activity."""

    activity_id: strawberry.ID = strawberry.field(name="activity_id")
    feedback: str = ""


@strawberry.input
class RecordActivityCancelInput:
    """Fields accepted when canceling a record activity."""

    activity_id: strawberry.ID = strawberry.field(name="activity_id")


@strawberry.type
class RecordThreadPayload:
    """A record chatter thread, or the error that prevented resolving it."""

    thread: ThreadType | None = None
    messages: list[MessageType] = strawberry.field(default_factory=list)
    message_result_count: int = strawberry.field(name="message_result_count", default=0)
    followers: list[ThreadFollowerType] = strawberry.field(default_factory=list)
    self_follower: ThreadFollowerType | None = strawberry.field(name="self_follower", default=None)
    suggested_recipients: list[SuggestedRecipientType] = strawberry.field(
        name="suggested_recipients",
        default_factory=list,
    )
    subtypes: list[MessageSubtypeOptionType] = strawberry.field(default_factory=list)
    follower_count: int = strawberry.field(name="follower_count", default=0)
    is_following: bool = strawberry.field(name="is_following", default=False)
    notifications: list[ThreadNotificationType] = strawberry.field(default_factory=list)
    unread_count: int = strawberry.field(name="unread_count", default=0)
    needaction_count: int = strawberry.field(name="needaction_count", default=0)
    message_has_error: bool = strawberry.field(name="message_has_error", default=False)
    message_has_error_counter: int = strawberry.field(name="message_has_error_counter", default=0)
    activities: list[ThreadActivityType] = strawberry.field(default_factory=list)
    activity_count: int = strawberry.field(name="activity_count", default=0)
    attachment_count: int = strawberry.field(name="attachment_count", default=0)
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class RecordMessagePostPayload:
    """A posted chatter message, or the error that prevented posting it."""

    message: MessageType | None = None
    thread: ThreadType | None = None
    followers: list[ThreadFollowerType] = strawberry.field(default_factory=list)
    follower_count: int = strawberry.field(name="follower_count", default=0)
    is_following: bool = strawberry.field(name="is_following", default=False)
    notifications: list[ThreadNotificationType] = strawberry.field(default_factory=list)
    unread_count: int = strawberry.field(name="unread_count", default=0)
    needaction_count: int = strawberry.field(name="needaction_count", default=0)
    message_has_error: bool = strawberry.field(name="message_has_error", default=False)
    message_has_error_counter: int = strawberry.field(name="message_has_error_counter", default=0)
    activities: list[ThreadActivityType] = strawberry.field(default_factory=list)
    activity_count: int = strawberry.field(name="activity_count", default=0)
    attachment_count: int = strawberry.field(name="attachment_count", default=0)
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class RecordMessageUpdatePayload:
    """An updated chatter message, or the error that prevented editing it."""

    message: MessageType | None = None
    thread: ThreadType | None = None
    followers: list[ThreadFollowerType] = strawberry.field(default_factory=list)
    follower_count: int = strawberry.field(name="follower_count", default=0)
    is_following: bool = strawberry.field(name="is_following", default=False)
    notifications: list[ThreadNotificationType] = strawberry.field(default_factory=list)
    unread_count: int = strawberry.field(name="unread_count", default=0)
    needaction_count: int = strawberry.field(name="needaction_count", default=0)
    message_has_error: bool = strawberry.field(name="message_has_error", default=False)
    message_has_error_counter: int = strawberry.field(name="message_has_error_counter", default=0)
    activities: list[ThreadActivityType] = strawberry.field(default_factory=list)
    activity_count: int = strawberry.field(name="activity_count", default=0)
    attachment_count: int = strawberry.field(name="attachment_count", default=0)
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class RecordMessageDeletePayload:
    """A deleted chatter message id plus refreshed thread state, or an error."""

    deleted_message_id: strawberry.ID | None = strawberry.field(name="deleted_message_id", default=None)
    thread: ThreadType | None = None
    messages: list[MessageType] = strawberry.field(default_factory=list)
    message_result_count: int = strawberry.field(name="message_result_count", default=0)
    followers: list[ThreadFollowerType] = strawberry.field(default_factory=list)
    follower_count: int = strawberry.field(name="follower_count", default=0)
    is_following: bool = strawberry.field(name="is_following", default=False)
    notifications: list[ThreadNotificationType] = strawberry.field(default_factory=list)
    unread_count: int = strawberry.field(name="unread_count", default=0)
    needaction_count: int = strawberry.field(name="needaction_count", default=0)
    message_has_error: bool = strawberry.field(name="message_has_error", default=False)
    message_has_error_counter: int = strawberry.field(name="message_has_error_counter", default=0)
    activities: list[ThreadActivityType] = strawberry.field(default_factory=list)
    activity_count: int = strawberry.field(name="activity_count", default=0)
    attachment_count: int = strawberry.field(name="attachment_count", default=0)
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class RecordMessageReactionPayload:
    """A reacted chatter message, or the error that prevented reacting."""

    message: MessageType | None = None
    reaction_groups: list[MessageReactionGroupType] = strawberry.field(name="reaction_groups", default_factory=list)
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class RecordMessageStarPayload:
    """A starred/unstarred chatter message result, or the error that prevented it."""

    message: MessageType | None = None
    starred: bool = False
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class RecordMessageDonePayload:
    """A message marked done for the user, or the error that prevented it."""

    message: MessageType | None = None
    thread: ThreadType | None = None
    notifications: list[ThreadNotificationType] = strawberry.field(default_factory=list)
    unread_count: int = strawberry.field(name="unread_count", default=0)
    needaction_count: int = strawberry.field(name="needaction_count", default=0)
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class RecordFollowPayload:
    """A record follower update result, or the error that prevented it."""

    follower: ThreadFollowerType | None = None
    thread: ThreadType | None = None
    followers: list[ThreadFollowerType] = strawberry.field(default_factory=list)
    follower_count: int = strawberry.field(name="follower_count", default=0)
    is_following: bool = strawberry.field(name="is_following", default=False)
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class RecordActivityPayload:
    """A record activity update result, or the error that prevented it."""

    activity: ThreadActivityType | None = None
    thread: ThreadType | None = None
    activities: list[ThreadActivityType] = strawberry.field(default_factory=list)
    activity_count: int = strawberry.field(name="activity_count", default=0)
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.type
class MessagingQuery:
    """Record- and actor-scoped chatter queries."""

    @strawberry.field(name="record_thread")
    def record_thread(self, info: strawberry.Info, input: RecordThreadInput) -> RecordThreadPayload:
        """Return the existing chatter thread attached to a model record."""

        try:
            record = _threaded_record(input)
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
        )

    @strawberry.field(name="activity_agenda")
    def activity_agenda(
        self,
        info: strawberry.Info,
        window_start: date,
        window_end: date,
        include_done: bool = False,
    ) -> list[AgendaActivityType]:
        """Return the current actor's assigned activities due within the window.

        The actor's own agenda across records: ``ThreadActivity`` rows assigned to the
        actor (``user``), due within ``[window_start, window_end)``, ordered by due date.
        The window is the whole bound — no pagination. Done and canceled are excluded
        unless ``include_done``. Overdue is neither stored nor filtered; it rides each
        row's ``state`` derivation.

        Authorization rides the existing ``messaging/thread_activity.read`` ``user``
        (assignee) arm — an assignment is itself the grant — so the read needs no
        parent-record re-gate and no record-read fan-out. Each row is projected as
        :class:`AgendaActivityType`: the activity's own fields, the actor's own identity,
        and the minimal record pointer — never the parent thread or attachment metadata,
        which a to-one traversal would otherwise resolve unguarded. The assignee
        FK is the request actor by construction, and the pointer ``attachment`` is primed
        elevated and by id on the queryset, so the projection needs no REBAC-guarded
        ``select_related`` (the pitfall) and no per-row pointer fan-out.
        """

        user = _request_user(info)
        if user is None:
            return []
        rows = ThreadActivity.objects.agenda(
            user,
            window_start,
            window_end,
            include_done=include_done,
        ).with_record_pointers()
        for row in rows:
            # The agenda filters on user == actor, so every row's assignee is the request
            # actor: prime the FK from the actor already in hand instead of a per-row
            # (and REBAC-guarded) user load.
            row.user = user
        return rows


@strawberry.type
class MessagingMutation:
    """Record-backed chatter mutations."""

    @strawberry.mutation(name="post_record_message")
    def post_record_message(self, info: strawberry.Info, input: RecordMessagePostInput) -> RecordMessagePostPayload:
        """Post an internal comment to the record's chatter thread."""

        try:
            record = _threaded_record(input)
        except ValueError as error:
            return RecordMessagePostPayload(error=str(error), error_code="BAD_RECORD")
        if record is None:
            return RecordMessagePostPayload(error="record not found", error_code="NOT_FOUND")
        try:
            attachments = _storage_files(input.attachment_ids)
            recipient_user_ids = tuple(user.pk for user in _users_from_public_ids(input.recipient_user_ids))
            parent = _message(input.parent_message_id) if input.parent_message_id is not None else None
            kind = _record_message_post_kind(input.kind)
            if kind == "note":
                if recipient_user_ids or input.autofollow_recipients:
                    raise ValueError("Internal notes cannot target recipients.")
                message = cast(Any, record).message_log(
                    input.body,
                    attachments=attachments,
                    parent=parent,
                )
            else:
                message = cast(Any, record).message_post(
                    input.body,
                    attachments=attachments,
                    recipient_user_ids=recipient_user_ids,
                    autofollow_recipients=input.autofollow_recipients,
                    parent=parent,
                )
        except PermissionDenied as error:
            return RecordMessagePostPayload(error=str(error), error_code="PERMISSION_DENIED")
        except ValueError as error:
            return RecordMessagePostPayload(error=str(error), error_code="BAD_MESSAGE")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordMessagePostPayload(
            message=message,
            thread=message.thread,
            followers=payload.followers,
            follower_count=payload.follower_count,
            is_following=payload.is_following,
            notifications=payload.notifications,
            unread_count=payload.unread_count,
            needaction_count=payload.needaction_count,
            message_has_error=payload.message_has_error,
            message_has_error_counter=payload.message_has_error_counter,
            activities=payload.activities,
            activity_count=payload.activity_count,
            attachment_count=payload.attachment_count,
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
            record = _threaded_record(input)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageUpdatePayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageUpdatePayload(error="record not found", error_code="NOT_FOUND")
        try:
            message = cast(Any, record).message_update_content(message, body=input.body)
        except PermissionDenied as error:
            return RecordMessageUpdatePayload(error=str(error), error_code="PERMISSION_DENIED")
        except ValueError as error:
            return RecordMessageUpdatePayload(error=str(error), error_code="BAD_MESSAGE")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordMessageUpdatePayload(
            message=message,
            thread=message.thread,
            followers=payload.followers,
            follower_count=payload.follower_count,
            is_following=payload.is_following,
            notifications=payload.notifications,
            unread_count=payload.unread_count,
            needaction_count=payload.needaction_count,
            message_has_error=payload.message_has_error,
            message_has_error_counter=payload.message_has_error_counter,
            activities=payload.activities,
            activity_count=payload.activity_count,
            attachment_count=payload.attachment_count,
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
            record = _threaded_record(input)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageDeletePayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageDeletePayload(error="record not found", error_code="NOT_FOUND")
        deleted_message_id = input.message_id
        try:
            thread = cast(Any, record).message_unlink(message)
        except PermissionDenied as error:
            return RecordMessageDeletePayload(error=str(error), error_code="PERMISSION_DENIED")
        except ValueError as error:
            return RecordMessageDeletePayload(error=str(error), error_code="BAD_MESSAGE")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordMessageDeletePayload(
            deleted_message_id=deleted_message_id,
            thread=thread,
            messages=payload.messages,
            message_result_count=payload.message_result_count,
            followers=payload.followers,
            follower_count=payload.follower_count,
            is_following=payload.is_following,
            notifications=payload.notifications,
            unread_count=payload.unread_count,
            needaction_count=payload.needaction_count,
            message_has_error=payload.message_has_error,
            message_has_error_counter=payload.message_has_error_counter,
            activities=payload.activities,
            activity_count=payload.activity_count,
            attachment_count=payload.attachment_count,
        )

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
            record = _threaded_record(input)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageReactionPayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageReactionPayload(error="record not found", error_code="NOT_FOUND")
        try:
            message = cast(Any, record).message_reaction(
                message,
                reaction=input.reaction,
                action=input.action,
                user=user,
            )
        except PermissionDenied as error:
            return RecordMessageReactionPayload(error=str(error), error_code="PERMISSION_DENIED")
        except ValueError as error:
            return RecordMessageReactionPayload(error=str(error), error_code="BAD_REACTION")
        return RecordMessageReactionPayload(
            message=message,
            reaction_groups=_message_reaction_groups(message, user),
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
            record = _threaded_record(input)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageStarPayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageStarPayload(error="record not found", error_code="NOT_FOUND")
        try:
            starred = cast(Any, record).message_set_starred(
                message,
                user=user,
                starred=input.starred,
            )
        except PermissionDenied as error:
            return RecordMessageStarPayload(error=str(error), error_code="PERMISSION_DENIED")
        except ValueError as error:
            return RecordMessageStarPayload(error=str(error), error_code="BAD_MESSAGE")
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
            record = _threaded_record(input)
            message = _message(input.message_id)
        except ValueError as error:
            return RecordMessageDonePayload(error=str(error), error_code="BAD_MESSAGE")
        if record is None:
            return RecordMessageDonePayload(error="record not found", error_code="NOT_FOUND")
        try:
            cast(Any, record).message_set_done(message, user=user)
        except PermissionDenied as error:
            return RecordMessageDonePayload(error=str(error), error_code="PERMISSION_DENIED")
        except ValueError as error:
            return RecordMessageDonePayload(error=str(error), error_code="BAD_MESSAGE")
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
            record = _threaded_record(input)
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
        except ValueError as error:
            return RecordFollowPayload(error=str(error), error_code="BAD_FOLLOWER")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordFollowPayload(
            follower=follower,
            thread=payload.thread,
            followers=payload.followers,
            follower_count=payload.follower_count,
            is_following=payload.is_following,
        )

    @strawberry.mutation(name="schedule_record_activity")
    def schedule_record_activity(
        self,
        info: strawberry.Info,
        input: RecordActivityScheduleInput,
    ) -> RecordActivityPayload:
        """Schedule an activity on the record's chatter thread."""

        current_user = _request_user(info)
        if current_user is None:
            return RecordActivityPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = _threaded_record(input)
            assigned_user = _user_from_public_id(input.user_id) if input.user_id else current_user
        except ValueError as error:
            return RecordActivityPayload(error=str(error), error_code="BAD_RECORD")
        if record is None:
            return RecordActivityPayload(error="record not found", error_code="NOT_FOUND")
        try:
            activity = cast(Any, record).activity_schedule(
                user=assigned_user,
                summary=input.summary,
                note=input.note,
                due_date=input.due_date,
                activity_type=input.activity_type,
            )
        except PermissionDenied as error:
            return RecordActivityPayload(error=str(error), error_code="PERMISSION_DENIED")
        except ValueError as error:
            return RecordActivityPayload(error=str(error), error_code="BAD_ACTIVITY")
        payload = _record_thread_payload(record, info, role=input.role)
        return RecordActivityPayload(
            activity=activity,
            thread=payload.thread,
            activities=payload.activities,
            activity_count=payload.activity_count,
        )

    @strawberry.mutation(name="complete_record_activity")
    def complete_record_activity(
        self,
        info: strawberry.Info,
        input: RecordActivityFeedbackInput,
    ) -> RecordActivityPayload:
        """Complete a scheduled record activity and log its feedback."""

        if _request_user(info) is None:
            return RecordActivityPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            activity = _thread_activity(input.activity_id)
        except ValueError as error:
            return RecordActivityPayload(error=str(error), error_code="BAD_ACTIVITY")
        record = activity.attachment.target
        if record is None or not isinstance(record, ThreadedModelMixin):
            return RecordActivityPayload(error="activity target is not threaded", error_code="BAD_RECORD")
        record = _readable_record(record)
        if record is None:
            return RecordActivityPayload(error="record not found", error_code="NOT_FOUND")
        try:
            activity = cast(Any, record).activity_feedback(activity, feedback=input.feedback)
        except PermissionDenied as error:
            return RecordActivityPayload(error=str(error), error_code="PERMISSION_DENIED")
        payload = _record_thread_payload(record, info)
        return RecordActivityPayload(
            activity=activity,
            thread=payload.thread,
            activities=payload.activities,
            activity_count=payload.activity_count,
        )

    @strawberry.mutation(name="cancel_record_activity")
    def cancel_record_activity(
        self,
        info: strawberry.Info,
        input: RecordActivityCancelInput,
    ) -> RecordActivityPayload:
        """Cancel a scheduled record activity."""

        if _request_user(info) is None:
            return RecordActivityPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            activity = _thread_activity(input.activity_id)
        except ValueError as error:
            return RecordActivityPayload(error=str(error), error_code="BAD_ACTIVITY")
        record = activity.attachment.target
        if record is None or not isinstance(record, ThreadedModelMixin):
            return RecordActivityPayload(error="activity target is not threaded", error_code="BAD_RECORD")
        record = _readable_record(record)
        if record is None:
            return RecordActivityPayload(error="record not found", error_code="NOT_FOUND")
        try:
            activity = cast(Any, record).activity_unlink(activity)
        except PermissionDenied as error:
            return RecordActivityPayload(error=str(error), error_code="PERMISSION_DENIED")
        payload = _record_thread_payload(record, info)
        return RecordActivityPayload(
            activity=activity,
            thread=payload.thread,
            activities=payload.activities,
            activity_count=payload.activity_count,
        )

    @strawberry.mutation(name="mark_record_thread_read")
    def mark_record_thread_read(self, info: strawberry.Info, input: RecordReferenceInput) -> RecordThreadPayload:
        """Mark the current user's notifications on a record thread as read."""

        user = _request_user(info)
        if user is None:
            return RecordThreadPayload(error="authentication required", error_code="NOT_AUTHENTICATED")
        try:
            record = _threaded_record(input)
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

    del info
    # The title annotation serves list rows in SQL; Message.title() prefers it,
    # so a title column on the grid costs no per-row probe.
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
    ``delete_<res>_by_pk`` (its own ``owner``/``admin`` permission would allow it).
    Narrowing the write-target queryset to ``.inbox()`` makes the by-pk lookup miss
    a record-attached row, so the generic mutation reports it as not found while the
    inbox rows keep their update/delete surfaces. The ``.inbox()`` verb resolves
    polymorphically on each model's queryset, so one backend serves both.
    """

    def write_target_queryset(self) -> Any:
        """Scope the update/delete/save target queryset to non-record rows."""

        return super().write_target_queryset().inbox()


_CHANNEL_RESOURCE = hasura_model_resource(
    ChannelType,
    model=Channel,
    name="channels",
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
    sortable=["display_name", "backend_class", "lifecycle", "runtime_status", "last_sync_completed_at", "updated_at"],
    aggregatable=["id", "last_sync_items"],
    groupable=["backend_class", "lifecycle", "runtime_status", "last_sync_status", "sync_stage"],
    insert=False,
    update=False,
    delete=False,
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
        # The transcript's keyset "load older" cursors on (sent_at, created_at).
        "created_at",
    ],
    sortable=["sent_at", "received_at", "created_at"],
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
    updatable=["status"],
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

_RESOURCE_TYPES = [
    *_CHANNEL_RESOURCE.types,
    *_MESSAGE_RESOURCE.types,
    *_THREAD_RESOURCE.types,
    *_PART_RESOURCE.types,
]


_MESSAGING_SCHEMA_BUCKET = {
    "query": [
        MessagingQuery,
        _CHANNEL_RESOURCE.query,
        _MESSAGE_RESOURCE.query,
        _THREAD_RESOURCE.query,
        _PART_RESOURCE.query,
    ],
    "mutation": [
        MessagingMutation,
        _CHANNEL_RESOURCE.mutation,
        _MESSAGE_RESOURCE.mutation,
        _THREAD_RESOURCE.mutation,
        _PART_RESOURCE.mutation,
    ],
    "types": [
        ChannelType,
        ThreadType,
        ThreadAttachmentType,
        ThreadFollowerType,
        ThreadActivityType,
        AgendaActivityType,
        RecordPointerType,
        ThreadNotificationType,
        MessageSubtypeType,
        MessageType,
        TrackingValueType,
        PartType,
        FragmentType,
        MessageEdgeType,
        ParticipantType,
        ReactionType,
        MessageStarType,
        MessageSubtypeOptionType,
        SuggestedRecipientType,
        MessageReactionGroupType,
        RecordThreadPayload,
        RecordMessagePostPayload,
        RecordMessageUpdatePayload,
        RecordMessageDeletePayload,
        RecordMessageReactionPayload,
        RecordMessageStarPayload,
        RecordMessageDonePayload,
        RecordFollowPayload,
        RecordActivityPayload,
        *_RESOURCE_TYPES,
    ],
}


schemas = {
    "console": {
        **_MESSAGING_SCHEMA_BUCKET,
        "subscription": [
            changes(Channel, field="channelChanged"),
            changes(Message, field="messageChanged"),
            changes(Thread, field="threadChanged"),
            changes(ThreadActivity, field="threadActivityChanged"),
            changes(ThreadNotification, field="threadNotificationChanged"),
            changes(MessageStar, field="messageStarChanged"),
        ],
    },
}


def _threaded_record(input: RecordReferenceInput) -> Any | None:
    """Return the record addressed by ``input`` when its model opts into chatter."""

    try:
        model = apps.get_model(input.model_label)
    except (LookupError, ValueError) as error:
        raise ValueError(f"Unknown model {input.model_label!r}.") from error
    if not issubclass(model, ThreadedModelMixin):
        raise ValueError(f"{model._meta.label} does not inherit ThreadedModelMixin.")
    try:
        return instance_from_public_id(model, str(input.record_id))
    except ImproperlyConfigured as error:
        raise ValueError(str(error)) from error


def _record_message_post_kind(kind: str) -> str:
    """Return the normalized side-chatter post kind."""

    value = str(kind or "comment").strip().lower()
    if value in {"comment", "message"}:
        return "comment"
    if value == "note":
        return "note"
    raise ValueError("Message kind must be 'comment' or 'note'.")


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


def _record_post_access_cache(info: strawberry.Info | None) -> dict[Any, bool]:
    """Return the per-request memo of record post-access keyed by thread id.

    Record post-access is a record-level rebac fact shared by every message in a
    thread, so it is resolved once per distinct thread per request — the batch key a
    dataloader would use — instead of re-walking message → record and re-checking
    rebac for each row. The synchronous query schema has no async dataloader, so the
    request object owns this per-request cache.
    """

    request = request_from_info(info) if info is not None else None
    if request is None:
        return {}
    cache = getattr(request, "_messaging_post_access", None)
    if cache is None:
        cache = {}
        setattr(request, "_messaging_post_access", cache)
    return cache


def _message_post_access(message: Any, info: strawberry.Info | None) -> bool:
    """Return whether the request actor may write (edit/delete) this message.

    Reuses the record's own public post-access owner (``ThreadedModelMixin.can_post``)
    — the exact predicate the update and delete mutations enforce — memoized per thread
    (see :func:`_record_post_access_cache`) so the rebac fact is resolved once per
    distinct record per request on every path (auto-CRUD, thread traversal, or the
    record feed) instead of per row.
    """

    user = _request_user(info)
    if user is None:
        return False
    thread_id = message.thread_id
    if thread_id is None:
        return False
    cache = _record_post_access_cache(info)
    if thread_id not in cache:
        record = message.threaded_record()
        cache[thread_id] = bool(record is not None and cast(Any, record).can_post(user))
    return cache[thread_id]


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
) -> RecordThreadPayload:
    """Return a record thread payload with follower state for the request user."""

    thread = cast(Any, record).message_thread(create=False)
    messages, message_result_count = (
        Message.objects.for_record(
            record,
            role=role,
            search=search,
            limit=message_limit,
            before=before,
            after=after,
            around=around,
        )
        if thread is not None
        else ([], 0)
    )
    followers = (
        list(cast(Any, record).message_followers().select_related("user"))
        if thread is not None
        else []
    )
    activities = (
        list(cast(Any, record).activity_ids().select_related("user"))
        if thread is not None
        else []
    )
    attachment_count = (
        apps.get_model("messaging", "Part").objects.filter(message__thread=thread).attachments().count()
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
    self_follower = next((follower for follower in followers if follower.user_id == user_id), None)
    is_following = self_follower is not None
    notifications = (
        list(
            ThreadNotification.objects.for_record(record, user=user, role=role)
            .select_related("thread", "attachment", "follower", "message", "message__subtype", "user")[:50]
        )
        if thread is not None and user is not None
        else []
    )
    unread_count = (
        ThreadFollower.objects.unread_count_for_record(record, user=user, role=role)
        if thread is not None and user is not None
        else 0
    )
    if messages:
        # Post access is a record-level fact shared by the whole page: prime the
        # per-request memo once (keyed by this thread) through the public post-access
        # owner so the can_edit/can_delete resolvers read it instead of re-walking
        # message → record for every row.
        _record_post_access_cache(info)[messages[0].thread_id] = bool(
            cast(Any, record).can_post(user)
        )
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
    return RecordThreadPayload(
        thread=thread,
        messages=messages,
        message_result_count=message_result_count,
        followers=followers,
        self_follower=self_follower,
        suggested_recipients=suggested_recipients,
        subtypes=[
            MessageSubtypeOptionType(**option)
            for option in message_subtype_options(record._meta.label)
        ],
        follower_count=len(followers),
        is_following=is_following,
        notifications=notifications,
        unread_count=unread_count,
        needaction_count=unread_count,
        message_has_error=message_has_error_counter > 0,
        message_has_error_counter=message_has_error_counter,
        activities=activities,
        activity_count=len(activities),
        attachment_count=attachment_count,
    )


def _request_user(info: strawberry.Info | None) -> Any | None:
    """Return the authenticated request user, if there is one."""

    if info is None:
        return None
    request = request_from_info(info)
    user = getattr(request, "user", None)
    if user is None or getattr(user, "is_authenticated", False) is False:
        return None
    return user


def _user_from_public_id(user_id: strawberry.ID | None) -> Any:
    """Return a user by public id for activity assignment."""

    if user_id is None:
        raise ValueError("A user id is required.")
    try:
        user = instance_from_public_id(get_user_model(), str(user_id))
    except ImproperlyConfigured as error:
        raise ValueError(str(error)) from error
    if user is None:
        raise ValueError("assigned user not found")
    return user


def _users_from_public_ids(user_ids: list[strawberry.ID]) -> tuple[Any, ...]:
    """Return users addressed by public id for direct chatter recipients."""

    users = []
    for user_id in user_ids:
        try:
            user = instance_from_public_id(get_user_model(), str(user_id))
        except ImproperlyConfigured as error:
            raise ValueError(str(error)) from error
        if user is None:
            raise ValueError("recipient user not found")
        users.append(user)
    return tuple(users)


def _thread_activity(activity_id: strawberry.ID) -> Any:
    """Return a scheduled record activity by public id, decoded elevated.

    The decode intentionally bypasses the activity's own read scope: authorization
    for reaching (and acting on) a record activity rides its parent record's read —
    the ``record_thread`` gate applied by :func:`_readable_record` in the complete
    and cancel resolvers — not the activity's own messaging permission. So the decode
    only resolves the public id; the record read is the gate.
    """

    try:
        activity = instance_from_public_id(
            ThreadActivity,
            str(activity_id),
            queryset=ThreadActivity._base_manager.all(),
        )
    except ImproperlyConfigured as error:
        raise ValueError(str(error)) from error
    if activity is None:
        raise ValueError("activity not found")
    return activity


def _readable_record(record: Any) -> Any | None:
    """Return ``record`` re-resolved under the request actor's read scope, else None.

    The ``record_thread`` gate: record chatter — its thread, messages, and
    activities — is reachable only when the actor can read the parent record, so an
    activity reached elevated (see :func:`_thread_activity`) is authorized *through*
    the record's read. Re-resolving on the actor-scoped default manager (the exact
    scope :func:`_threaded_record` uses) yields ``None`` when the record is unreadable,
    which the caller surfaces as ``NOT_FOUND``.
    """

    return type(record)._default_manager.filter(pk=record.pk).first()


def _message(message_id: strawberry.ID) -> Any:
    """Return a message by public id."""

    try:
        message = Message.from_public_id(str(message_id))
    except ImproperlyConfigured as error:
        raise ValueError(str(error)) from error
    if message is None:
        raise ValueError("message not found")
    return message


def _storage_files(file_ids: list[strawberry.ID]) -> tuple[Any, ...]:
    """Return readable storage files addressed by public id."""

    files = []
    for file_id in file_ids:
        file = File.objects.all().from_public_id(str(file_id))
        if file is None:
            raise ValueError("attachment not found")
        files.append(file)
    return tuple(files)
