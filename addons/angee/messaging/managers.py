"""Managers that own the messaging write path — the channel-sync ingest.

A channel backend parses a source into neutral ``ParsedMessage`` rows; these
managers turn each into a :class:`~angee.messaging.models.Message` with its thread,
its recursive :class:`~angee.messaging.models.Part` tree (text content-addressed
into :class:`~angee.messaging.models.Fragment`\\s), its participants, and its
quotation edges. They encode the invariants a high-volume email sync depends on:

- ``(platform, external_id)`` ``update_or_create`` keys make re-sync idempotent.
- null bytes (``\\x00``) are stripped before every write (Postgres rejects them).
- thread resolution is the 4-step RFC-5322 priority under ``select_for_update``.
- denormalised counters bump with ``F()``, never read-modify-write.
- the quotation graph FK-joins on shared fragments, skipping boilerplate quoted by
  more than :data:`_BOILERPLATE_CUTOFF` messages.

The sync runs under ``system_context``; ``created_by`` is set to the channel owner.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from rebac import current_actor

from angee.base.actors import actor_user_id
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.messaging.tracking import TrackingChange

if TYPE_CHECKING:
    from angee.messaging.backends import ParsedMessage, ParsedPart
    from angee.messaging.models import Message

# A fragment quoted by more than this many messages is boilerplate (a disclaimer or
# repeated signature); quote-linking it would join the whole corpus, so skip it.
_BOILERPLATE_CUTOFF = 100

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fwd|fw|aw|sv|vs|ref|tr|rif)\s*(?:\[\d+\])?\s*:\s*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

def strip_null_bytes(value: Any) -> Any:
    """Recursively remove ``\\x00`` from strings inside str/dict/list values.

    Email bodies routinely contain null bytes, which Postgres rejects in text/JSON
    columns; stripping them on the write path keeps a large sync from hard-failing.
    """

    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {key: strip_null_bytes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_null_bytes(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_null_bytes(item) for item in value)
    return value


def normalize_subject(subject: str) -> str:
    """Strip repeated ``Re:``/``Fwd:``/… prefixes and collapse whitespace for matching."""

    text = strip_null_bytes(subject or "")
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    # Casing is preserved (only the prefix match is case-insensitive) so the
    # normalized subject still reads naturally and two subjects differing only in
    # case stay distinct conversations.
    return text.strip()


def message_subtype_options(model_label: str = "") -> tuple[dict[str, Any], ...]:
    """Return follower-selectable subtype options for ``model_label``.

    The option list is deterministic even before any message has created subtype
    rows. Existing global/model rows override labels and flags for their key.
    """

    subtype_model = apps.get_model("messaging", "MessageSubtype")
    options = subtype_model.builtin_options()
    labels = ["", strip_null_bytes(model_label or "").strip()]
    for subtype in subtype_model._base_manager.filter(model_label__in=labels, hidden=False).order_by(
        "sequence",
        "key",
        "sqid",
    ):
        options[str(subtype.key)] = {
            "key": str(subtype.key),
            "name": str(subtype.name),
            "description": str(subtype.description),
            "internal": bool(subtype.internal),
            "default": bool(subtype.default),
            "sequence": int(subtype.sequence),
        }
    return tuple(sorted(options.values(), key=lambda option: (option["sequence"], option["key"])))


def _message_search_query(term: str) -> models.Q:
    """Return the Odoo-style search predicate for one chatter search token."""

    return (
        models.Q(subject__icontains=term)
        | models.Q(preview__icontains=term)
        | models.Q(subtype__name__icontains=term)
        | models.Q(subtype__description__icontains=term)
        | models.Q(parts__name__icontains=term)
        | models.Q(parts__fragment__text__icontains=term)
        | models.Q(parts__file__filename__icontains=term)
        | models.Q(parts__file__title__icontains=term)
        | models.Q(tracking_values__field_name__icontains=term)
        | models.Q(tracking_values__field_label__icontains=term)
        | models.Q(tracking_values__old_display__icontains=term)
        | models.Q(tracking_values__new_display__icontains=term)
    )


def _message_chronological_key(message: Any) -> tuple[Any, Any]:
    """Return a message's chatter display order: sent time then pk.

    The order key is ``sent_at`` (falling back to ``created_at`` for a message
    that never carried a send time), then ``pk`` as the deterministic tiebreak —
    the single source of truth for chronological feed order, so the client renders
    the server's order verbatim instead of re-sorting.
    """

    return (message.sent_at or message.created_at, message.pk)


# The `_order_at` annotation mirrors `_message_chronological_key`'s sent-time key so
# the window/cursor filter on the exact `(sent_at, pk)` tuple the feed displays by —
# a backfilled email (older send time, newer pk) cannot skip, duplicate, or misorder
# at a page boundary. These express the keyset comparison against an `(at, pk)` anchor.
_MESSAGE_ORDER_ANNOTATION = Coalesce("sent_at", "created_at")


def _message_before(anchor: tuple[Any, Any]) -> models.Q:
    """Return rows strictly before the ``(order_at, pk)`` cursor."""

    at, pk = anchor
    return models.Q(_order_at__lt=at) | models.Q(_order_at=at, pk__lt=pk)


def _message_at_or_before(anchor: tuple[Any, Any]) -> models.Q:
    """Return rows at or before the ``(order_at, pk)`` cursor (anchor inclusive)."""

    at, pk = anchor
    return models.Q(_order_at__lt=at) | models.Q(_order_at=at, pk__lte=pk)


def _message_after(anchor: tuple[Any, Any]) -> models.Q:
    """Return rows strictly after the ``(order_at, pk)`` cursor."""

    at, pk = anchor
    return models.Q(_order_at__gt=at) | models.Q(_order_at=at, pk__gt=pk)


def _preview(body: ParsedPart | None, *, limit: int = 280) -> str:
    """Return a short text preview from the first text node of a parsed body tree."""

    if body is None:
        return ""
    if body.text and body.role not in ("quoted", "signature"):
        return body.text[:limit]
    for child in body.children:
        found = _preview(child, limit=limit)
        if found:
            return found
    return ""


# The key under which a message stores the digest of what its last sync wrote, so an
# identical re-sync is recognised as a no-op (see ``MessageManager._ingest_one``).
_SYNC_HASH_KEY = "sync_hash"


def _parsed_part_digest(part: ParsedPart | None) -> Any:
    """Return a JSON-safe digest of a parsed body node and its children."""

    if part is None:
        return None
    return {
        "type": part.type,
        "disposition": part.disposition,
        "role": part.role,
        "text": part.text,
        "name": part.name,
        "cid": part.cid,
        # Attachment bytes are hashed, not carried, so a large file does not inflate
        # the digest while a changed byte still flips it.
        "content": hashlib.sha256(part.content).hexdigest() if part.content is not None else None,
        "children": [_parsed_part_digest(child) for child in part.children],
    }


def _parsed_sync_hash(parsed: ParsedMessage, *, channel_id: Any) -> str:
    """Return a stable digest of everything an ingest of ``parsed`` would write.

    Covers the message columns, its Part tree, and its participants for the given
    channel, so an identical re-sync hashes equal and can skip the rewrite. The thread
    is excluded — a re-thread must still reconcile counters even when nothing else
    changed — and is compared separately by the caller.
    """

    payload = {
        "channel_id": str(channel_id) if channel_id is not None else None,
        "direction": parsed.direction,
        "subject": strip_null_bytes(parsed.subject),
        "sent_at": parsed.sent_at.isoformat() if parsed.sent_at is not None else None,
        "received_at": parsed.received_at.isoformat() if parsed.received_at is not None else None,
        "metadata": strip_null_bytes(parsed.metadata),
        "sender": (
            None
            if parsed.sender is None
            else {
                "platform": parsed.sender.platform,
                "value": parsed.sender.value,
                "display_name": parsed.sender.display_name,
            }
        ),
        "recipients": [
            {
                "platform": recipient.handle.platform,
                "value": recipient.handle.value,
                "display_name": recipient.handle.display_name,
                "role": recipient.role,
            }
            for recipient in parsed.recipients
        ],
        "body": _parsed_part_digest(parsed.body),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FragmentManager(AngeeManager):
    """Content-addressed text store: one row per distinct (null-stripped) text."""

    def upsert(self, *, text: str, kind: str = "paragraph", owner_id: Any = None) -> Any:
        """Get-or-create a fragment by the SHA-256 of its cleaned (null-stripped, trimmed) text."""

        text = strip_null_bytes(text).strip()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        fragment, _created = self.get_or_create(
            hash=digest,
            defaults={"text": text, "kind": kind, "created_by_id": owner_id},
        )
        return fragment


class ThreadManager(AngeeManager):
    """Owns thread resolution — the 4-step RFC-5322 priority under a row lock."""

    def resolve(
        self,
        *,
        platform: str,
        channel: Any,
        subject: str = "",
        in_reply_to: str = "",
        references: tuple[str, ...] = (),
        message_external_id: str = "",
        owner_id: Any = None,
        modality: Any = None,
        visibility: Any = None,
    ) -> Any:
        """Resolve the thread a message belongs to, creating one if needed.

        Priority: ``In-Reply-To`` → ``References`` (newest-first, i.e. right-to-left,
        resolved in one batch query) → normalised subject → a new thread. The subject
        match and the create run under ``select_for_update`` on a deterministic
        external id (``subj:<normalized>`` or ``msg:<id>``) so two concurrent batches
        resolving the same subject collide on the unique constraint and converge to
        one thread instead of double-creating.

        A message with no threading hint *and* no subject keys on ``msg:<external_id>``
        — its own one-message thread, never merged with another, because there is no
        key to merge on (collapsing keyless messages would fuse unrelated mail). The
        inbox groups such threads individually; they read as standalone conversations.

        ``modality``/``visibility`` land a *newly created* thread under a non-email
        :class:`~angee.messaging.models.Thread.Modality` /
        :class:`~angee.messaging.models.Thread.Visibility` — a public feed passes
        ``PUBLIC_THREAD``/``PUBLIC`` so the row is born public instead of being
        bulk-updated afterward. Each defaults to the private email-thread shape and is
        ignored when an existing thread is reused (an established thread keeps its own).
        """

        message_model = apps.get_model("messaging", "Message")
        if in_reply_to:
            parent = (
                message_model.objects.filter(platform=platform, external_id=in_reply_to)
                .select_related("thread")
                .first()
            )
            if parent is not None and parent.thread_id:
                return parent.thread
        if references:
            ref_map = {
                row.external_id: row
                for row in message_model.objects.filter(platform=platform, external_id__in=references).select_related(
                    "thread"
                )
            }
            for external_id in reversed(references):
                row = ref_map.get(external_id)
                if row is not None and row.thread_id:
                    return row.thread

        normalized = normalize_subject(subject)
        with transaction.atomic():
            if normalized:
                existing = (
                    self.select_for_update()
                    .filter(platform=platform, subject_normalized=normalized)
                    .order_by("-created_at")
                    .first()
                )
                if existing is not None:
                    return existing
            deterministic_id = f"subj:{normalized}" if normalized else f"msg:{message_external_id}"
            thread, _created = self.select_for_update().get_or_create(
                platform=platform,
                external_id=deterministic_id,
                defaults={
                    "channel": channel,
                    "subject": strip_null_bytes(subject),
                    "subject_normalized": normalized,
                    "modality": modality or self.model.Modality.EMAIL_THREAD,
                    "visibility": visibility or self.model.Visibility.PRIVATE,
                    "created_by_id": owner_id,
                },
            )
            return thread


class ThreadAttachmentManager(AngeeManager):
    """Owns the polymorphic edge from a model row to its chatter thread."""

    @staticmethod
    def _content_type(record: Any) -> Any:
        """Return ``record``'s content type (proxy-aware) — the polymorphic edge key."""

        return ContentType.objects.get_for_model(record, for_concrete_model=False)

    def for_record(self, record: Any, *, role: str = "chatter") -> Any | None:
        """Return the existing thread attachment for ``record`` and ``role``."""

        if record.pk is None:
            return None
        return (
            self.model._base_manager.select_related("thread")
            .filter(content_type=self._content_type(record), object_id=record.pk, role=role)
            .first()
        )

    def ensure_for_record(self, record: Any, *, role: str = "chatter", subject: str = "") -> Any:
        """Return ``record``'s attachment, creating its private chatter thread if needed.

        Both the thread and the attachment are resolved with ``get_or_create`` on a
        deterministic key (the thread on its ``record:…:role`` external id, the
        attachment on the ``(content_type, object_id, role)`` unique constraint), so
        two concurrent first-posts converge on one row instead of the second raising
        an ``IntegrityError`` — ``select_for_update`` cannot lock a row that does not
        exist yet, so a lock-then-create cannot serialise the first insert.
        """

        if record.pk is None:
            raise ValueError("Cannot attach a thread to an unsaved record.")
        content_type = self._content_type(record)
        external_id = f"record:{content_type.app_label}.{content_type.model}:{record.pk}:{role}"
        thread_model = self.model._meta.get_field("thread").related_model
        with transaction.atomic():
            thread, _created = thread_model._base_manager.get_or_create(
                platform=thread_model._meta.get_field("platform").choices_enum.OTHER,
                external_id=external_id,
                defaults={
                    "subject": strip_null_bytes(subject or str(record)),
                    "subject_normalized": normalize_subject(subject or str(record)),
                    "modality": thread_model.Modality.GROUP,
                    "visibility": thread_model.Visibility.PRIVATE,
                },
            )
            attachment, _created = self.model._base_manager.get_or_create(
                content_type=content_type,
                object_id=record.pk,
                role=role,
                defaults={
                    "thread": thread,
                    "label": strip_null_bytes(str(record)),
                },
            )
            # The thread is deterministic from (content_type, object_id, role), so an
            # existing attachment for this record+role already points at this thread;
            # prime the FK cache to keep the select_related the callers relied on.
            attachment.thread = thread
            return attachment

    def teardown_for_record(self, record: Any) -> None:
        """Delete every chatter thread attached to ``record`` and its whole subtree.

        A record's chatter thread is private to that record, so a hard delete of the
        record collects the thread graph with it — no orphaned thread survives to be
        mis-resolved when a later row reuses the primary key. Deleting each ``Thread``
        cascades its attachments, followers, activities, notifications, and
        participants; its messages FK the thread with ``SET_NULL`` (an ingested email
        message outlives a merged thread), so a private record thread's messages are
        deleted explicitly first.
        """

        if record.pk is None:
            return
        thread_ids = list(
            self.model._base_manager.filter(content_type=self._content_type(record), object_id=record.pk)
            .values_list("thread_id", flat=True)
            .distinct()
        )
        if not thread_ids:
            return
        thread_model = self.model._meta.get_field("thread").related_model
        message_model = apps.get_model("messaging", "Message")
        with transaction.atomic():
            message_model._base_manager.filter(thread_id__in=thread_ids).delete()
            thread_model._base_manager.filter(pk__in=thread_ids).delete()


class ThreadFollowerQuerySet(AngeeQuerySet[Any]):
    """Chainable read scopes for record chatter followers."""

    def for_attachment(self, attachment: Any) -> ThreadFollowerQuerySet:
        """Return followers bound to one record's chatter attachment edge."""

        return cast(ThreadFollowerQuerySet, self.filter(attachment=attachment))


class ThreadFollowerManager(AngeeManager.from_queryset(ThreadFollowerQuerySet)):  # type: ignore[misc]
    """Owns user subscriptions to model-attached chatter threads."""

    def for_record(self, record: Any, *, role: str = "chatter") -> Any:
        """Return followers for ``record`` and ``role``."""

        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return self.none()
        return self.for_attachment(attachment)

    def is_following(self, record: Any, *, user: Any = None, user_id: Any = None, role: str = "chatter") -> bool:
        """Return whether ``user`` follows ``record``'s chatter thread."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return False
        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return False
        return self.model._base_manager.filter(attachment=attachment, user_id=resolved_user_id).exists()

    def subscribe(
        self,
        record: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
        notification_policy: str = "inbox",
        subtype_keys: tuple[str, ...] = (),
    ) -> Any:
        """Ensure ``user`` follows ``record``'s chatter thread."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            raise ValueError("A user is required to follow a thread.")
        attachment = apps.get_model("messaging", "ThreadAttachment").objects.ensure_for_record(
            record,
            role=role,
            subject=_record_thread_subject(record),
        )
        follower, created = self.get_or_create(
            attachment=attachment,
            user_id=resolved_user_id,
            defaults={
                "thread": attachment.thread,
                "notification_policy": notification_policy,
                "subtype_keys": list(subtype_keys),
                "created_by_id": resolved_user_id,
            },
        )
        if not created:
            follower.thread = attachment.thread
            follower.notification_policy = notification_policy
            follower.subtype_keys = list(subtype_keys)
            follower.save(update_fields=("thread", "notification_policy", "subtype_keys", "updated_at"))
        return follower

    def unsubscribe(self, record: Any, *, user: Any = None, user_id: Any = None, role: str = "chatter") -> int:
        """Remove ``user`` from ``record``'s chatter followers."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return 0
        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return 0
        deleted, _details = self.model._base_manager.filter(attachment=attachment, user_id=resolved_user_id).delete()
        return deleted


class ThreadNotificationQuerySet(AngeeQuerySet[Any]):
    """Chainable read scopes for per-recipient notification/read state."""

    DELIVERY_ERROR_STATUSES = ("bounce", "exception")
    """Notification statuses that mean the author has a delivery error."""

    def for_attachment(self, attachment: Any) -> ThreadNotificationQuerySet:
        """Return notifications bound to one record's chatter attachment edge."""

        return cast(ThreadNotificationQuerySet, self.filter(attachment=attachment))

    def unread(self) -> ThreadNotificationQuerySet:
        """Return notifications the recipient has not yet read."""

        return cast(ThreadNotificationQuerySet, self.filter(is_read=False))

    def delivery_errors(self) -> ThreadNotificationQuerySet:
        """Return notifications whose delivery bounced or raised an exception."""

        return cast(
            ThreadNotificationQuerySet,
            self.filter(notification_status__in=self.DELIVERY_ERROR_STATUSES),
        )


class ThreadNotificationManager(AngeeManager.from_queryset(ThreadNotificationQuerySet)):  # type: ignore[misc]
    """Owns per-recipient notification/read state for record chatter messages."""

    def for_record(
        self,
        record: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
        unread_only: bool = False,
    ) -> Any:
        """Return notifications for ``user`` on ``record`` and ``role``."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return self.none()
        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return self.none()
        # Notification bookkeeping bypasses per-row REBAC: the record-level gate has
        # already authorised the whole chatter read, so scope the unread/error reads
        # with sudo and let the chainable predicates own the filters.
        queryset = self.sudo(reason="messaging.notification.for_record").for_attachment(attachment).filter(
            user_id=resolved_user_id,
        )
        if unread_only:
            queryset = queryset.unread()
        return queryset

    def unread_count_for_record(
        self,
        record: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
    ) -> int:
        """Return the unread notification count for ``user`` on ``record``."""

        return int(
            self.for_record(
                record,
                user=user,
                user_id=user_id,
                role=role,
                unread_only=True,
            ).count()
        )

    def mark_read_for_record(
        self,
        record: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
    ) -> int:
        """Mark ``user``'s unread notifications on ``record`` as read."""

        now = timezone.now()
        return int(
            self.for_record(
                record,
                user=user,
                user_id=user_id,
                role=role,
                unread_only=True,
            ).update(is_read=True, read_at=now, updated_at=now)
        )

    def needaction_for_message(self, message: Any, *, user: Any = None, user_id: Any = None) -> bool:
        """Return whether ``message`` has an unread notification for ``user``."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return False
        return (
            self.sudo(reason="messaging.notification.needaction")
            .filter(message=message, user_id=resolved_user_id)
            .unread()
            .exists()
        )

    def mark_read_for_message(self, message: Any, *, user: Any = None, user_id: Any = None) -> int:
        """Mark one message's unread notification for ``user`` as read."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return 0
        now = timezone.now()
        return int(
            self.sudo(reason="messaging.notification.mark_read_message")
            .filter(message=message, user_id=resolved_user_id)
            .unread()
            .update(is_read=True, read_at=now, updated_at=now)
        )

    def error_count_for_record(
        self,
        record: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
    ) -> int:
        """Return the delivery-error count authored by ``user`` on ``record``."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            return 0
        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return 0
        return int(
            self.sudo(reason="messaging.notification.error_count")
            .for_attachment(attachment)
            .filter(created_by_id=resolved_user_id)
            .delivery_errors()
            .count()
        )

    def mark_failed(
        self,
        notification: Any,
        *,
        status: str = "exception",
        failure_type: str = "unknown",
        failure_reason: str = "",
    ) -> Any:
        """Mark one notification as a delivery failure."""

        if status not in ThreadNotificationQuerySet.DELIVERY_ERROR_STATUSES:
            raise ValueError("Delivery failure status must be 'bounce' or 'exception'.")
        cleaned_failure_type = strip_null_bytes(failure_type or "unknown")
        cleaned_failure_reason = strip_null_bytes(failure_reason or "")
        now = timezone.now()
        self.model._base_manager.filter(pk=notification.pk).update(
            notification_status=status,
            failure_type=cleaned_failure_type,
            failure_reason=cleaned_failure_reason,
            updated_at=now,
        )
        notification.notification_status = status
        notification.failure_type = cleaned_failure_type
        notification.failure_reason = cleaned_failure_reason
        notification.updated_at = now
        return notification

    def mark_failed_for_message(
        self,
        message: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        status: str = "exception",
        failure_type: str = "unknown",
        failure_reason: str = "",
    ) -> Any:
        """Mark one message notification for ``user`` as failed."""

        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            raise ValueError("A user is required to mark a notification failed.")
        notification = self.model._base_manager.get(message=message, user_id=resolved_user_id)
        return self.mark_failed(
            notification,
            status=status,
            failure_type=failure_type,
            failure_reason=failure_reason,
        )

    def fanout_for_message(
        self,
        message: Any,
        *,
        attachment: Any | None = None,
        subtype_key: str = "",
        owner_id: Any = None,
        recipient_user_ids: tuple[Any, ...] = (),
    ) -> int:
        """Create follower and explicit-recipient notifications for one message."""

        if message.thread_id is None:
            return 0
        follower_model = apps.get_model("messaging", "ThreadFollower")
        followers = follower_model._base_manager.filter(thread_id=message.thread_id)
        if attachment is not None:
            followers = followers.filter(attachment=attachment)
        now = timezone.now()
        # One existence read instead of a get_or_create round-trip per recipient: the
        # message is freshly posted, so almost every recipient needs a new row, which
        # a single ``bulk_create`` inserts (``ignore_conflicts`` covers a racing
        # fanout). The rare pre-existing row is re-pointed in place.
        existing = {row.user_id: row for row in self.model._base_manager.filter(message=message)}
        new_rows: list[Any] = []
        queued: set[Any] = set()

        def _is_author(recipient_id: Any) -> bool:
            return owner_id is not None and str(recipient_id) == str(owner_id)

        policy_enum = follower_model.NotificationPolicy
        type_enum = self.model.NotificationType
        for follower in followers.select_related("attachment"):
            policy = str(follower.notification_policy)
            if policy == policy_enum.MUTED:
                continue
            subtype_keys = tuple(str(key) for key in (follower.subtype_keys or ()))
            if subtype_keys and subtype_key and subtype_key not in subtype_keys:
                continue
            if subtype_keys and not subtype_key:
                continue
            notification_type = type_enum.EMAIL if policy == policy_enum.EMAIL else type_enum.INBOX
            existing_row = existing.get(follower.user_id)
            if existing_row is not None:
                existing_row.thread_id = message.thread_id
                existing_row.attachment = follower.attachment
                existing_row.follower = follower
                existing_row.notification_type = notification_type
                existing_row.save(
                    update_fields=("thread", "attachment", "follower", "notification_type", "updated_at")
                )
                continue
            if follower.user_id in queued:
                continue
            queued.add(follower.user_id)
            is_author = _is_author(follower.user_id)
            new_rows.append(
                self.model(
                    message=message,
                    user_id=follower.user_id,
                    thread_id=message.thread_id,
                    attachment=follower.attachment,
                    follower=follower,
                    notification_type=notification_type,
                    notification_status=self.model.NotificationStatus.READY,
                    is_read=is_author,
                    read_at=now if is_author else None,
                    created_by_id=owner_id,
                )
            )
        for user_id in _normalise_user_ids(recipient_user_ids):
            existing_row = existing.get(user_id)
            if existing_row is not None:
                if existing_row.attachment_id is None and attachment is not None:
                    existing_row.attachment = attachment
                    existing_row.save(update_fields=("attachment", "updated_at"))
                continue
            if user_id in queued:
                continue
            queued.add(user_id)
            is_author = _is_author(user_id)
            new_rows.append(
                self.model(
                    message=message,
                    user_id=user_id,
                    thread_id=message.thread_id,
                    attachment=attachment,
                    notification_type=self.model.NotificationType.INBOX,
                    notification_status=self.model.NotificationStatus.READY,
                    is_read=is_author,
                    read_at=now if is_author else None,
                    created_by_id=owner_id,
                )
            )
        self.model._base_manager.bulk_create(new_rows, ignore_conflicts=True)
        return len(new_rows)


class ThreadActivityQuerySet(AngeeQuerySet[Any]):
    """Chainable read scopes for scheduled chatter activities."""

    def open(self) -> ThreadActivityQuerySet:
        """Return activities still to do (not yet done or cancelled)."""

        return cast(ThreadActivityQuerySet, self.filter(status=self.model.ActivityStatus.TODO))


class ThreadActivityManager(AngeeManager.from_queryset(ThreadActivityQuerySet)):  # type: ignore[misc]
    """Owns scheduled activities attached to model chatter threads."""

    def for_record(self, record: Any, *, role: str = "chatter", include_done: bool = True) -> Any:
        """Return activities for ``record`` and ``role``."""

        attachment = _record_attachment(record, role=role)
        if attachment is None:
            return self.none()
        queryset = self.filter(attachment=attachment)
        if not include_done:
            queryset = queryset.open()
        return queryset

    def schedule(
        self,
        record: Any,
        *,
        user: Any = None,
        user_id: Any = None,
        role: str = "chatter",
        summary: str,
        note: str = "",
        due_date: Any = None,
        activity_type: str = "todo",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Create a scheduled activity for ``record``."""

        summary = strip_null_bytes(summary or "").strip()
        if not summary:
            raise ValueError("Activity summary cannot be empty.")
        resolved_user_id = _resolve_user_id(user=user, user_id=user_id)
        if resolved_user_id is None:
            raise ValueError("An assigned user is required for an activity.")
        owner_id = actor_user_id(current_actor())
        attachment = apps.get_model("messaging", "ThreadAttachment").objects.ensure_for_record(
            record,
            role=role,
            subject=_record_thread_subject(record),
        )
        return self.create(
            thread=attachment.thread,
            attachment=attachment,
            user_id=resolved_user_id,
            activity_type=strip_null_bytes(activity_type or "todo"),
            summary=summary,
            note=strip_null_bytes(note or ""),
            due_date=due_date or timezone.localdate(),
            metadata=metadata or {},
            created_by_id=owner_id,
        )

    def complete(self, activity: Any, *, feedback: str = "", post_message: bool = True) -> Any:
        """Mark an activity done and optionally log that completion to the thread."""

        if activity.status == self.model.ActivityStatus.DONE:
            return activity
        owner_id = actor_user_id(current_actor())
        feedback = strip_null_bytes(feedback or "").strip()
        activity.status = self.model.ActivityStatus.DONE
        activity.completed_at = timezone.now()
        activity.feedback = feedback
        activity.save(update_fields=("status", "completed_at", "feedback", "updated_at"))
        if post_message:
            body = activity.completion_message()
            if feedback:
                body = f"{body}\n\n{feedback}"
            model_class = activity.attachment.content_type.model_class()
            message_model = apps.get_model("messaging", "Message")
            message_model.objects.post_to_thread(
                activity.thread,
                body=body,
                subject=activity.thread.subject,
                owner_id=owner_id,
                attachment=activity.attachment,
                message_type=message_model.MessageKind.AUTO_COMMENT,
                subtype_key="activity_done",
                subtype_model_label=model_class._meta.label if model_class is not None else "",
            )
        return activity

    def cancel(self, activity: Any) -> Any:
        """Cancel an activity without posting a completion message."""

        if activity.status == self.model.ActivityStatus.CANCELED:
            return activity
        activity.status = self.model.ActivityStatus.CANCELED
        activity.completed_at = timezone.now()
        activity.save(update_fields=("status", "completed_at", "updated_at"))
        return activity


def _record_attachment(record: Any, *, role: str = "chatter") -> Any | None:
    """Return the chatter attachment edge for ``record`` via its owning manager.

    The resolution logic (content-type lookup, ``select_related`` on the thread,
    unscoped read) lives once on :meth:`ThreadAttachmentManager.for_record`; the
    follower/notification/activity managers resolve their bookkeeping edge through
    that owner rather than each re-deriving it.
    """

    return apps.get_model("messaging", "ThreadAttachment").objects.for_record(record, role=role)


def _record_thread_subject(record: Any) -> str:
    """Return the subject a record declares for its chatter thread."""

    subject = getattr(record, "message_thread_subject", None)
    return str(subject()) if callable(subject) else str(record)


def _resolve_user_id(*, user: Any = None, user_id: Any = None) -> Any | None:
    """Resolve an explicit user/user id, falling back to the ambient actor."""

    if user_id is not None:
        return user_id
    if user is not None:
        if getattr(user, "is_authenticated", True) is False:
            return None
        return getattr(user, "pk", user)
    return actor_user_id(current_actor())


def _normalise_user_ids(user_ids: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return unique user ids while preserving caller order."""

    seen: set[str] = set()
    unique: list[Any] = []
    for user_id in user_ids:
        if user_id is None:
            continue
        key = str(user_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(user_id)
    return tuple(unique)


def _file_mime_type(file: Any) -> str:
    """Return the MIME type string for a storage file part."""

    mime = getattr(file, "mime_type", None)
    value = getattr(mime, "mime_type", "")
    return value or "application/octet-stream"


def _message_subtype(
    *,
    subtype_key: str,
    model_label: str = "",
    owner_id: Any = None,
) -> Any | None:
    """Return/create the subtype row that classifies a chatter message."""

    subtype_key = strip_null_bytes(subtype_key or "").strip()
    if not subtype_key:
        return None
    model_label = strip_null_bytes(model_label or "").strip()
    subtype_model = apps.get_model("messaging", "MessageSubtype")
    name, description = subtype_model.builtin_default(subtype_key) or (
        _humanize_key(subtype_key),
        _humanize_key(subtype_key),
    )
    subtype, _created = subtype_model.objects.get_or_create(
        model_label=model_label,
        key=subtype_key,
        defaults={
            "name": name,
            "description": description,
            "created_by_id": owner_id,
        },
    )
    return subtype


def _humanize_key(value: str) -> str:
    """Return a human label from a subtype key."""

    return value.replace("_", " ").replace("-", " ").strip().capitalize() or "Message"


def _reaction_handle_for_user(user: Any) -> Any:
    """Return the stable parties handle used to attribute a user's reaction."""

    if user is None or getattr(user, "pk", None) is None:
        raise ValueError("Reaction author is required.")
    handle_model = apps.get_model("parties", "Handle")
    email = strip_null_bytes(getattr(user, "email", "") or "").strip()
    username = strip_null_bytes(user.get_username() if hasattr(user, "get_username") else getattr(user, "username", ""))
    value = email or username or str(user.pk)
    platform = handle_model.Platform.EMAIL if email else handle_model.Platform.OTHER
    display_name = strip_null_bytes(
        user.get_full_name() if hasattr(user, "get_full_name") else "",
    ).strip() or username or value
    return handle_model.objects.upsert(
        platform=platform,
        value=value,
        owner_id=user.pk,
        display_name=display_name,
        is_own=True,
        metadata={"user_id": str(user.pk)},
    )


def _tracking_preview(tracking_values: tuple[TrackingChange | dict[str, Any], ...]) -> str:
    """Return a compact preview for a tracking-only message."""

    if not tracking_values:
        return ""
    first = _normalise_tracking_value(tracking_values[0], 0)
    return f"{first['field_label']}: {first['old_display']} -> {first['new_display']}"[:280]


def _normalise_tracking_value(raw: TrackingChange | dict[str, Any], position: int) -> dict[str, Any]:
    """Return one tracking row in the persisted shape, at ``position``.

    Accepts the structured :class:`~angee.messaging.tracking.TrackingChange` the field
    tracker emits, or a raw dict from an external ``message_track``/``message_log`` caller.
    Either way it JSON-safes the stored values and null-strips the display strings; the
    row shape itself is authored once on ``TrackingChange``.
    """

    if isinstance(raw, TrackingChange):
        field_name = raw.field_name
        field_label = raw.field_label
        field_type = raw.field_type
        old_value = _json_safe(raw.old_value)
        new_value = _json_safe(raw.new_value)
        old_display = strip_null_bytes(str(raw.old_display))
        new_display = strip_null_bytes(str(raw.new_display))
        metadata: dict[str, Any] = {}
    else:
        field_name = strip_null_bytes(str(raw.get("field_name") or raw.get("field") or "")).strip()
        if not field_name:
            raise ValueError("Tracking value field_name is required.")
        field_label = strip_null_bytes(str(raw.get("field_label") or raw.get("label") or field_name)).strip()
        field_type = strip_null_bytes(str(raw.get("field_type") or raw.get("type") or ""))
        old_value = _json_safe(raw.get("old_value"))
        new_value = _json_safe(raw.get("new_value"))
        old_display = strip_null_bytes(str(raw.get("old_display") or _display_value(old_value)))
        new_display = strip_null_bytes(str(raw.get("new_display") or _display_value(new_value)))
        raw_metadata = raw.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    return {
        "position": position,
        "field_name": field_name,
        "field_label": field_label,
        "field_type": field_type,
        "old_value": old_value,
        "new_value": new_value,
        "old_display": old_display,
        "new_display": new_display,
        "metadata": strip_null_bytes(metadata),
    }


def _json_safe(value: Any) -> Any:
    """Return a JSONField-safe representation of a tracked value."""

    value = strip_null_bytes(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return str(value)


def _display_value(value: Any) -> str:
    """Return the display text for a tracked value."""

    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_display_value(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}: {_display_value(item)}" for key, item in value.items())
    return str(value)


class MessageStarManager(AngeeManager):
    """Owns per-user Odoo-style starred message state."""

    def is_starred(self, message: Any, *, user: Any | None) -> bool:
        """Return whether ``user`` has starred ``message``."""

        user_id = getattr(user, "pk", None)
        if user_id is None:
            return False
        if (
            hasattr(message, "_prefetched_objects_cache")
            and "stars" in message._prefetched_objects_cache
        ):
            return any(star.user_id == user_id for star in message.stars.all())
        return self.model._base_manager.filter(message=message, user_id=user_id).exists()

    def set_starred(self, message: Any, *, user: Any, starred: bool | None = None) -> bool:
        """Set or toggle ``user``'s star on ``message`` and return the new state."""

        user_id = getattr(user, "pk", None)
        if user_id is None:
            raise ValueError("A user is required to star a message.")
        with transaction.atomic():
            message = type(message)._base_manager.select_for_update().get(pk=message.pk)
            star = (
                self.model._base_manager.select_for_update()
                .filter(message=message, user_id=user_id)
                .first()
            )
            next_starred = not bool(star) if starred is None else bool(starred)
            if next_starred and star is None:
                self.model._base_manager.create(
                    message=message,
                    user_id=user_id,
                    created_by_id=user_id,
                )
            elif not next_starred and star is not None:
                star.delete()
        return next_starred

    def unstar_all(self, *, user: Any) -> int:
        """Remove all stars owned by ``user``."""

        user_id = getattr(user, "pk", None)
        if user_id is None:
            return 0
        deleted, _details = self.model._base_manager.filter(user_id=user_id).delete()
        return int(deleted)


class ReactionManager(AngeeManager):
    """Owns the attributed-reaction write — the row shape for a (message, handle, reaction).

    ``MessageManager.set_reaction`` is the user-keyed chatter toggle; this is the
    distinct attributed write the ``social`` feed overlay lands for each external
    reactor. The row shape (fields + ``created_by`` default) lives here with the table
    owner so a producer batches through this owner instead of hand-rolling its own
    ``get_or_create``.
    """

    def attribute(self, reactions: Any, *, owner_id: Any = None) -> int:
        """Land attributed reactions in one insert; return how many rows were built.

        ``reactions`` is an iterable of ``(message, handle, reaction)`` triples. One
        ``bulk_create`` inserts the batch, idempotent on the partial unique
        ``(message, handle, reaction)`` constraint via ``ignore_conflicts`` — so a
        re-sync re-landing the same reactions is a no-op — and every row carries the
        cleaned reaction content and the ``created_by`` (the field-backed REBAC owner).
        """

        rows = [
            self.model(
                message=message,
                handle=handle,
                reaction=self.model.clean_reaction(reaction),
                created_by_id=owner_id,
            )
            for message, handle, reaction in reactions
        ]
        if rows:
            self.bulk_create(rows, ignore_conflicts=True)
        return len(rows)


class MessageQuerySet(AngeeQuerySet[Any]):
    """Chainable read scopes for chatter/ingest messages."""

    def for_thread(self, thread: Any) -> MessageQuerySet:
        """Return messages belonging to one thread."""

        return cast(MessageQuerySet, self.filter(thread=thread))

    def visible_in_chatter(self) -> MessageQuerySet:
        """Return messages a record's chatter feed shows (drop user notifications)."""

        return cast(
            MessageQuerySet,
            self.exclude(message_type=self.model.MessageKind.USER_NOTIFICATION),
        )

    def searching(self, term: str) -> MessageQuerySet:
        """Return messages matching one Odoo-style chatter search token."""

        return cast(MessageQuerySet, self.filter(_message_search_query(term)))


class MessageManager(AngeeManager.from_queryset(MessageQuerySet)):  # type: ignore[misc]
    """Owns the message ingest write path (idempotent, null-safe, F()-counted)."""

    def for_record(
        self,
        record: Any,
        *,
        role: str = "chatter",
        search: str = "",
        limit: int = 50,
        before: Any | None = None,
        after: Any | None = None,
        around: Any | None = None,
    ) -> tuple[list[Any], int]:
        """Return fetched chatter messages for a record, optionally search-filtered."""

        attachment = apps.get_model("messaging", "ThreadAttachment").objects.for_record(record, role=role)
        if attachment is None:
            return [], 0
        limit = max(1, min(int(limit or 50), 200))
        # Chatter reads bypass per-row REBAC: the record-level gate already authorised
        # the whole feed, so scope with sudo and compose the chainable read predicates.
        queryset = (
            self.sudo(reason="messaging.message.for_record")
            .for_thread(attachment.thread)
            .visible_in_chatter()
            .select_related("thread", "subtype", "sender", "channel", "parent", "parent__subtype")
            .prefetch_related("parts__fragment", "parts__file", "tracking_values", "reactions__handle", "stars")
            .annotate(_order_at=_MESSAGE_ORDER_ANNOTATION)
        )
        search = strip_null_bytes(search or "").strip()
        for term in (item for item in _WS_RE.split(search) if item):
            queryset = queryset.searching(term)
        queryset = queryset.distinct()
        count = int(queryset.count())
        # Window and cursor on the same `(sent_at, pk)` key the feed displays by, then
        # return the page chronological ascending so the client renders it verbatim.
        ascending = ("_order_at", "pk")
        descending = ("-_order_at", "-pk")
        if around not in (None, ""):
            anchor = self._record_message_anchor(queryset, around)
            if anchor is None:
                return [], count
            before_limit = max(1, limit // 2)
            after_limit = max(0, limit - before_limit)
            page = [
                *queryset.filter(_message_at_or_before(anchor)).order_by(*descending)[:before_limit],
                *queryset.filter(_message_after(anchor)).order_by(*ascending)[:after_limit],
            ]
        elif before not in (None, ""):
            anchor = self._record_message_anchor(queryset, before)
            if anchor is None:
                return [], count
            page = list(queryset.filter(_message_before(anchor)).order_by(*descending)[:limit])
        elif after not in (None, ""):
            anchor = self._record_message_anchor(queryset, after)
            if anchor is None:
                return [], count
            page = list(queryset.filter(_message_after(anchor)).order_by(*ascending)[:limit])
        else:
            page = list(queryset.order_by(*descending)[:limit])
        return sorted(page, key=_message_chronological_key), count

    def _record_message_anchor(self, queryset: Any, value: Any) -> tuple[Any, Any] | None:
        """Resolve a public message id to its ``(order_at, pk)`` cursor in the window."""

        return (
            queryset.filter(**self.model.public_id_lookup(str(value)))
            .values_list("_order_at", "pk")
            .first()
        )

    def post_to_thread(
        self,
        thread: Any,
        *,
        body: str,
        subject: str = "",
        owner_id: Any = None,
        attachment: Any | None = None,
        attachments: tuple[Any, ...] = (),
        message_type: Message.MessageKind | None = None,
        subtype_key: str = "comment",
        subtype_model_label: str = "",
        parent: Any | None = None,
        tracking_values: tuple[TrackingChange | dict[str, Any], ...] = (),
        recipient_user_ids: tuple[Any, ...] = (),
    ) -> Any:
        """Create an internal user-authored message in ``thread`` and bump thread counters.

        ``message_type`` defaults to :attr:`Message.MessageKind.COMMENT`; the enum is
        the single source of truth for the stored kind, so ``None`` resolves to it here.
        """

        body = strip_null_bytes(body or "").strip()
        subject = strip_null_bytes(subject or thread.subject)
        tracking_rows = tuple(_normalise_tracking_value(value, index) for index, value in enumerate(tracking_values))
        if not body and not attachments and not tracking_rows:
            raise ValueError("Message body, attachment, or tracking value is required.")
        if parent is not None and parent.thread_id != thread.pk:
            raise ValueError("Parent message does not belong to this thread.")
        part_model = apps.get_model("messaging", "Part")
        fragment_model = apps.get_model("messaging", "Fragment")
        notification_model = apps.get_model("messaging", "ThreadNotification")
        tracking_model = apps.get_model("messaging", "TrackingValue")
        sent_at = timezone.now()
        with transaction.atomic():
            subtype = _message_subtype(
                subtype_key=subtype_key,
                model_label=subtype_model_label,
                owner_id=owner_id,
            )
            message = self.create(
                thread=thread,
                platform=thread.platform,
                direction=self.model.Direction.INTERNAL,
                status=self.model.MessageStatus.SENT,
                message_type=strip_null_bytes(message_type or self.model.MessageKind.COMMENT),
                subtype=subtype,
                parent=parent,
                subject=subject,
                preview=body[:280] if body else _tracking_preview(tracking_values),
                sent_at=sent_at,
                created_by_id=owner_id,
            )
            position = 0
            if body:
                fragment = fragment_model.objects.upsert(
                    text=body, kind=fragment_model.FragmentKind.PARAGRAPH, owner_id=owner_id
                )
                part_model.objects.create(
                    message=message,
                    position=position,
                    type="text/plain",
                    disposition=part_model.Disposition.INLINE,
                    role=part_model.PartRole.BODY,
                    fragment=fragment,
                    created_by_id=owner_id,
                )
                position += 1
            for file in attachments:
                part_model.objects.create(
                    message=message,
                    position=position,
                    type=_file_mime_type(file),
                    disposition=part_model.Disposition.ATTACHMENT,
                    role=part_model.PartRole.BODY,
                    name=getattr(file, "filename", "") or "attachment",
                    file=file,
                    created_by_id=owner_id,
                )
                position += 1
            for row in tracking_rows:
                tracking_model.objects.create(
                    message=message,
                    created_by_id=owner_id,
                    **row,
                )
            notification_model.objects.fanout_for_message(
                message,
                attachment=attachment,
                subtype_key=subtype.key if subtype is not None else subtype_key,
                owner_id=owner_id,
                recipient_user_ids=recipient_user_ids,
            )
            self._bump_thread(type(thread), thread.pk, sent_at)
        thread.message_count = (thread.message_count or 0) + 1
        thread.last_message_at = sent_at
        return message

    def set_reaction(self, message: Any, *, reaction: str, action: str = "toggle", user: Any) -> Any:
        """Add, remove, or toggle the current user's reaction on ``message``."""

        reaction_model = apps.get_model("messaging", "Reaction")
        reaction = reaction_model.clean_reaction(reaction)
        action = strip_null_bytes(action or "toggle").strip().lower()
        if action not in {"add", "remove", "toggle"}:
            raise ValueError("Reaction action must be add, remove, or toggle.")
        handle = _reaction_handle_for_user(user)
        with transaction.atomic():
            message = type(message)._base_manager.select_for_update().get(pk=message.pk)
            queryset = reaction_model._base_manager.select_for_update().filter(
                message=message,
                handle=handle,
                reaction=reaction,
            )
            exists = queryset.exists()
            should_add = action == "add" or (action == "toggle" and not exists)
            if should_add and not exists:
                reaction_model._base_manager.create(
                    message=message,
                    handle=handle,
                    reaction=reaction,
                    created_by_id=user.pk,
                )
            elif action == "remove" or (action == "toggle" and exists):
                queryset.delete()
        return message

    def update_content(self, message: Any, *, body: str, owner_id: Any = None) -> Any:
        """Update a user-authored comment body, preserving Odoo's edit guardrails."""

        body = strip_null_bytes(body or "").strip()
        if not body:
            raise ValueError("Message body is required.")
        part_model = apps.get_model("messaging", "Part")
        fragment_model = apps.get_model("messaging", "Fragment")
        with transaction.atomic():
            message = type(message)._base_manager.select_for_update().get(pk=message.pk)
            edit_error = message.content_edit_error()
            if edit_error is not None:
                raise ValueError(edit_error)
            fragment = fragment_model.objects.upsert(
                text=body, kind=fragment_model.FragmentKind.PARAGRAPH, owner_id=owner_id
            )
            text_parts = list(
                part_model._base_manager.select_for_update()
                .filter(message=message, role=part_model.PartRole.BODY, fragment__isnull=False)
                .order_by("position", "pk")
            )
            if text_parts:
                body_part = text_parts[0]
                body_part.fragment = fragment
                body_part.type = "text/plain"
                body_part.disposition = part_model.Disposition.INLINE
                body_part.name = ""
                body_part.save(update_fields=("fragment", "type", "disposition", "name", "updated_at"))
                part_model._base_manager.filter(pk__in=[part.pk for part in text_parts[1:]]).delete()
            else:
                part_model._base_manager.filter(message=message).update(position=models.F("position") + 1)
                part_model._base_manager.create(
                    message=message,
                    position=0,
                    type="text/plain",
                    disposition=part_model.Disposition.INLINE,
                    role=part_model.PartRole.BODY,
                    fragment=fragment,
                    created_by_id=owner_id,
                )
            metadata = dict(message.metadata or {})
            metadata["edited_at"] = timezone.now().isoformat()
            if owner_id is not None:
                metadata["edited_by_id"] = owner_id
            message.preview = body[:280]
            message.status = self.model.MessageStatus.EDITED
            message.metadata = metadata
            message.save(update_fields=("preview", "status", "metadata", "updated_at"))
        return message

    def unlink_from_thread(self, message: Any, *, thread: Any) -> Any:
        """Delete ``message`` from ``thread`` and repair thread denormalisations."""

        with transaction.atomic():
            message = type(message)._base_manager.select_for_update().get(pk=message.pk)
            if message.thread_id != thread.pk:
                raise ValueError("Message does not belong to this thread.")
            thread_model = type(thread)
            thread = thread_model._base_manager.select_for_update().get(pk=thread.pk)
            message.delete()
            self._recount_thread(thread)
        return thread

    def _recount_thread(self, thread: Any) -> None:
        """Recompute a thread's denormalised counters from its surviving messages.

        Shared by the two paths where a thread *loses* a message: a delete
        (``unlink_from_thread``) and a re-threading re-sync (``_ingest_one``). An
        ``F()`` delta cannot repair a subtraction whose true total is unknown, so the
        losing thread is recounted by aggregate. Call with the locked ``thread`` row.
        """

        summary = self.model._base_manager.filter(thread=thread).aggregate(
            count=models.Count("pk"),
            last_sent_at=models.Max("sent_at"),
        )
        thread.message_count = int(summary["count"] or 0)
        thread.last_message_at = summary["last_sent_at"]
        thread.save(update_fields=("message_count", "last_message_at", "updated_at"))

    def _bump_thread(self, thread_model: Any, thread_id: Any, sent_at: Any) -> None:
        """Increment a thread's message count and monotonically advance its last-message time.

        ``message_count`` rides an ``F()`` delta (never read-modify-write) and
        ``last_message_at`` advances via ``Greatest``/``Coalesce`` so out-of-order
        ingest never regresses it. ``updated_at`` is stamped to the current time
        because an ``.update()`` bypasses the ``auto_now`` save hook — the same
        stamp :meth:`_recount_thread` applies via ``save`` — so the row's modified
        time never lags the counters it just changed. It is the *current* time, never
        the (possibly null, possibly backfilled) ``sent_at``. Shared by a freshly
        created message and by the thread that *gains* a re-threaded one.
        """

        updates: dict[str, Any] = {
            "message_count": models.F("message_count") + 1,
            "updated_at": timezone.now(),
        }
        if sent_at is not None:
            updates["last_message_at"] = Greatest(
                Coalesce(models.F("last_message_at"), sent_at),
                sent_at,
            )
        thread_model._base_manager.filter(pk=thread_id).update(**updates)

    def ingest(
        self,
        parsed_messages: list[ParsedMessage],
        *,
        channel: Any,
        owner_id: Any = None,
        modality: Any = None,
        visibility: Any = None,
        message_kind: Any = None,
        quote_edges: bool = True,
    ) -> list[Any]:
        """Upsert each parsed message into a thread with its parts/participants/edges.

        Returns the landed :class:`~angee.messaging.models.Message` rows (a caller
        wanting the count takes ``len(...)``) so an overlay — a public-feed engagement
        pass — reuses the rows this write already resolved instead of re-querying them
        by ``(platform, external_id)``.

        Idempotent on ``(platform, external_id)``; null bytes stripped; thread
        counters bumped with ``F()``. ``modality``/``visibility`` land each resolved
        thread under a non-email :class:`~angee.messaging.models.Thread.Modality` /
        :class:`~angee.messaging.models.Thread.Visibility` (a public feed passes
        ``PUBLIC_THREAD``/``PUBLIC``); each defaults to the private email-thread shape.
        ``message_kind`` is the :class:`~angee.messaging.models.Message.MessageKind` each
        message lands under (this manager owns writing the column) — it defaults to
        ``EMAIL``, and a public-feed producer passes ``COMMENT`` so a public post is not
        mislabelled email. ``quote_edges`` runs the RFC-5322 quotation builder — email's
        shared-fragment graph — and defaults on; a non-email producer whose short shared
        text would otherwise mint spurious ``quote`` edges passes ``quote_edges=False``.
        """

        owner_id = owner_id if owner_id is not None else channel.owner_id
        message_kind = message_kind or self.model.MessageKind.EMAIL
        thread_model = apps.get_model("messaging", "Thread")
        ingested: list[Any] = []
        for parsed in parsed_messages:
            if not parsed.external_id:
                continue
            with transaction.atomic():
                ingested.append(
                    self._ingest_one(
                        parsed,
                        channel=channel,
                        owner_id=owner_id,
                        thread_model=thread_model,
                        modality=modality,
                        visibility=visibility,
                        message_kind=message_kind,
                    )
                )
        # Quotation runs after the whole batch lands, so a message quoting a later
        # one in the same batch still links (an inline pass would miss it). It is the
        # email graph, so a non-email producer skips it via ``quote_edges=False``.
        if quote_edges:
            edges = apps.get_model("messaging", "MessageEdge").objects
            for message in ingested:
                edges.create_for_message(message)
        return ingested

    def _ingest_one(
        self,
        parsed: ParsedMessage,
        *,
        channel: Any,
        owner_id: Any,
        thread_model: Any,
        modality: Any = None,
        visibility: Any = None,
        message_kind: Any = None,
    ) -> Any:
        handle_model = apps.get_model("parties", "Handle")
        thread = thread_model.objects.resolve(
            platform=parsed.platform,
            channel=channel,
            subject=parsed.subject,
            in_reply_to=parsed.in_reply_to,
            references=parsed.references,
            message_external_id=parsed.external_id,
            owner_id=owner_id,
            modality=modality,
            visibility=visibility,
        )
        sender = None
        if parsed.sender is not None:
            sender = handle_model.objects.upsert(
                platform=parsed.sender.platform,
                value=parsed.sender.value,
                owner_id=owner_id,
                display_name=parsed.sender.display_name,
            )
        # Capture the message's prior state before update_or_create moves it: the prior
        # thread (a re-sync that re-resolves to a different thread must reconcile both
        # threads' counters below, and update_or_create reports only created/updated)
        # and the digest its last sync stored (an identical re-sync is a no-op).
        prior = (
            self.model._base_manager.filter(platform=parsed.platform, external_id=parsed.external_id)
            .values("pk", "thread_id", "metadata")
            .first()
        )
        content_hash = _parsed_sync_hash(parsed, channel_id=channel.pk)
        if (
            prior is not None
            and prior["thread_id"] == thread.pk
            and (prior["metadata"] or {}).get(_SYNC_HASH_KEY) == content_hash
        ):
            # Idempotent re-sync into the same thread with identical content: nothing to
            # write. Skipping the save() avoids a new HistoryMixin row, and skipping the
            # part rebuild avoids churning Part primary keys / re-upserting Fragments.
            return self.model._base_manager.get(pk=prior["pk"])
        metadata = {**strip_null_bytes(parsed.metadata), _SYNC_HASH_KEY: content_hash}
        message, created = self.update_or_create(
            platform=parsed.platform,
            external_id=parsed.external_id,
            defaults={
                "thread": thread,
                "channel": channel,
                "sender": sender,
                "direction": parsed.direction,
                "status": self.model.MessageStatus.SYNCED,
                "message_type": message_kind or self.model.MessageKind.EMAIL,
                "subject": strip_null_bytes(parsed.subject),
                "preview": strip_null_bytes(_preview(parsed.body)),
                "sent_at": parsed.sent_at,
                "received_at": parsed.received_at,
                "metadata": metadata,
                "created_by_id": owner_id,
            },
        )
        message.parts.all().delete()
        if parsed.body is not None:
            self._build_parts(message, parsed.body, parent=None, position=0, owner_id=owner_id)
        self._write_participants(message, thread, parsed, sender, owner_id)
        # Reconcile the denormalised thread counters. The winning thread gains the
        # message whenever it is a fresh row or a re-sync re-resolved an existing message
        # onto a *different* thread (e.g. a References parent that only just landed). The
        # losing thread is recounted from its survivors — but only when there was one:
        # the prior thread may be NULL (its thread was deleted, ``SET_NULL``-ing the
        # message), in which case the winner still gains the message and there is no
        # loser to recount. Gating the winner's bump on ``created`` alone dropped exactly
        # that NULL-prior re-home; an idempotent re-sync into the same thread is a no-op.
        thread_changed = prior is not None and prior["thread_id"] != thread.pk
        if thread_changed and prior["thread_id"] is not None:
            losing_thread = thread_model._base_manager.select_for_update().get(pk=prior["thread_id"])
            self._recount_thread(losing_thread)
        if created or thread_changed:
            self._bump_thread(thread_model, thread.pk, parsed.sent_at)
        return message

    def _build_parts(self, message: Any, parsed: ParsedPart, *, parent: Any, position: int, owner_id: Any) -> None:
        part_model = apps.get_model("messaging", "Part")
        fragment_model = apps.get_model("messaging", "Fragment")
        file_ref = None
        fragment = None
        if parsed.content is not None:
            file_ref = self._ingest_file(parsed, owner_id)
        elif parsed.text and not parsed.children:
            part_role = part_model.PartRole
            fragment_kind = fragment_model.FragmentKind
            fragment = fragment_model.objects.upsert(
                text=parsed.text,
                kind=(
                    fragment_kind.SIGNATURE
                    if parsed.role == part_role.SIGNATURE
                    else fragment_kind.QUOTE
                    if parsed.role == part_role.QUOTED
                    else fragment_kind.PARAGRAPH
                ),
                owner_id=owner_id,
            )
        part = part_model.objects.create(
            message=message,
            parent=parent,
            position=position,
            type=parsed.type,
            disposition=parsed.disposition,
            role=parsed.role,
            cid=parsed.cid,
            name=parsed.name,
            fragment=fragment,
            file=file_ref,
            created_by_id=owner_id,
        )
        for index, child in enumerate(parsed.children):
            self._build_parts(message, child, parent=part, position=index, owner_id=owner_id)

    def _ingest_file(self, parsed: ParsedPart, owner_id: Any) -> Any:
        """Persist attachment bytes through the storage File owner; returns the File or None.

        Delegates to ``File.objects.ingest_bytes`` — the storage owner's
        server-side byte intake (draft → write → finalize) — so the attachment
        lands content-addressed and ``Part.file`` resolves. The owner stamps the
        file's ``created_by`` so the channel owner can read its own attachments.
        """

        if parsed.content is None:
            return None
        file_model = apps.get_model("storage", "File")
        return file_model.objects.ingest_bytes(
            parsed.content,
            filename=parsed.name or "attachment.bin",
            owner_id=owner_id,
        )

    def _write_participants(self, message: Any, thread: Any, parsed: ParsedMessage, sender: Any, owner_id: Any) -> None:
        participant_model = apps.get_model("messaging", "Participant")
        handle_model = apps.get_model("parties", "Handle")
        participant_model.objects.filter(message=message).delete()
        if sender is not None:
            participant_model.objects.create(
                message=message,
                thread=thread,
                handle=sender,
                role=participant_model.ParticipantRole.FROM,
                created_by_id=owner_id,
            )
        for recipient in parsed.recipients:
            handle = handle_model.objects.upsert(
                platform=recipient.handle.platform,
                value=recipient.handle.value,
                owner_id=owner_id,
                display_name=recipient.handle.display_name,
            )
            participant_model.objects.create(
                message=message, thread=thread, handle=handle, role=recipient.role, created_by_id=owner_id
            )


class PartQuerySet(AngeeQuerySet[Any]):
    """Chainable read scopes for message body parts."""

    def attachments(self) -> PartQuerySet:
        """Return parts that carry a stored file — a message's attachment parts."""

        return cast(PartQuerySet, self.filter(file__isnull=False))


class PartManager(AngeeManager.from_queryset(PartQuerySet)):  # type: ignore[misc]
    """Owns the recursive body-part rows; reads compose the ``PartQuerySet`` scopes."""


class MessageEdgeManager(AngeeManager):
    """Owns the cross-message graph — derived quote edges from shared fragments."""

    def _edge_fields(self, *, owner_id: Any, fragment: Any = None, confidence: float = 1.0) -> dict[str, Any]:
        """Return the non-key columns of one edge row — the write shape shared by
        :meth:`relate` and the batched quotation builder, so the edge field set and the
        ``created_by`` default live once here with the table owner rather than in each
        caller. ``fragment`` accepts a row or its id.
        """

        return {
            "fragment_id": getattr(fragment, "pk", fragment),
            "confidence": confidence,
            "created_by_id": owner_id,
        }

    def relate(
        self,
        src: Any,
        dst: Any,
        *,
        kind: Any,
        owner_id: Any,
        fragment: Any = None,
        confidence: float = 1.0,
    ) -> Any:
        """Write one typed edge from ``src`` to ``dst``, idempotent on the (src, dst, kind) key.

        The single edge-write entry point on the table owner: a social producer relating
        two messages (mention/crosspost/forward) writes through this one shape instead of
        its own ``get_or_create``, and the batched quotation builder lands the same
        :meth:`_edge_fields` columns. ``src``/``dst``/``fragment`` accept a row or its id;
        returns the edge, creating it only when the (src, dst, kind) triple is new.
        """

        edge, _created = self.get_or_create(
            src_id=getattr(src, "pk", src),
            dst_id=getattr(dst, "pk", dst),
            kind=kind,
            defaults=self._edge_fields(owner_id=owner_id, fragment=fragment, confidence=confidence),
        )
        return edge

    def create_for_message(self, message: Any) -> int:
        """Write quote edges from ``message`` to others sharing a non-boilerplate fragment.

        Skips fragments quoted by more than :data:`_BOILERPLATE_CUTOFF` messages;
        edge direction runs from the earlier message to the later one.
        """

        part_model = apps.get_model("messaging", "Part")
        part_role = part_model.PartRole
        fragment_ids = list(
            part_model.objects.filter(message=message, fragment__isnull=False)
            .exclude(role__in=(part_role.QUOTED, part_role.SIGNATURE))
            .values_list("fragment_id", flat=True)
            .distinct()
        )
        if not fragment_ids:
            return 0
        # One pass over every sharing part: pull each sharer's message id *and*
        # sent_at together, so edge direction needs no per-pair lookup. Excluding
        # this message's own parts leaves only the others to link.
        sharers_by_fragment: dict[Any, dict[Any, Any]] = {}
        for fragment_id, other_id, other_sent_at in (
            part_model.objects.filter(fragment_id__in=fragment_ids)
            .exclude(message_id=message.pk)
            .values_list("fragment_id", "message_id", "message__sent_at")
        ):
            sharers_by_fragment.setdefault(fragment_id, {})[other_id] = other_sent_at
        # Collapse to one directed edge per message pair — the first fragment that
        # links a pair wins, matching the get_or_create this replaces. Boilerplate (a
        # disclaimer/signature quoted by the whole corpus) is skipped past the cutoff
        # so linking it does not join everything.
        fragment_by_pair: dict[tuple[Any, Any], Any] = {}
        for fragment_id, others in sharers_by_fragment.items():
            if len(others) > _BOILERPLATE_CUTOFF:
                continue
            for other_id, other_sent_at in others.items():
                pair = self._direction(message.pk, message.sent_at, other_id, other_sent_at)
                fragment_by_pair.setdefault(pair, fragment_id)
        if not fragment_by_pair:
            return 0
        kind = self.model.EdgeKind.QUOTE
        # One existence read replaces the per-pair get_or_create: every candidate edge
        # touches this message, so read the quote edges on it once, then bulk-insert the
        # missing pairs under one transaction (``ignore_conflicts`` covers a concurrent
        # run, so a re-ingest that re-derives the same edges stays a no-op).
        with transaction.atomic():
            existing_pairs = set(
                self.filter(kind=kind)
                .filter(models.Q(src_id=message.pk) | models.Q(dst_id=message.pk))
                .values_list("src_id", "dst_id")
            )
            new_edges = [
                self.model(
                    src_id=src_id,
                    dst_id=dst_id,
                    kind=kind,
                    **self._edge_fields(owner_id=message.created_by_id, fragment=fragment_id),
                )
                for (src_id, dst_id), fragment_id in fragment_by_pair.items()
                if (src_id, dst_id) not in existing_pairs
            ]
            self.bulk_create(new_edges, ignore_conflicts=True)
        return len(new_edges)

    def _direction(self, a_id: Any, a_sent_at: Any, b_id: Any, b_sent_at: Any) -> tuple[Any, Any]:
        """Order an edge from the earlier message to the later one (by sent_at, then pk).

        Both sent_at values are already in hand from the single sharers query, so
        direction is a pure comparison — no extra lookup.
        """

        if a_sent_at and b_sent_at and a_sent_at != b_sent_at:
            return (a_id, b_id) if a_sent_at < b_sent_at else (b_id, a_id)
        return (a_id, b_id) if a_id < b_id else (b_id, a_id)
