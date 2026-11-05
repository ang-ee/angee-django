"""Messages models for Messaging; public imports remain on the package."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Self

from django.apps import apps
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models, router, transaction
from django.db.models.functions import MD5, Coalesce
from django.utils import timezone
from rebac import (
    PermissionDenied,
    current_actor,
    system_context,
    to_subject_ref,
)

from angee.base.fields import ModelLabelField
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.locks import OptimisticLockMixin
from angee.base.refs import RecordRef
from angee.base.serialization import strip_null_bytes
from angee.graphql.mixins import ChangeBroadcastMixin
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.trash import ProtectedTrashMixin
from angee.messaging.constants import DELIVERY_TASK_EXPIRES
from angee.messaging.delivery import queue_message_delivery
from angee.messaging.managers import (
    FragmentManager,
    MessageEdgeManager,
    MessageManager,
    MessageStarManager,
    MessageSubtypeManager,
    PartManager,
    ReactionManager,
)
from angee.messaging.resources import MessageResource
from angee.messaging.source_copies import SourceCopyMixin
from angee.parties.models import Handle
from angee.resources.mixins import ResourceLoadMixin
from angee.tags.models import TaggedModel

if TYPE_CHECKING:
    from angee.messaging.models.threads import ThreadAttachment


class MessageSubtype(AuditMixin, ProtectedDataModel, concrete=True):
    """A typed chatter event category, mirroring Odoo's message subtypes.

    Subtypes classify system notifications and comments so followers can later
    opt into precise event families. ``model_label`` scopes a subtype to one
    model; an empty value is a global subtype.
    """


    # The built-in subtype catalogue this base messaging slice ships: the closed set of
    # keys the chatter write path classifies messages under. The model owns the catalogue
    # (key → name, description); the managers seed rows and build the follower option
    # lists from it, so the defaults live once here instead of a parallel module dict.
    # Custom and per-``model_label`` subtypes are additional dynamic rows keyed off this.
    BUILTIN_DEFAULTS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("comment", "Comment", "Discussion comment"),
        ("note", "Note", "Internal note"),
        ("record_created", "Record created", "Record created"),
        ("record_updated", "Record updated", "Record updated"),
    )

    objects = MessageSubtypeManager()

    sqid_prefix = "mst_"
    key = models.CharField(max_length=128)
    # No standalone index: ``model_label`` is the leading column of
    # ``uq_message_subtype_model_key``, whose btree already serves a lone
    # ``model_label`` lookup — a separate single-column index would be redundant.
    model_label = ModelLabelField(max_length=128, blank=True, default="")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    internal = models.BooleanField(default=False)
    default = models.BooleanField(default=True)
    sequence = models.PositiveIntegerField(default=100)
    hidden = models.BooleanField(default=False)
    metadata = models.JSONField(blank=True, default=dict)

    @classmethod
    def builtin_default(cls, key: str) -> tuple[str, str] | None:
        """Return the ``(name, description)`` a built-in subtype ``key`` ships with."""

        for builtin_key, name, description in cls.BUILTIN_DEFAULTS:
            if builtin_key == key:
                return name, description
        return None

    @classmethod
    def builtin_options(cls) -> dict[str, dict[str, Any]]:
        """Return the follower-selectable option dict for each built-in subtype key.

        Ordered by declaration so the option ``sequence`` is deterministic before any
        row exists; existing global/model rows override these in the option list.
        """

        return {
            key: {
                "key": key,
                "name": name,
                "description": description,
                "internal": False,
                "default": True,
                "sequence": (index + 1) * 10,
            }
            for index, (key, name, description) in enumerate(cls.BUILTIN_DEFAULTS)
        }

    class Meta:
        """Django model options for message subtypes."""

        abstract = True
        ordering = ("sequence", "key", "sqid")
        constraints = (
            models.UniqueConstraint(
                fields=("model_label", "key"),
                name="uq_message_subtype_model_key",
            ),
        )

    def __str__(self) -> str:
        """Return a readable subtype label."""

        return self.name


@dataclass(frozen=True)
class MessageReactionGroup:
    """One message's reactions of a single content, grouped for the chatter feed.

    The domain read shape :meth:`Message.reaction_groups` returns; the GraphQL layer
    projects it onto its own type. Owned here beside :class:`Message` so the grouping
    fact lives once, next to the rows it summarizes, not in the resolver layer.
    """

    reaction: str
    count: int
    self_reacted: bool
    handles: tuple[Any, ...]


@dataclass(frozen=True)
class WebformSubmission:
    """A public form's answers and claimed email; the email proves no identity."""

    answers: dict[str, Any]
    unverified_submitter_email: str | None


class Message(
    ChangeBroadcastMixin,
    SourceCopyMixin,
    ResourceLoadMixin,
    TaggedModel,
    ProtectedTrashMixin,
    OptimisticLockMixin,
    AuditMixin,
    ProtectedDataModel,
    concrete=True,
):
    """One message — the unit of a thread. The root post is itself a Message.

    Dedup key is ``(channel, external_id)`` — one row per provider event per
    source, carried by the ``MD5(external_id)`` expression constraint so an
    unbounded provider id never overflows a btree entry. The same event reached
    through two channels is two messages, matching the "message identity ≠
    content identity" rule; ``same_as`` evidence folds the newer copy into the
    older as a retained redirect (:class:`SourceCopyMixin`), and readers collapse
    them through ``canonical()``.
    ``parent`` is the single-parent reply pointer (In-Reply-To); richer
    cross-message relations live on :class:`MessageEdge`. The body — including a
    sparse ``TITLE`` part for the subject and ``HEADER`` parts for retained
    envelope headers — is the :class:`Part` tree; raw envelope recipients are kept
    in ``metadata`` as the lossless source behind :class:`Participant`.

    Edits are data, not shadow rows: ``edit_history`` appends newest-first
    ``{edited_at, edited_by_id, prev_fragment_hashes}`` entries while the replaced
    text survives as immutable content-addressed fragments — no per-save history
    table doubling the hot write path.

    Moderation is trash (:class:`~angee.iam.models.trash.ProtectedTrashMixin`): a trashed
    message is withheld from every reader but channel managers and admins, and
    record chatter leaves it out of every read but its moderators' removed list.
    """

    _inbox_attachment: ThreadAttachment | None = None
    """Authorized notification context set by the notification read owner."""

    resource_class = MessageResource
    rebac_grantable = {"reader": "write"}
    class Direction(models.TextChoices):
        """Whether a message came in, went out, or is internal."""

        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"
        INTERNAL = "internal", "Internal"

    class MessageStatus(models.TextChoices):
        """Delivery lifecycle state of a message; moderation is the trash flag."""

        DRAFT = "draft", "Draft"
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        SYNCED = "synced", "Synced"
        EDITED = "edited", "Edited"
        FAILED = "failed", "Failed"
        UNCERTAIN = "uncertain", "Uncertain"

    ACTIVE_OUTBOUND_STATUSES = (MessageStatus.DRAFT, MessageStatus.QUEUED, MessageStatus.SENT, MessageStatus.UNCERTAIN)
    """Prepared, accepted or unresolved outbound messages that still answer their parent."""
    PUBLISHED_STATUSES = (MessageStatus.SYNCED, MessageStatus.EDITED, MessageStatus.SENT)
    """Provider-visible messages, whether ingested or sent locally."""
    POSSIBLY_PUBLIC_STATUSES = (MessageStatus.SENT, MessageStatus.UNCERTAIN)
    """Outbound states that may be provider-visible even after local trash."""

    class MessageKind(models.TextChoices):
        """Odoo-style functional kind of a message.

        Every value has a live producer: ``COMMENT`` is a chatter note *or* a
        public post (disambiguated structurally — ``direction``/thread shape, see
        :meth:`Message.content_edit_error`), ``EMAIL``/``CHAT`` are set by the
        ingest producers, ``NOTIFICATION`` by the retained system log. Add a value only together with its producer.
        """

        COMMENT = "comment", "Comment"
        EMAIL = "email", "Email"
        CHAT = "chat", "Chat"
        NOTIFICATION = "notification", "Notification"

    sqid_prefix = "msg_"
    thread = models.ForeignKey(
        "messaging.Thread",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="messages",
        # Covered: every composite index below leads with thread (the Zulip
        # covered-FK rule — a redundant single-column index can misprice plans).
        db_index=False,
    )
    channel = models.ForeignKey(
        "messaging.Channel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messaging_messages",
    )
    sender = models.ForeignKey(
        "parties.Handle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_messages",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    platform = StateField(choices_enum=Handle.Platform, default=Handle.Platform.EMAIL)
    direction = StateField(choices_enum=Direction, default=Direction.INBOUND)
    status = StateField(choices_enum=MessageStatus, default=MessageStatus.SYNCED)
    message_type = StateField(choices_enum=MessageKind, default=MessageKind.COMMENT, db_index=True)
    subtype = models.ForeignKey(
        "messaging.MessageSubtype",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
    )
    external_id = models.TextField(blank=True, default="")
    preview = models.CharField(max_length=512, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    received_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    """Draft release, queued retry, or idle UNCERTAIN recovery horizon."""
    delivery_lease_until = models.DateTimeField(null=True, blank=True)
    """Pending dispatch or live claim deadline; unleased queues stay inert."""
    delivery_claim = models.UUIDField(null=True, blank=True, editable=False)
    """Committed effect marker and settlement fence for the current provider attempt."""
    delivery_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    """Current send horizon, retained after possible acceptance for tolerant lookup."""
    delivery_possibly_accepted = models.BooleanField(default=False, editable=False)
    """Only uncertain settlement or an expired effect claim sets this marker."""
    delivery_operator_only = models.BooleanField(default=False, editable=False)
    """Automatic reconciliation is unavailable or has exhausted its retry budget."""
    delivery_reconcile_attempts = models.PositiveSmallIntegerField(default=0, editable=False)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    """Operator who explicitly acknowledged or retried this delivery."""
    edit_history = models.JSONField(blank=True, default=list)
    metadata = models.JSONField(blank=True, default=dict)

    objects = MessageManager()

    @classmethod
    def protected_local_keys(cls) -> frozenset[str]:
        """Trusted provenance and delivery facts contributed through autoconfig."""

        return frozenset(settings.ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS)

    @staticmethod
    def held_draft_fields() -> dict[str, Any]:
        """The shared held predicate, including approval-only unscheduled drafts."""

        return {"direction": "outbound", "status": "draft", "is_trashed": False}

    @staticmethod
    def counted_in_thread_q() -> models.Q:
        """All retained messages count, including local drafts, queues and failures."""

        return models.Q(is_trashed=False)

    @property
    def is_held(self) -> bool:
        """Whether this retained draft awaits its due time or operator approval."""

        return all(getattr(self, key) == value for key, value in self.held_draft_fields().items())

    hasura_filter_expressions: ClassVar[
        Mapping[str, Callable[[models.QuerySet[Any]], models.expressions.BaseExpression]]
    ] = {
        "is_held": lambda rows: models.ExpressionWrapper(
            models.Q(**rows.model.held_draft_fields()), output_field=models.BooleanField(),
        ),
    }

    @classmethod
    def ingest_metadata(cls, incoming: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        """Replace provider metadata while retaining trusted local facts."""

        metadata = dict(incoming)
        protected = cls.protected_local_keys()
        local = metadata.get("local")
        local = (
            {key: value for key, value in local.items() if key not in protected}
            if isinstance(local, dict) else {}
        )
        retained = (previous or {}).get("local", {})
        if isinstance(retained, dict):
            local.update({key: value for key, value in retained.items() if key in protected})
        if local:
            metadata["local"] = local
        else:
            metadata.pop("local", None)
        return metadata

    @property
    def local_metadata(self) -> dict[str, Any]:
        """Copy the local facts; older untyped provider values are not local facts."""

        local = (self.metadata or {}).get("local")
        return dict(local) if isinstance(local, dict) else {}

    @property
    def is_locally_written(self) -> bool:
        """Local delivery states distinguish authored envelopes from synced own mail."""

        return self.status not in (self.MessageStatus.SYNCED, self.MessageStatus.EDITED)

    @property
    def delivery_needs_reconciliation(self) -> bool:
        """A possibly accepted message needs reconciliation before another send."""

        return bool(self.delivery_possibly_accepted)

    @property
    def delivery_blocked(self) -> bool:
        """Whether removal, discard, or a provider identity collision prevents a send."""

        return (self.is_trashed and not self.delivery_needs_reconciliation) or bool(
            self.local_metadata.get("delivery_conflict")
        )

    @property
    def delivery_held(self) -> bool:
        """Whether this message's release time is still in the future."""

        return (
            self.status != self.MessageStatus.UNCERTAIN
            and self.scheduled_at is not None and self.scheduled_at > timezone.now()
        )

    def validate_delivery(self) -> None:
        """Require the persisted outbound envelope used by every delivery entrypoint."""

        if self.direction != self.Direction.OUTBOUND or self.channel_id is None or not self.external_id.strip():
            raise ValidationError("Delivery requires an outbound message, a channel, and a stable external id.")

    def validate_held_draft(self) -> None:
        """Require an outbound retained draft before an operator acts on it."""

        self.validate_delivery()
        if not self.is_held or self.delivery_blocked:
            raise ValidationError("Only held outbound drafts may be sent or discarded.")

    def body_text(self, *, max_bytes: int | None = None) -> str:
        """Read plain-text BODY fragments in native order, optionally byte-bounded."""

        if not self.has_access("read"):
            raise PermissionDenied("Message read access is required.")
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("Text byte limit cannot be negative.")
        text = apps.get_model("messaging", "Part").objects.plain_body_text(self)
        return text if max_bytes is None else text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")

    class Meta:
        """Django model options for the message source model."""

        abstract = True
        ordering = ("-sent_at", "sqid")
        rebac_resource_type = "messaging/message"
        constraints = (
            # Channel-scoped idempotency over a fixed digest: the ingest manager
            # looks rows up through the same MD5 expression so this index serves
            # both the constraint and the hot resync lookup.
            models.UniqueConstraint(
                models.F("channel"),
                MD5("external_id"),
                condition=~models.Q(external_id="") & models.Q(channel__isnull=False),
                name="uq_message_channel_external_id",
            ),
        )
        indexes = (
            models.Index(
                fields=("id",), condition=models.Q(status="uncertain", delivery_operator_only=False),
                name="ix_message_uncertain",
            ),
            models.Index(
                fields=("scheduled_at",), condition=models.Q(scheduled_at__isnull=False), name="ix_message_due_hold",
            ),
            models.Index(
                fields=("delivery_lease_until",), condition=models.Q(delivery_lease_until__isnull=False),
                name="ix_message_delivery_lease",
            ),
            # The exact keyset the feed orders and cursors by
            # (``MessageQuerySet.chronological_time()`` + pk tiebreak): one expression index
            # serves the hot page query verbatim, trailing id for cursor scans.
            models.Index(
                models.F("thread"),
                Coalesce("sent_at", "created_at"),
                models.F("id"),
                name="ix_message_thread_order_id",
            ),
            # Receipt scans and mark-read walk (thread, id > anchor) directly.
            models.Index(fields=("thread", "id"), name="ix_message_thread_id"),
            # In-Reply-To / References resolution is platform-wide (a reply through
            # account B must find the parent account A ingested), which the
            # channel-led unique index cannot serve — this digest probe can.
            models.Index(
                MD5("external_id"),
                name="ix_message_external_id_md5",
                condition=~models.Q(external_id=""),
            ),
        )

    def webform_submission(self) -> WebformSubmission | None:
        """Read the public-form envelope without interpreting it in consumers."""

        envelope = (self.metadata or {}).get("webform")
        if not isinstance(envelope, dict) or not isinstance(envelope.get("answers"), dict):
            return None
        email = envelope.get("unverified_submitter_email")
        return WebformSubmission(
            answers=dict(envelope["answers"]),
            unverified_submitter_email=email if isinstance(email, str) and email else None,
        )

    def content_edit_error(self) -> str | None:
        """Return why this message's body cannot be edited, or ``None`` if it can.

        Only an internally authored plain comment may be re-edited; an ingested or system message
        is an immutable record. Editability keys on ``direction == INTERNAL`` as well
        as ``COMMENT`` kind, so an ingested COMMENT-kind message (a reused-table
        social/mail row that never came from ``post_to_thread``) stays immutable. This
        is the single predicate behind both the ``update_content`` write guard and the
        ``can_edit`` projection, so the two never drift.
        """

        if self.message_type != self.MessageKind.COMMENT:
            return "Only comment messages can be edited."
        if self.direction != self.Direction.INTERNAL:
            return "Only internally authored comments can be edited."
        return None

    def delete_error(self) -> str | None:
        """Only comments may be deleted; notes and automatic messages are retained."""

        return self._retained_error("deleted")

    def trash_error(self) -> str | None:
        """Only comments may be trashed; notes and automatic messages stay on record."""

        return self._retained_error("removed")

    def _retained_error(self, verb: str) -> str | None:
        """Return why this message is a retained record that no removal verb may take."""

        if self.message_type != self.MessageKind.COMMENT:
            return f"Only comment messages can be {verb}."
        return None

    def can_trash(self, *, moderate_access: bool) -> bool:
        """Return whether a record moderator may move this comment to the trash."""

        return moderate_access and not self.is_trashed and self.trash_error() is None

    def trash(self, *, reason: str = "", recount: bool = True) -> None:
        """Trash and cancel unsent delivery; restoring never re-arms a send."""

        manager = type(self).objects.db_manager(hints={"instance": self})
        with transaction.atomic(using=manager.db):
            manager.cancel_delivery(self)
            super().trash(reason=reason)
            if recount:
                self._recount_thread()

    def restore(self) -> None:
        """Restore this message and recount its thread with it."""

        manager = type(self)._base_manager.db_manager(hints={"instance": self})
        with transaction.atomic(using=manager.db):
            super().restore()
            self._recount_thread()

    def _recount_thread(self) -> None:
        """Keep the thread's counters to its untrashed messages; bookkeeping, not access."""

        if self.thread_id is None:
            return
        thread_model = type(self).thread.field.related_model
        with system_context(reason="messaging.message.recount_thread"):
            thread = thread_model._base_manager.db_manager(hints={"instance": self}).get(pk=self.thread_id)
            type(self).objects.db_manager(hints={"instance": self})._recount_thread(thread)

    def can_change_comment(self, *, post_access: bool, moderate_access: bool, actor_id: Any) -> bool:
        """Combine record moderation with the author's continuing post access."""

        return self.sender_id is not None and (
            moderate_access or (post_access and actor_id is not None and self.created_by_id == actor_id)
        )

    def can_edit(self, *, post_access: bool, moderate_access: bool, actor_id: Any) -> bool:
        """Return whether this actor may change this comment's content."""

        return (
            self.can_change_comment(post_access=post_access, moderate_access=moderate_access, actor_id=actor_id)
            and self.content_edit_error() is None
        )

    def can_delete(self, *, post_access: bool, moderate_access: bool, actor_id: Any) -> bool:
        """Return whether this actor may remove this comment."""

        return (
            self.can_change_comment(post_access=post_access, moderate_access=moderate_access, actor_id=actor_id)
            and self.delete_error() is None
        )

    @property
    def chronological_key(self) -> tuple[datetime, int]:
        """The feed's complete position: coalesced send/create time, then PK."""

        return self.sent_at or self.created_at, self.pk

    @property
    def feed_order_key(self) -> str:
        """Opaque ASCII key whose descending comparison equals chronological order.

        Version 1 encodes UTC microseconds and a biased signed-64-bit integer PK.
        Consumers compare the complete string; they never decode public IDs or
        interpret this token as a signed pagination cursor.
        """

        at, pk = self.chronological_key
        if timezone.is_naive(at) or not isinstance(pk, int) or not -(2**63) <= pk < 2**63:
            raise ValueError("Message feed positions require an aware timestamp and signed-64-bit PK.")
        return f"1:{at.astimezone(UTC).isoformat(timespec='microseconds')}:{pk + 2**63:020d}"

    @classmethod
    def prime_reaction_groups(cls, messages: Sequence[Self], user: Any) -> None:
        """Load admitted messages' reactions and the actor's readable handles in two reads."""

        if not messages:
            return
        reaction_model = apps.get_model("messaging", "Reaction")
        handle_model = apps.get_model("parties", "Handle")
        handles = handle_model.objects.with_actor(user) if user is not None else handle_model.objects.none()
        models.prefetch_related_objects(
            list(messages),
            models.Prefetch("reactions", queryset=reaction_model._base_manager.order_by("pk")),
            models.Prefetch("reactions__handle", queryset=handles),
        )

    def reaction_groups(self, user: Any = None) -> list[MessageReactionGroup]:
        """Return this message's reactions grouped by content, with ``user``'s state.

        The chatter feed shows reactions grouped by content — each with a count, the
        reacting handles the user may read, and whether ``user`` reacted. Handles come
        from an actor-scoped relation cache (``prime_reaction_groups`` or a REBAC-scoped
        prefetch); any other handle is proved for ``user`` in one statement.
        With no user, the returned groups contain no handles.
        This is the single owner of grouping; GraphQL only projects it.
        """

        reaction_model = apps.get_model("messaging", "Reaction")
        cache = getattr(self, "_prefetched_objects_cache", None)
        if cache is not None and "reactions" in cache:
            reactions = list(self.reactions.all())
        else:
            reactions = list(reaction_model._base_manager.filter(message=self).order_by("pk"))
        actor = to_subject_ref(user) if user is not None else None

        def proved(row: Any) -> bool:
            if not reaction_model.handle.is_cached(row):
                return False
            return row.handle is None or (actor is not None and getattr(row.handle, "_rebac_actor", None) == actor)

        unproved = {row.handle_id for row in reactions if row.handle_id is not None and not proved(row)}
        readable_handles = (
            apps.get_model("parties", "Handle").objects.db_manager(self._state.db)
            .with_actor(user).filter(pk__in=unproved).in_bulk()
            if unproved and user is not None else {}
        )
        user_id = getattr(user, "pk", None)
        grouped: dict[str, list[Any]] = {}
        for reaction in reactions:
            grouped.setdefault(str(reaction.reaction), []).append(reaction)
        groups = [
            MessageReactionGroup(
                reaction=content,
                count=len(rows),
                self_reacted=user_id is not None and any(row.created_by_id == user_id for row in rows),
                handles=tuple(
                    handle for row in rows
                    if (handle := row.handle if proved(row) else readable_handles.get(row.handle_id)) is not None
                ),
            )
            for content, rows in grouped.items()
        ]
        return sorted(groups, key=lambda group: min(row.pk for row in grouped[group.reaction]))

    def threaded_record(self) -> models.Model | None:
        """Return the chatter record this message's thread is attached to, if any.

        A record chatter post lands in a private thread attached to one model row;
        walking message → thread → attachment → target lets the ``can_edit`` /
        ``can_delete`` projections ask that record for its own post access — the
        exact gate the update/delete mutations enforce.
        """

        reference = self.threaded_record_ref()
        if reference is None:
            return None
        return apps.get_model(reference.model_label)._base_manager.using(self._state.db).filter(
            pk=reference.object_id,
        ).first()

    def threaded_record_ref(self) -> RecordRef | None:
        """The chatter host's identity, without loading its document body."""

        thread = self.thread
        if thread is None:
            return None
        return thread.chatter_record_ref()

    def sender_name(self) -> str:
        """Return the actor-visible sender label, using its selected SQL value."""

        annotated = getattr(self, "_sender_name", None)
        if annotated is not None:
            return str(annotated)
        actor = self.actor() or current_actor()
        if self.sender_id is None or actor is None:
            return ""
        handle_model = apps.get_model("parties", "Handle")
        return (
            handle_model.objects.with_actor(actor)
            .with_sender_name()
            .filter(pk=self.sender_id)
            .values_list("_sender_name", flat=True)
            .first()
            or ""
        )

    def thread_title(self) -> str:
        """Return the readable thread title selected by the inbox projection."""

        annotated = getattr(self, "_thread_title", None)
        if annotated is not None:
            return str(annotated)
        actor = self.actor() or current_actor()
        if self.thread_id is None or actor is None:
            return ""
        # The caller already owns access to this materialized Message. The
        # expression independently scopes its related records, including when
        # the parent was loaded through record-gated chatter or elevated code.
        return (
            type(self)
            ._base_manager.filter(pk=self.pk)
            .annotate(_thread_title=type(self).objects.with_actor(actor).thread_title_expression())
            .values_list("_thread_title", flat=True)
            .first()
            or ""
        )

    def transport_channel(self, *, reason: str) -> Any | None:
        """Return the concrete ``messaging.Channel`` this message travelled through.

        ``channel`` targets the ``integrate.Integration`` parent row; transport and
        channel contributions live on the ``Channel`` child with the same key.
        This is a system read for delivery and ingest-time consumers; a dangling
        key raises ``DoesNotExist``.
        """

        if self.channel_id is None:
            return None
        return apps.get_model("messaging", "Channel").objects.sudo(reason=reason).get(pk=self.channel_id)

    def channel_vendor_name(self) -> str:
        """Return the readable channel vendor selected by the inbox projection."""

        annotated = getattr(self, "_channel_vendor_name", None)
        if annotated is not None:
            return str(annotated)
        actor = self.actor() or current_actor()
        if self.channel_id is None or actor is None:
            return ""
        return (
            type(self)
            ._base_manager.filter(pk=self.pk)
            .annotate(_channel_vendor_name=type(self).objects.with_actor(actor).channel_vendor_name_expression())
            .values_list("_channel_vendor_name", flat=True)
            .first()
            or ""
        )

    def authorship(self) -> tuple[str, str]:
        """The sender's subject and label, falling back to the recorded author."""

        sender = self.sender
        author = sender if sender is not None else self.created_by
        if author is None:
            return "", ""
        return str(to_subject_ref(author)), (sender.display_name or str(sender)) if sender is not None else str(author)

    def title(self) -> str:
        """Return this message's title text — its ``TITLE`` part's fragment, or ``""``.

        The single owner of the title read: prefetch-aware (a page of messages with
        ``parts__fragment`` prefetched projects titles without per-row queries), so
        the GraphQL resolver and displays never re-derive which part is the title.
        """

        annotated = getattr(self, "_title_text", None)
        if annotated is not None:
            # A list-scale read annotated the title in SQL (with_title_text) —
            # Coalesce makes "" the no-title value, so None only means unannotated.
            return str(annotated)
        cache = getattr(self, "_prefetched_objects_cache", None)
        if cache is not None and "parts" in cache:
            for part in self.parts.all():
                if part.role == Part.PartRole.TITLE:
                    fragment = part.fragment
                    return fragment.text if fragment is not None else ""
            return ""
        title_part = (
            apps.get_model("messaging", "Part")
            ._base_manager.filter(message=self, role=Part.PartRole.TITLE)
            .select_related("fragment")
            .first()
        )
        fragment = title_part.fragment if title_part is not None else None
        return fragment.text if fragment is not None else ""

    def reserve_delivery(self) -> Self | None:
        """Reserve a due draft/queue or eligible uncertain recovery under its row lock."""

        if self.pk is None:
            raise ValidationError("Cannot deliver an unsaved self.")
        using = self._state.db or router.db_for_write(type(self), instance=self)
        with system_context(reason="messaging.delivery.queue"), transaction.atomic(using=using):
            row = (
                type(self).objects.using(using).sudo(reason="messaging.delivery.queue")
                .lock_if_supported().get(pk=self.pk)
            )
            if row.status != row.MessageStatus.UNCERTAIN:
                row.validate_delivery()
            now = timezone.now()
            if (
                row.status not in (row.MessageStatus.DRAFT, row.MessageStatus.QUEUED, row.MessageStatus.UNCERTAIN)
                or row.delivery_blocked or row.delivery_held
                or (row.status == row.MessageStatus.QUEUED and row.delivery_claim is not None)
                or (row.scheduled_at is None and row.delivery_lease_until is not None
                    and row.delivery_lease_until > now)
            ):
                return None
            if row.status == row.MessageStatus.UNCERTAIN:
                if row.delivery_operator_only or not row.delivery_possibly_accepted:
                    return None
                if row.scheduled_at is not None and row.scheduled_at > now:
                    return None
                if row.delivery_lease_until is not None and row.delivery_lease_until > now:
                    return None
                # scheduled_at retains the due recovery horizon until a worker claims
                # it; a lease alongside it fences a still-pending broker submission.
                row.scheduled_at = row.scheduled_at or now
                row.delivery_lease_until = now + timedelta(seconds=DELIVERY_TASK_EXPIRES)
                type(row)._base_manager.using(using).filter(pk=row.pk).update(
                    scheduled_at=row.scheduled_at, delivery_lease_until=row.delivery_lease_until,
                )
            else:
                row.status = row.MessageStatus.QUEUED
                row.scheduled_at = None
                row.delivery_lease_until = now + timedelta(seconds=DELIVERY_TASK_EXPIRES)
                row.save(update_fields=("status", "scheduled_at", "delivery_lease_until", "updated_at"))
        self.status = row.status
        self.scheduled_at = row.scheduled_at
        self.delivery_lease_until = row.delivery_lease_until
        self.revision = row.revision
        return row

    def deliver(self) -> bool:
        """Queue this outbound message through messaging's fenced delivery owner.

        This is the consumer seam: callers compose and persist the message,
        envelope participants, and parts, then call ``message.deliver()``. The
        transport always runs through ``angee.jobs``, never in the request.
        """

        return queue_message_delivery(self)

    def acknowledge_delivery(self, *, actor: Any, expected_revision: int) -> Any:
        """Confirm an uncertain provider effect as sent without making another call."""

        return type(self).objects.resolve_uncertain_delivery(
            self, actor=actor, expected_revision=expected_revision, retry=False,
        )

    def retry_delivery(self, *, actor: Any, expected_revision: int) -> Any:
        """Retry FAILED, or explicitly accept repeat-effect risk for UNCERTAIN."""

        return type(self).objects.resolve_uncertain_delivery(
            self, actor=actor, expected_revision=expected_revision, retry=True,
        )

    def __str__(self) -> str:
        """Return a readable message label for Django displays."""

        return self.preview or f"message:{self.public_id}"

    def broadcasts_changes(self) -> bool:
        """Whether this message's changes reach the generic ``changes`` subscription.

        A message on a record-attached thread stays off the generic ``messageChanged``
        surface, whether or not the host opted in: the members of an opted-in room hold
        ``messaging/thread.reader``, not ``message.read``, so ``ChangeReadGate`` drops
        every per-message event for them — the live contract for a room is the thread's
        ``threadChanged`` (see :meth:`Thread.broadcasts_changes`). Only a message on a
        generic (non-record) thread, or one whose thread merged away, broadcasts — the
        emission mirror of ``MessageQuerySet.inbox()``.
        """

        if self.thread_id is None:
            return True
        try:
            thread = self.thread
        except ObjectDoesNotExist:
            # The thread went first in the same delete cascade (record chatter teardown);
            # a missing thread is no evidence of a generic one, so stay off the surface.
            return False
        return thread is None or not thread.is_record_attached()


class Fragment(AuditMixin, ProtectedDataModel, concrete=True):
    """A content-addressed text node shared across messages.

    Email threads re-quote the same paragraphs in every reply; a hashed shared row
    dedups that text, makes the quotation graph a cheap FK-join (two messages
    quote-link iff their parts share a Fragment), and isolates signatures (one
    repeated signature → one Fragment, excluded from search/quotation). ``kind`` is
    the secondary skip axis in the quotation builder; :attr:`Part.role` is primary.

    Because the row is content-addressed and shared — two owners quoting the same
    paragraph dedup to one row — it carries no REBAC type: a per-owner ``read`` on a
    shared row would hide the text from every owner but the first. Visibility is
    scoped instead by the owning :class:`Part`/:class:`Message` (each REBAC-gated);
    the row is reached only through a readable Part and is never enumerable on its
    own, mirroring storage's unscoped ``MimeType`` catalogue.
    """


    class FragmentKind(models.TextChoices):
        """What a fragment of text is."""

        PARAGRAPH = "paragraph", "Paragraph"
        QUOTE = "quote", "Quote"
        SIGNATURE = "signature", "Signature"
        CODE = "code", "Code"
        HEADER = "header", "Header"

    sqid_prefix = "frg_"
    text = models.TextField()
    hash = models.CharField(max_length=64, unique=True)
    kind = StateField(choices_enum=FragmentKind, default=FragmentKind.PARAGRAPH)
    search = SearchVectorField(null=True)
    """Full-text vector over ``text``, stamped once at creation by the manager.

    A content-addressed row is immutable, so no trigger or update queue is needed
    (contrast Zulip's async tsvector worker): each *unique* paragraph is indexed
    exactly once however many messages share it, which is what keeps the GIN small
    at millions of messages. ``config="simple"`` — mail is multilingual; stemming
    one language would skew the rest.
    """

    objects = FragmentManager()

    class Meta:
        """Django model options for the fragment source model."""

        abstract = True
        # No rebac_resource_type: a content-addressed shared row is unscoped
        # substrate, gated through the owning Part/Message (see the class docstring).
        indexes = (GinIndex(fields=("search",), name="ix_fragment_search"),)

    def part_count(self) -> int:
        """Return how many actor-readable parts use this fragment."""

        return int(apps.get_model("messaging", "Part").objects.fragment_uses(self).count())

    def message_count(self) -> int:
        """Return how many actor-readable messages use this fragment."""

        return int(
            apps.get_model("messaging", "Part")
            .objects.fragment_uses(self)
            .order_by()
            .values("message_id")
            .distinct()
            .count()
        )

    def __str__(self) -> str:
        """Return a truncated preview for Django displays."""

        return (self.text[:60] + "…") if len(self.text) > 60 else self.text


class Part(AuditMixin, ProtectedDataModel, concrete=True):
    """One recursive body node of a message (the MIME/JMAP part shape, one model).

    ``type``/``role`` is a genuine discriminator, not MTI: a ``multipart/*`` is a
    container; a text part references a :class:`Fragment`; a byte part references a
    ``storage.File``. Attachments are ``disposition=attachment`` + ``file``, or an
    inline document (``disposition=inline`` with a filename and a document type, as
    forwarded bills arrive); inline images are ``disposition=inline`` + ``cid``.
    """


    class Disposition(models.TextChoices):
        """How a part is presented."""

        INLINE = "inline", "Inline"
        ATTACHMENT = "attachment", "Attachment"

    class PartRole(models.TextChoices):
        """The semantic role of a part — the primary quotation/search filter axis.

        ``TITLE`` carries the message's subject (an email Subject, a post title) and
        ``HEADER`` a retained envelope header (``name`` holds the header name, the
        fragment its value) — sparse top-level rows only messages that *have* those
        facts pay for. Role lives on the use, not the content: the same fragment may
        be a paragraph in one message and a title in another.
        """

        BODY = "body", "Body"
        TITLE = "title", "Title"
        QUOTED = "quoted", "Quoted"
        SIGNATURE = "signature", "Signature"
        HEADER = "header", "Header"

    sqid_prefix = "prt_"
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="parts",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    position = models.PositiveIntegerField(default=0)
    type = models.CharField(max_length=128, default="text/plain")
    disposition = StateField(choices_enum=Disposition, default=Disposition.INLINE)
    role = StateField(choices_enum=PartRole, default=PartRole.BODY)
    cid = models.CharField(max_length=4096, blank=True, default="")
    name = models.CharField(max_length=512, blank=True, default="")
    fragment = models.ForeignKey(
        "messaging.Fragment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="parts",
    )
    file = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="message_parts",
    )

    objects = PartManager()

    class Meta:
        """Django model options for the part source model."""

        abstract = True
        ordering = ("message", "position", "sqid")
        rebac_resource_type = "messaging/part"
        constraints = (
            models.UniqueConstraint(
                fields=("message",),
                condition=models.Q(role="title"),
                name="uq_part_message_title",
            ),
        )

    def __str__(self) -> str:
        """Return the part type for Django displays."""

        return f"{self.type} ({self.role})"


class MessageEdge(AuditMixin, ProtectedDataModel, concrete=True):
    """One typed cross-message relation — the unified quote/reference graph.

    ``Message.parent`` stays the single-parent reply pointer and ``Thread`` is
    membership; this carries the M2M/derived relations. A derived *quote* edge sets
    ``fragment`` (the shared content-addressed text) and a ``confidence``; both
    direction indexes back the bulk BFS.
    """


    class EdgeKind(models.TextChoices):
        """The type of cross-message relation.

        ``quote`` is produced by the messaging quotation builder; ``mention``/
        ``crosspost``/``forward`` are produced by the ``posts`` feed overlay onto this
        shared graph (through ``MessageEdgeManager.relate``). The single-parent reply
        pointer is ``Message.parent``, not an edge. Add a value only together with
        its producer (cross-channel dedup will add ``duplicate`` with the
        annotate-both design).
        """

        QUOTE = "quote", "Quote"
        MENTION = "mention", "Mention"
        CROSSPOST = "crosspost", "Crosspost"
        FORWARD = "forward", "Forward"

    sqid_prefix = "mge_"
    src = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="edges_out",
    )
    dst = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="edges_in",
    )
    kind = StateField(choices_enum=EdgeKind, default=EdgeKind.QUOTE)
    fragment = models.ForeignKey(
        "messaging.Fragment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="edges",
    )
    confidence = models.FloatField(default=1.0)

    objects = MessageEdgeManager()

    class Meta:
        """Django model options for the message-edge source model."""

        abstract = True
        rebac_resource_type = "messaging/message_edge"
        constraints = (
            models.UniqueConstraint(
                fields=("src", "dst", "kind"),
                name="uq_message_edge_src_dst_kind",
            ),
        )
        indexes = (
            models.Index(fields=("src", "dst")),
            models.Index(fields=("dst", "src")),
        )

    def __str__(self) -> str:
        """Return a readable edge description for Django displays."""

        return f"{self.src_id} -{self.kind}-> {self.dst_id}"


class Participant(AuditMixin, ProtectedDataModel, concrete=True):
    """A Handle-keyed membership of a thread/message — the queryable recipient row.

    The raw to/cc/bcc stays in ``Message.metadata`` as the lossless source; this is
    its queryable projection, so the inbox can group/filter by participant.
    One row states one fact: a handle's role on a message, or, without a message,
    in a thread. A merge moving a handle's rows onto one already stating the same
    fact drops the moved copy (``merge_identity``).
    """

    merge_identity = (("message", "handle", "role"), ("thread", "handle", "role"))

    class ParticipantRole(models.TextChoices):
        """The RFC-5322 envelope role of a participant."""

        FROM = "from", "From"
        TO = "to", "To"
        CC = "cc", "Cc"
        BCC = "bcc", "Bcc"

    sqid_prefix = "ptp_"
    thread = models.ForeignKey(
        "messaging.Thread",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    message = models.ForeignKey(
        "messaging.Message",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    handle = models.ForeignKey(
        "parties.Handle",
        on_delete=models.CASCADE,
        related_name="participations",
    )
    role = StateField(choices_enum=ParticipantRole, default=ParticipantRole.TO)
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Django model options for the participant source model."""

        abstract = True
        ordering = ("role", "sqid")
        rebac_resource_type = "messaging/participant"
        constraints = (
            # One row per envelope fact; the write path dedupes a repeated
            # address, the constraint keeps a concurrent rebuild honest.
            models.UniqueConstraint(
                fields=("message", "handle", "role"),
                condition=models.Q(message__isnull=False),
                name="uq_participant_message_handle_role",
            ),
            models.UniqueConstraint(
                fields=("thread", "handle", "role"),
                condition=models.Q(message__isnull=True),
                name="uq_participant_thread_handle_role",
            ),
        )

    def __str__(self) -> str:
        """Return a readable participant label for Django displays."""

        return f"{self.handle_id} ({self.role})"


class Reaction(AuditMixin, ProtectedDataModel, concrete=True):
    """One attributed reaction to a message, keyed by the reactor's parties ``Handle``.

    This is the single per-actor reaction store: ``MessageManager.set_reaction``
    (reached from ``ThreadedModelMixin.message_reaction``) adds/removes/toggles a row
    per ``(message, handle, reaction)``, and ``Message.reaction_groups`` reads the rows
    back grouped by content for the chatter feed. The ``posts`` addon reuses this same
    table for public reactions (``like``/``repost`` are reaction values on the shared
    ``messaging.Message``), so there is one reaction table, not two; the rolled-up
    public counts live separately on ``posts.PostMetrics``.

    Dedup — one reaction of a given content per reactor — is enforced only for an
    *attributed* row (``handle`` set): the unique constraint is partial on
    ``handle IS NOT NULL``. A row whose ``handle`` was ``SET_NULL`` by a later
    ``Handle`` delete is de-attributed history, not a live reactor, so it falls out
    of the invariant rather than colliding (SQL treats NULLs as distinct regardless).
    A merge moving a reactor's rows onto a handle that already reacted the same way
    drops the moved copy (``merge_identity``).
    """

    merge_identity = (("message", "handle", "reaction"),)

    sqid_prefix = "rxn_"
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    handle = models.ForeignKey(
        "parties.Handle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reactions",
    )
    reaction = models.CharField(max_length=64)

    objects = ReactionManager()

    class Meta:
        """Django model options for the reaction source model."""

        abstract = True
        rebac_resource_type = "messaging/reaction"
        constraints = (
            models.UniqueConstraint(
                fields=("message", "handle", "reaction"),
                condition=models.Q(handle__isnull=False),
                name="uq_reaction_message_handle_reaction",
            ),
        )

    def __str__(self) -> str:
        """Return the reaction for Django displays."""

        return self.reaction

    @classmethod
    def clean_reaction(cls, value: Any) -> str:
        """Return ``value`` normalized into a valid stored reaction, or raise.

        The single owner of what a stored reaction value may be: null-byte scrubbed,
        whitespace-stripped, non-empty, and within the ``reaction`` field's own
        ``max_length``. Both write paths — the user-keyed toggle
        (``MessageManager.set_reaction``) and the attributed batch overlay
        (``ReactionManager.attribute``) — clean through here, so an empty or
        over-length value cannot reach the table by one path while the other guards it.
        """

        cleaned = strip_null_bytes(value or "").strip()
        if not cleaned:
            raise ValueError("Reaction is required.")
        max_length = cls.reaction.field.max_length
        if max_length is not None and len(cleaned) > max_length:
            raise ValueError("Reaction is too long.")
        return cleaned


class MessageStar(AuditMixin, ProtectedDataModel, concrete=True):
    """A user's Odoo-style star/favorite marker on a message."""


    sqid_prefix = "msr_"
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="stars",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )

    objects = MessageStarManager()

    class Meta:
        """Django model options for the message star source model."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "messaging/message_star"
        constraints = (
            models.UniqueConstraint(
                fields=("message", "user"),
                name="uq_message_star_message_user",
            ),
        )

    def __str__(self) -> str:
        """Return a readable message star label."""

        return f"{self.user_id} starred {self.message_id}"
