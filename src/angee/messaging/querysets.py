"""Native selected-set scopes and projections for messaging rows."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, time
from functools import reduce
from itertools import batched
from operator import or_
from typing import TYPE_CHECKING, Any, Self, TypedDict
from zoneinfo import ZoneInfo

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, router, transaction
from django.db.models.functions import MD5, Coalesce
from django.db.models.query import ModelIterable
from django.utils import timezone
from rebac import (
    current_actor,
    system_context,
)
from rebac.relation_loading import relation_actor

from angee.base.fields.implementation import ImplClassField
from angee.base.models.trash import TrashQuerySet
from angee.base.pagination import InvalidKeysetCursor, KeysetOrder, KeysetPage
from angee.base.querysets import MergeableQuerySet
from angee.base.serialization import strip_null_bytes
from angee.iam.models.ownership import OwnerQuerySet
from angee.iam.permissions import actor_user_id
from angee.iam.querysets import ProtectedQuerySet
from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.models import IntegrationLifecycle, IntegrationQuerySet
from angee.messaging.expressions import AttachedFileSkipReason
from angee.messaging.inbox import MessageInbox
from angee.parties.managers import Signing
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations

    from angee.messaging.models import (
        Channel,
        Message,
        Part,
        Thread,
        ThreadFollower,
        ThreadNotification,
    )


_SIGNING_TEXT_BATCH = 2000
"""Signature fragments whose texts one query loads for signature mining."""


AUTOMATED_MAIL_HEADERS: Mapping[str, models.Q] = {
    "auto-submitted": ~models.Q(fragment__text__iexact="no"),
    "list-id": models.Q(),
    "list-unsubscribe": models.Q(),
    "precedence": models.Q(fragment__text__iregex=r"^\s*(bulk|list|junk)\s*$"),
}

"""Header parts whose value predicates mark automated mail."""

_WS_RE = re.compile(r"\s+")


class ChannelQuerySet[ChannelT: Channel](IntegrationQuerySet[ChannelT]):
    """Chainable scopes over messaging channels and their pairing attempts."""

    def outbound_email(self) -> Self:
        """Connected channels whose declared backend offers configured email delivery."""
        field = self.model._meta.get_field("backend_class")
        assert isinstance(field, ImplClassField)
        keys = [key for key in field.registered_keys() if field.resolve_class(key).outbound_email_configured()]
        return self.filter(backend_class__in=keys, lifecycle=IntegrationLifecycle.CONNECTED)

    def for_backend(self, backend_class: str) -> Self:
        """Constrain an existing channel selection to its declared backend."""
        return self.filter(backend_class=backend_class)

    def require_backend_target(self, public_id: str, *, backend_class: str) -> ChannelT:
        """Resolve an explicit import target and reject backend mismatches uniformly."""
        field = self.model._meta.get_field("backend_class")
        assert isinstance(field, ImplClassField)
        backend = field.resolve_class(backend_class)
        channel = self.system_context(reason="messaging.channel.confirm_target").for_backend(backend_class).filter(
            **self.model.public_id_lookup(public_id),
        ).first()
        if channel is None:
            raise ValidationError({"target": f"No {backend.label} channel {public_id!r}."})
        return channel

    def published_webforms(self, slug: str) -> Self:
        """Published webform channels for anonymous schema/submission admission."""
        return self.for_backend("webform").filter(slug=slug, is_published=True)

    def unfinished_pairing(self, user: Any, backend_class: str) -> Self:
        """Return the user's empty, never-claimed, non-paused channels, newest first.

        "Claimed" is the durable identity under the live implementation's
        ``state_identity_key``. A pairing report's ``own_id`` is diagnostic — a
        duplicate rejection or logout keeps it after releasing the claim — so
        it does not disqualify reuse. Message membership is the message owner's
        ``for_channel`` predicate; callers run elevated so it sees every message.
        """

        field = self.model.live_implementation_field()
        if field is None:
            raise ValueError("Unfinished pairing requires a live channel backend.")
        impl_class = field.resolve_class(backend_class)
        if not issubclass(impl_class, LiveBridgeImpl):
            raise ValueError("Unfinished pairing requires a live channel backend.")
        identity_key = impl_class.state_identity_key
        identity = f"subscription_state__{identity_key}"
        claimed = (
            models.Q(subscription_state__has_key=identity_key)
            & ~models.Q(**{identity: ""})
            & ~models.Q(**{identity: None})
        )
        message_model = apps.get_model("messaging", "Message")
        return (
            self.filter(
                ~models.Exists(message_model.objects.for_channel(models.OuterRef("pk"))),
                owner_id=user.pk,
                backend_class=backend_class,
            )
            .exclude(lifecycle=IntegrationLifecycle.PAUSED)
            .exclude(claimed)
            .order_by("-created_at", "-pk")
        )


def _external_id_annotated(queryset: Any) -> Any:
    """Annotate the ``MD5(external_id)`` digest the identity indexes are built on."""

    return queryset.annotate(_eid_digest=MD5("external_id"))


def _message_search_query(term: str) -> models.Q:
    """Return the Odoo-style search predicate for one chatter search token.

    Titles need no arm of their own: a ``TITLE`` part's text is fragment text like
    any body paragraph, so ``parts__fragment__text`` covers subjects too. The
    substring arms serve the record-scoped chatter search (bounded by the record
    gate); the corpus-wide fast path is :meth:`MessageQuerySet.searching_fulltext`.
    """

    return (
        models.Q(preview__icontains=term)
        | models.Q(subtype__name__icontains=term)
        | models.Q(subtype__description__icontains=term)
        | models.Q(parts__name__icontains=term)
        | models.Q(parts__fragment__text__icontains=term)
        | models.Q(parts__file__filename__icontains=term)
        | models.Q(parts__file__title__icontains=term)
    )


_MESSAGE_ORDER = KeysetOrder("_order_at")


class _MessageChronology(TypedDict):
    """The hydrated timestamp used by record transcript windows and cursors."""

    _order_at: datetime


class ThreadQuerySet[ThreadT: Thread](
    MergeableQuerySet[ThreadT], OwnerQuerySet[ThreadT], AngeeHistoryQuerySet[ThreadT],
):
    """Chainable read scopes for message threads."""

    def name_untitled(self, names: Mapping[Any, str], *, created_by_id: Any = None) -> int:
        """Title threads that have none from source names; never rename one.

        ``names`` maps thread pks to names. Each row is re-read under its lock, so
        a title set concurrently (a racing ingest) always wins. Returns the number
        of threads named.
        """

        fragment_model = apps.get_model("messaging", "Fragment")
        named = 0
        for pk, name in names.items():
            text = str(name or "").strip()
            if not text:
                continue
            with transaction.atomic(using=self.db):
                thread = (
                    self.sudo(reason="messaging.thread.name_untitled")
                    .lock_if_supported()
                    .filter(pk=pk, title__isnull=True)
                    .first()
                )
                if thread is None:
                    continue
                thread.title_id = fragment_model.objects.db_manager(self.db).upsert(
                    text=text, created_by_id=created_by_id,
                ).pk
                thread.save(update_fields=["title"])
                named += 1
        return named


    def inbox(self) -> Self:
        """Return channel/inbox threads — those not attached to a record.

        A thread bound to a model row through a ``ThreadAttachment`` is record
        chatter; it is reachable only through the record-scoped ``record_thread``
        payload (gated on the parent record's read) and must never surface in the
        owner-scoped generic ``threads`` list, aggregate, or by-pk lookup.
        """

        # A structural anti-join inside the already authorized inbox queryset: the
        # unscoped base manager supplies every redirect group without opening a
        # separately audited system read. Equality on the survivor id is hashed once.
        chatter = self.model._base_manager.filter(attachments__role="chatter").survivor_ids()
        attached = chatter.filter(survivor=self.survivor_id(ref=models.OuterRef))
        return self.alias(_record_attached=models.Exists(attached)).filter(_record_attached=False)

    def for_channel(self, channel: Any) -> Self:
        """Return the threads that belong to ``channel`` — the purge-scope predicate.

        A channel's threads FK the shared ``integrate.Integration`` parent through the
        channel's Integration pk, so ``filter(channel=channel)`` resolves against that
        shared FK. The single owner of the thread channel-scope predicate: the teardown
        delete and the delete-preview count both read it, so they cannot drift.
        """

        return self.filter(channel=channel)

    def conversation_labels(self) -> dict[Any, str]:
        """Label each thread for the actor: its title, else the people in it.

        Status feeds use their category, never a title inferred from authors.
        An untitled direct thread names its counterpart; an untitled group names
        up to two people and counts the rest ("Anna, Tim +4"). People are the
        actor-readable senders of inbound messages other than the actor's own
        identity, most active first: outbound messages are the account holder's,
        whichever handle sent them. A thread with no known people labels as
        ``""`` so clients apply their own translated fallback. Evaluate on a
        bounded page of threads; each call costs a fixed number of queries.
        """

        rows = list(self.order_by().select_related("title").only("pk", "modality", "title__text"))
        labels = {thread.pk: thread.display_title for thread in rows}
        untitled = {thread.pk: thread.modality for thread in rows if not labels[thread.pk]}
        actor = self.actor() or current_actor()
        people: dict[Any, list[str]] = {pk: [] for pk in untitled}
        if untitled and actor is not None:
            handles = (
                apps.get_model("parties", "Handle")
                .objects.with_actor(actor)
                .with_sender_name()
                .excluding_identity_of(actor_user_id(actor))
            )
            participant_model = apps.get_model("messaging", "Participant")
            activity = (
                participant_model.objects.with_actor(actor)
                .scoped_for_aggregate()
                .filter(
                    thread_id__in=list(untitled),
                    role=participant_model.ParticipantRole.FROM,
                    message__direction=apps.get_model("messaging", "Message").Direction.INBOUND,
                    handle_id__in=models.Subquery(handles.values("pk")),
                )
                .order_by()
                .values("thread_id", "handle_id")
                .annotate(_turns=models.Count("pk"))
                .order_by("thread_id", "-_turns", "handle_id")
            )
            ranked = [(row["thread_id"], row["handle_id"]) for row in activity]
            names = dict(handles.filter(pk__in={handle for _, handle in ranked}).values_list("pk", "_sender_name"))
            for thread_id, handle_id in ranked:
                name = str(names.get(handle_id) or "").strip()
                if name and name not in people[thread_id]:
                    people[thread_id].append(name)
        for pk, modality in untitled.items():
            present = people[pk]
            if modality == self.model.Modality.DIRECT and present:
                labels[pk] = present[0]
            elif modality == self.model.Modality.GROUP and present:
                shown = ", ".join(present[:2])
                labels[pk] = f"{shown} +{len(present) - 2}" if len(present) > 2 else shown
        return labels


class ThreadFollowerQuerySet[ThreadFollowerT: ThreadFollower](ProtectedQuerySet[ThreadFollowerT]):
    """Chainable read scopes for record chatter followers."""

    @staticmethod
    def user_q(user: Any) -> models.Q:
        """Select a user's existing person follows without creating or loading the person."""

        user_id = _resolve_user_id(user=user)
        if user_id is None:
            return models.Q(pk__in=[])
        people = apps.get_model("parties", "Person")._base_manager.filter(user_id=user_id)
        return models.Q(party_id__in=people.values("pk"))

    @staticmethod
    def _preference_order(thread_id: Any) -> tuple[models.Case, str]:
        return models.Case(models.When(thread_id=thread_id, then=0), default=1), "pk"

    def for_thread(self, thread: Any) -> Self:
        return self.filter(thread_id__in=thread.member_threads().values("pk"))

    def effective_for_thread(self, thread: Any) -> Self:
        """Current thread preferences win; otherwise retain the earliest physical follow.

        Which physical follow represents a party is a fact of the rows, not an
        access decision: the choice reads the table unscoped, and the caller's
        own scope (an actor, a sudo) applies to the rows it returns.
        """

        members = thread.member_threads().values("pk")
        physical = self.model._base_manager.using(self.db).filter(thread_id__in=members)
        kept = (
            physical.filter(party_id=models.OuterRef("party_id"))
            .order_by(*self._preference_order(thread.canonical().pk))
            .values("pk")[:1]
        )
        return self.filter(thread_id__in=members, pk=models.Subquery(kept))

    def for_attachment(self, attachment: Any) -> Self:
        return self.effective_for_thread(attachment.thread)


class NotificationPointerIterable(ModelIterable):
    """Prime the narrowed inbox projection once per materialized Django page."""

    def __iter__(self) -> Iterator[Any]:
        rows = list(super().__iter__())
        if not isinstance(self.queryset, ThreadNotificationQuerySet):
            raise ImproperlyConfigured("NotificationPointerIterable requires ThreadNotificationQuerySet.")
        self.queryset.prime_message_pointers(rows, relation_actor(self.queryset))
        yield from rows


class ThreadNotificationQuerySet[ThreadNotificationT: ThreadNotification](ProtectedQuerySet[ThreadNotificationT]):
    """Chainable read scopes for per-recipient delivery rows."""

    DELIVERY_ERROR_STATUSES = ("bounce", "exception")
    """Notification statuses that mean the author has a delivery error."""

    def prime_message_pointers(self, rows: Sequence[Any], actor: Any) -> None:
        """Load narrow message context, checking read once per distinct attached record.

        Elevated bookkeeping loads never authorize a message. Only a current
        recipient who can still read its threaded record gets the pointer.
        Record-less inbox rows retain their delivery state but have no record pointer.
        """

        from angee.messaging.models import ThreadedModelMixin

        for row in rows:
            row._inbox_message_pointer = None
        if not rows or actor is None:
            return
        recipient_id = actor_user_id(actor)
        rows = [row for row in rows if str(row.user_id) == str(recipient_id)]
        if not rows:
            return
        with system_context(reason="messaging.notification.message_pointers"):
            messages = {
                message.pk: message
                for message in apps.get_model("messaging", "Message")
                ._base_manager.filter(pk__in={row.message_id for row in rows})
                .select_related("subtype")
            }
            attachments = list(
                apps.get_model("messaging", "ThreadAttachment")
                ._base_manager.filter(
                    models.Q(pk__in={row.attachment_id for row in rows if row.attachment_id is not None})
                    | models.Q(thread_id__in={row.thread_id for row in rows}, role="chatter")
                )
                .select_related("thread").prefetch_related("target", "content_type")
            )
        thread_model = apps.get_model("messaging", "Thread")
        threads = thread_model.objects.system_context(reason="messaging.notification.redirect_groups")
        redirect_groups: models.QuerySet[Thread] = threads.none()
        for survivor_id in {edge.thread.merged_into_id for edge in attachments if edge.thread.merged_into_id}:
            redirect_groups |= threads.redirect_group(thread_model(pk=survivor_id))
        redirects = {
            edge.thread_id: edge
            for edge in apps.get_model("messaging", "ThreadAttachment")._base_manager.filter(
                thread_id__in=redirect_groups,
                role="chatter",
            ).prefetch_related("target", "content_type")
        }
        def current(edge: Any) -> Any:
            return redirects.get(edge.thread.merged_into_id, edge)
        by_id = {attachment.pk: current(attachment) for attachment in attachments}
        by_thread = {
            attachment.thread_id: current(attachment) for attachment in attachments if attachment.role == "chatter"
        }
        allowed: dict[tuple[Any, Any], bool] = {}
        for row in rows:
            attachment = by_id.get(row.attachment_id) if row.attachment_id is not None else by_thread.get(row.thread_id)
            if attachment is None:
                continue
            key = (attachment.content_type_id, attachment.object_id)
            if key not in allowed:
                target = attachment.target
                allowed[key] = isinstance(target, ThreadedModelMixin) and target.thread_reader_allowed(actor)
            message = messages.get(row.message_id)
            if allowed[key] and message is not None and not message.is_trashed:
                message._inbox_attachment = attachment
                row._inbox_message_pointer = message
        senders = {
            sender.pk: sender
            for sender in apps.get_model("parties", "Handle")
            .objects.with_actor(actor)
            .filter(
                pk__in={row._inbox_message_pointer.sender_id for row in rows if row._inbox_message_pointer is not None}
            )
        }
        for row in rows:
            message = row._inbox_message_pointer
            if message is not None:
                message._meta.get_field("sender").set_cached_value(message, senders.get(message.sender_id))


    def with_message_pointers(self) -> Self:
        """Batch the recipient's narrow message context after filtering and pagination."""

        queryset = self.all()
        queryset._iterable_class = NotificationPointerIterable
        return queryset

    def for_attachment(self, attachment: Any) -> Self:
        """Return notifications bound to one record's chatter attachment edge."""

        return self.filter(
            thread_id__in=attachment.thread.member_threads().values("pk"),
        )

    def delivery_errors(self) -> Self:
        """Return notifications whose delivery bounced or raised an exception."""

        return self.filter(notification_status__in=self.DELIVERY_ERROR_STATUSES)


def _resolve_user_id(*, user: Any = None, user_id: Any = None) -> Any | None:
    """Resolve an explicit user/user id, falling back to the ambient actor."""

    if user_id is not None:
        return user_id
    if user is not None:
        if getattr(user, "is_authenticated", True) is False:
            return None
        return getattr(user, "pk", user)
    return actor_user_id(current_actor())


class MessageQuerySet[MessageT: Message](
    MergeableQuerySet[MessageT], TrashQuerySet[MessageT], ProtectedQuerySet[MessageT]
):
    """Chainable read scopes for chatter/ingest messages."""

    def record_scope(
        self,
        record: Any,
        *,
        role: str = "chatter",
        search: str = "",
        message_types: tuple[str, ...] = (),
        trashed: bool = False,
    ) -> MessageQuerySet[WithAnnotations[MessageT, _MessageChronology]]:
        """The parent-authorized message population, without presentation hydration."""

        rows = self.using(self._db or router.db_for_read(self.model, instance=record))  # type: ignore[attr-defined]  # django-stubs post3 omits QuerySet._db; post4 adds it.
        using = rows.db
        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        attachment = attachment_model.objects.db_manager(using, hints={"instance": record}).for_record(
            record, role=role,
        )
        if attachment is None:
            return rows.none().annotate(_order_at=self.chronological_time())
        # Chatter reads bypass per-row REBAC: the record-level gate already authorised
        # the whole feed, so scope with sudo and compose the chainable read predicates.
        queryset = (
            rows.sudo(reason="messaging.message.for_record")
            .for_thread(attachment.thread)
            .filter(is_trashed=trashed)
            .annotate(_order_at=MessageQuerySet.chronological_time())
        )
        kinds = {
            strip_null_bytes(value or "").strip().lower()
            for value in message_types
            if strip_null_bytes(value or "").strip()
        }
        allowed_kinds = {value for value, _label in self.model.MessageKind.choices}
        if not kinds.issubset(allowed_kinds):
            raise ValueError("Message type filter contains an unsupported value.")
        if kinds:
            queryset = queryset.filter(message_type__in=kinds)
        search = strip_null_bytes(search or "").strip()
        for term in (item for item in _WS_RE.split(search) if item):
            queryset = queryset.searching(term)
        return queryset.distinct()


    def record_window(
        self,
        record: Any,
        *,
        role: str = "chatter",
        search: str = "",
        limit: int = 50,
        before: Any | None = None,
        after: Any | None = None,
        around: Any | None = None,
        message_types: tuple[str, ...] = (),
        trashed: bool = False,
    ) -> tuple[MessageQuerySet[WithAnnotations[MessageT, _MessageChronology]], int]:
        """Select bounded chatter identities while leaving the rows composable.

        The same parent scope owns filtering, count and chronological cuts.
        ID probes never hydrate content; the reader projects the resulting rows.
        """

        limit = max(1, min(int(limit or 50), 200))
        queryset = self.record_scope(
            record, role=role, search=search, message_types=message_types, trashed=trashed,
        )
        count = int(queryset.count())
        ascending = ("_order_at", "pk")
        descending = ("-_order_at", "-pk")
        if around not in (None, ""):
            anchor = self._record_message_anchor(queryset, around)
            if anchor is None:
                return queryset.none(), count
            before_limit = max(1, limit // 2)
            after_limit = max(0, limit - before_limit)
            ids = [
                *queryset.filter(_MESSAGE_ORDER.before(anchor, inclusive=True))
                .order_by(*descending).values_list("pk", flat=True)[:before_limit],
                *queryset.filter(_MESSAGE_ORDER.after(anchor))
                .order_by(*ascending).values_list("pk", flat=True)[:after_limit],
            ]
        else:
            window = queryset
            if before not in (None, "") or after not in (None, ""):
                anchor = self._record_message_anchor(queryset, before if before not in (None, "") else after)
                if anchor is None:
                    return queryset.none(), count
                boundary = (_MESSAGE_ORDER.before(anchor) if before not in (None, "")
                            else _MESSAGE_ORDER.after(anchor))
                window = window.filter(boundary)
            order = ascending if after not in (None, "") and before in (None, "") else descending
            ids = list(window.order_by(*order).values_list("pk", flat=True)[:limit])
        return queryset.filter(pk__in=ids).order_by(*ascending), count

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
        message_types: tuple[str, ...] = (),
        trashed: bool = False,
    ) -> tuple[list[Any], int]:
        """Materialize the native record window for existing non-GraphQL readers."""

        rows, count = self.record_window(
            record, role=role, search=search, limit=limit, before=before, after=after,
            around=around, message_types=message_types, trashed=trashed,
        )
        return list(rows), count


    def record_window_continues(
        self, record: Any, messages: list[Any], *, newer: bool,
        role: str = "chatter", search: str = "", message_types: tuple[str, ...] = (),
    ) -> bool:
        """Whether a record-gated window has matching rows beyond its chronological edge."""

        if not messages:
            return False
        edge = messages[-1] if newer else messages[0]
        scope = self.record_scope(record, role=role, search=search, message_types=message_types)
        boundary = (_MESSAGE_ORDER.after(edge.chronological_key) if newer
                    else _MESSAGE_ORDER.before(edge.chronological_key))
        return bool(scope.filter(boundary).exists())


    def _record_message_anchor(self, queryset: Any, value: Any) -> tuple[Any, Any] | None:
        """Resolve a public message id to its ``(order_at, pk)`` cursor in the window."""

        return queryset.filter(**self.model.public_id_lookup(str(value))).values_list("_order_at", "pk").first()


    def trash_targets(self) -> Self:
        """Keep record chatter off the shared trash verbs; its record moderates it."""

        return self.inbox()

    def due_deliveries(self, *, now: datetime) -> Self:
        """Due unclaimed holds/retries and lost dispatches, never an uncertain effect."""

        due = models.Q(scheduled_at__lte=now, **self.model.held_draft_fields()) | models.Q(
            scheduled_at__lte=now,
            status="queued",
            delivery_lease_until__isnull=False,
        )
        lost = models.Q(status="queued", delivery_lease_until__lte=now)
        return (
            self.untrashed()
            .filter(
                due | lost,
                direction="outbound",
                channel__isnull=False,
                delivery_claim__isnull=True,
            )
            .exclude(external_id="")
            .filter(
                models.Q(metadata__local__isnull=True) | ~models.Q(metadata__local__has_key="delivery_conflict"),
            )
        )

    def expired_delivery_claims(self, *, now: datetime) -> Self:
        """Claims with no settlement before their deadline require acknowledgement."""

        return self.filter(
            direction="outbound",
            status="queued",
            delivery_claim__isnull=False,
            delivery_lease_until__lte=now,
        )

    def published(self) -> Self:
        """Retained messages published by a source or accepted outbound transport."""

        return self.untrashed().filter(status__in=self.model.PUBLISHED_STATUSES)

    def counted_in_thread(self) -> Self:
        """Use the model's retained-row predicate for reads and recounts."""

        return self.filter(self.model.counted_in_thread_q())

    @staticmethod
    def chronological_time(prefix: str = "") -> Any:
        """Message chronology as a SQL expression, including through a joined message."""

        return Coalesce(f"{prefix}sent_at", f"{prefix}created_at")

    def unread_for(self, user: Any) -> Self:
        """Filter authorized messages by the user's effective follow and receipt.

        This SQL projection keeps record lists at a fixed query count. The
        caller authorizes the message population; follows are structural read
        state, and the preference and chronological owners supply their rules.
        """

        # Models import their managers; preference values are needed only when querying.
        from angee.messaging.models import NotificationPreference

        follower_model = apps.get_model("messaging", "ThreadFollower")
        follows = follower_model.objects.db_manager(self.db).for_message_expression(user)
        return self.untrashed().alias(
            _order_at=self.chronological_time(),
            _follow_policy=models.Subquery(follows.values("notification_policy")[:1]),
            _follow_subtypes=models.Subquery(follows.values("subtype_keys")[:1]),
            _read_pk=models.Subquery(follows.values("last_read_message_id")[:1]),
            _read_at=models.Subquery(follows.annotate(
                _read_at=self.chronological_time("last_read_message__"),
            ).values("_read_at")[:1]),
        ).filter(
            models.Exists(follows),
            NotificationPreference.subscribed_row_q(
                notification_policy="_follow_policy", subtype_keys="_follow_subtypes",
            ),
            models.Q(_read_pk__isnull=True) | _MESSAGE_ORDER.after((models.F("_read_at"), models.F("_read_pk"))),
        )

    def needaction_expression(self, user: Any) -> models.Exists:
        """Correlate the existing authorized receipt/preference predicate to one row."""

        return models.Exists(self.unread_for(user).filter(pk=models.OuterRef("pk")))

    def explorer(self) -> MessageInbox:
        """Compose personal-inbox reads from this collection's current scope."""

        return MessageInbox(self)

    def for_thread(self, thread: Any) -> Self:
        """Return messages belonging to one thread."""

        return self.filter(thread_id__in=thread.member_threads().values("pk"))

    def for_channel(self, channel: Any) -> Self:
        """Return the messages that belong to ``channel`` — the purge-scope predicate.

        Only *this* channel's ``(channel, external_id)`` rows: the same logical message
        reached through another channel is a separate row there and survives. The single
        owner of the message channel-scope predicate, read by both the teardown delete and
        the delete-preview count so they cannot drift.
        """

        return self.filter(channel=channel)

    def inbox(self) -> Self:
        """Return channel/inbox messages — those not attached to a record thread.

        A message whose thread carries a ``ThreadAttachment`` is record chatter,
        reachable only through the record-scoped ``record_thread`` payload (gated on
        the parent record's read); it must never surface in the owner-scoped generic
        ``messages`` list, aggregate, or by-pk lookup. A message with no thread (an
        ingested mail whose thread was merged away) is not record-attached and stays
        in the inbox.
        """

        # The same structural anti-join as ``ThreadQuerySet.inbox``. A message
        # without a thread has no survivor id, so it matches no redirect group.
        thread_model = apps.get_model("messaging", "Thread")
        chatter = thread_model._base_manager.filter(attachments__role="chatter").survivor_ids()
        attached = chatter.filter(survivor=self.survivor_id("thread__", ref=models.OuterRef))
        return self.alias(_record_attached=models.Exists(attached)).filter(_record_attached=False)

    def automated_sender_ids(self) -> models.QuerySet[Any]:
        """Return the senders whose every message here is automated mail.

        Automated mail carries a list header (RFC 2369 ``List-Unsubscribe``, RFC 2919
        ``List-Id``), an RFC 3834 ``Auto-Submitted`` other than ``no``, or
        ``Precedence: bulk``, ``list`` or ``junk`` (:data:`AUTOMATED_MAIL_HEADERS`).
        Such an address speaks for a list or a system, and its display name names
        whoever triggered the mail. A person who sends the odd vacation auto-reply
        still writes the rest by hand.
        """

        part_model = apps.get_model("messaging", "Part")
        automated = part_model._base_manager.filter(
            reduce(or_, (models.Q(name=name) & test for name, test in AUTOMATED_MAIL_HEADERS.items())),
            message=models.OuterRef("pk"),
            role=part_model.PartRole.HEADER,
        )
        return (
            self.filter(sender__isnull=False)
            .annotate(_automated=models.Exists(automated))
            .order_by()
            .values("sender_id")
            .annotate(_sent=models.Count("pk"), _automated_sent=models.Count("pk", filter=models.Q(_automated=True)))
            .filter(_automated_sent=models.F("_sent"))
            .values_list("sender_id", flat=True)
        )

    def searching(self, term: str) -> Self:
        """Return messages matching one Odoo-style chatter search token."""

        return self.filter(_message_search_query(term))

    def title_text_expression(self) -> models.Expression:
        """The sparse title part's text, shared by selected fields and list projections."""

        part_model = apps.get_model("messaging", "Part")
        title_text = part_model._base_manager.filter(
            message=models.OuterRef("pk"), role=part_model.PartRole.TITLE,
        ).values("fragment__text")[:1]
        return Coalesce(models.Subquery(title_text), models.Value(""), output_field=models.TextField())

    def with_title_text(self) -> Self:
        """Annotate the model-owned title without a per-row content probe."""

        return self.annotate(_title_text=self.title_text_expression())

    def sender_name_expression(self) -> models.Expression | models.Subquery:
        """Correlate the actor-readable sender identity without multiplying rows."""

        handle_model = apps.get_model("parties", "Handle")
        actor = self.actor() or current_actor()
        handles = handle_model.objects.with_actor(actor) if actor is not None else handle_model.objects.all()
        return (
            handles.with_sender_name()
            .filter(pk=models.OuterRef("sender_id"))
            .readable_scalar_subquery(
                "_sender_name",
                actor=actor,
                default="",
                output_field=models.TextField(),
            )
        )

    def thread_title_expression(self) -> models.Expression | models.Subquery:
        """Correlate the title from the actor-readable thread queryset."""

        thread_model = apps.get_model("messaging", "Thread")
        actor = self.actor() or current_actor()
        return thread_model.objects.filter(pk=models.OuterRef("thread_id")).readable_scalar_subquery(
            "title__text",
            actor=actor,
            default="",
            output_field=models.TextField(),
        )

    def channel_vendor_name_expression(self) -> models.Expression | models.Subquery:
        """Correlate the vendor label through readable Integration and Vendor sets.

        Keep scalar projection inside the scoped subquery: the outer message
        may also materialize its channel through ``rebac_select_related``.
        """

        integration_model = apps.get_model("integrate", "Integration")
        vendor_model = apps.get_model("integrate", "Vendor")
        actor = self.actor() or current_actor()
        vendors = vendor_model.objects.with_actor(actor).scoped() if actor is not None else vendor_model.objects.none()
        return integration_model.objects.filter(
            pk=models.OuterRef("channel_id"), vendor_id__in=vendors.values("pk")
        ).readable_scalar_subquery("vendor__display_name", actor=actor, default="", output_field=models.TextField())

    def with_external_ids(self, external_ids: tuple[str, ...] | list[str]) -> Self:
        """Filter to exact external ids through the ``MD5(external_id)`` identity index.

        The public owner of the indexed external-id read (a plain ``external_id__in``
        would seq-scan past the digest indexes); callers add their own scope column
        (``platform`` for threading, ``channel`` for identity).
        """

        values = [external_id for external_id in external_ids if external_id]
        digests = [hashlib.md5(value.encode("utf-8")).hexdigest() for value in values]
        return _external_id_annotated(self).filter(_eid_digest__in=digests, external_id__in=values)

    def searching_fulltext(self, term: str) -> Self:
        """Return messages whose fragments full-text match ``term`` — the corpus-scale path.

        Rides the fragment GIN vector, so titles and bodies match through one index
        that holds each unique text once; use this for inbox-wide search where the
        substring predicates of :meth:`searching` would scan.
        """

        query = apps.get_model("messaging", "Fragment").objects.search_query(term)
        return self.filter(parts__fragment__search=query)

    def involving_parties(self, parties: Any) -> Self:
        """Return messages any of ``parties``' resolved handles participated in.

        Inbox scopes and correspondence reads pass either a lazy Party
        queryset (a circle subtree) or a concrete iterable. Participants are Handle-keyed
        and ``Handle.party`` is the resolution-materialised owner, so one join
        answers "every message exchanged with these parties across channels".
        The participant subquery preserves one row per message even when several
        handles match.
        """

        handle_model = apps.get_model("parties", "Handle")
        participant_model = apps.get_model("messaging", "Participant")
        visible_handle_ids = handle_model.objects.all().scoped_for_aggregate().filter(party__in=parties).values("pk")
        visible_message_ids = (
            participant_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                handle_id__in=models.Subquery(visible_handle_ids),
                message_id__isnull=False,
            )
            .values("message_id")
        )
        return self.filter(pk__in=models.Subquery(visible_message_ids))

    def for_feed(self, search: str = "", *, all_copies: bool = False, anchor: str = "") -> Self:
        """Apply read/search scope and tuple order, retaining an explicitly addressed occurrence.

        A message anchor names a physical row, including a retained source copy
        normally collapsed by ``canonical()``. Keep only that occurrence beside
        the canonical rows, under the same current scope and search predicates.
        """

        queryset = self.scoped().annotate(_order_at=MessageQuerySet.chronological_time())
        terms = self._feed_search(search).split()
        for term in terms:
            queryset = queryset.searching(term)
        if terms:
            queryset = queryset.distinct()
        if not all_copies:
            canonical = queryset.canonical()
            kind, _, value = anchor.partition(":")
            if kind == "message":
                canonical |= queryset.untrashed().filter(sqid=value)
            queryset = canonical
        return queryset.order_by("-_order_at", "-pk")

    @staticmethod
    def _feed_search(search: str) -> str:
        return " ".join(strip_null_bytes(search or "").split())

    def feed_page(
        self,
        *,
        scope: tuple[str, str],
        search: str = "",
        all_copies: bool = False,
        before_cursor: str | None = None,
        after_cursor: str | None = None,
        through_cursor: str | None = None,
        anchor: str = "",
        limit: int = 50,
    ) -> KeysetPage[Any]:
        """Read a currently authorized, newest-first fixed or discovery window.

        ``before_cursor`` is an exclusive upper cut; ``through_cursor`` is an
        inclusive lower cut. Advance the former while keeping the latter fixed.
        ``after_cursor`` instead reads the nearest newer page, and cannot combine
        with either bound. Cuts bind actor/root/search and survive anchor changes.
        Existing older/newer flags describe the whole scope; window exhaustion
        and history below the fixed lower cut are separate facts, even if empty.
        """

        search = self._feed_search(search)
        queryset = self.for_feed(search, all_copies=all_copies, anchor=anchor)
        around = None
        if anchor and before_cursor is None and after_cursor is None:
            kind, _, value = anchor.partition(":")
            if kind == "message":
                around = queryset.from_public_id(value)
            elif kind == "date":
                day, _, zone = value.partition("@")
                instant = datetime.combine(
                    datetime.strptime(day, "%Y-%m-%d").date(),
                    time.min,
                    ZoneInfo(zone) if zone else timezone.get_current_timezone(),
                )
                around = queryset.filter(_order_at__gte=instant).order_by("_order_at", "pk").first() or queryset.first()
            else:
                raise ValueError("Unknown transcript anchor.")
            if around is None and kind == "message":
                raise ValueError("Message unavailable in this conversation.")
        try:
            page = queryset.keyset_page(
                order=_MESSAGE_ORDER,
                cursor_scope=(scope, search, all_copies),
                cursor_salt="angee.messaging.feed.v1",
                before_cursor=before_cursor,
                after_cursor=after_cursor,
                through_cursor=through_cursor,
                around=around,
                limit=limit,
            )
        except InvalidKeysetCursor as error:
            raise ValueError("Invalid message feed cursor for this scope.") from error
        return page

    def feed_revalidate(
        self, ids: list[str], *, search: str = "", all_copies: bool = False, anchor: str = "",
    ) -> dict[str, Any]:
        """Partition at most 200 submitted IDs into current survivors and absences.

        The caller resolves the same readable root as feed_page. Every survivor
        passes the same current message, membership and search predicates. Absence
        does not distinguish deleted, inaccessible and out-of-scope rows.
        """

        if len(ids) > 200:
            raise ValueError("Message feed revalidation accepts at most 200 IDs.")
        unique = list(dict.fromkeys(ids))
        queryset = self.for_feed(search, all_copies=all_copies, anchor=anchor)
        survivors = {
            str(value) for value in queryset.filter(sqid__in=unique).values_list("sqid", flat=True)
        } if unique else set()
        return {
            "messages": queryset.filter(sqid__in=survivors),
            "absent_ids": [value for value in unique if value not in survivors],
        }


class PartQuerySet[PartT: Part](ProtectedQuerySet[PartT]):
    """Chainable read scopes for message body parts."""

    def inbox(self) -> Self:
        """Return inbox messages' parts — the part mirror of ``MessageQuerySet.inbox``.

        Record chatter surfaces only through the record-gated payloads; a part
        whose message's thread is record-attached stays off the generic surface
        (a thread-less provider event has no chatter attachment).
        """

        return self.exclude(message__thread__attachments__role="chatter")

    def fragment_uses(self, fragment: Any) -> Self:
        """Return this actor's readable parts referencing the shared fragment."""

        return self.filter(fragment=fragment)

    def signings(self) -> Iterator[Signing]:
        """Yield each pairing of a signature fragment with a sender who signed it, once.

        The distinct rows stream and carry fragment ids; each fragment's text loads
        once, in batches, rather than riding every row of the DISTINCT. A fragment
        deleted between the two reads took its signature with it and is skipped.
        """

        rows = (
            self.filter(role=self.model.PartRole.SIGNATURE, fragment__isnull=False, message__sender__isnull=False)
            .order_by("fragment__hash", "message__sender_id")
            .values_list(
                "fragment_id",
                "fragment__hash",
                "message__sender_id",
                "message__sender__party_id",
                "message__created_by_id",
            )
            .distinct()
        )
        fragment_model = self.model.fragment.field.related_model
        assert fragment_model is not None
        fragments = fragment_model._base_manager
        for batch in batched(rows.iterator(chunk_size=_SIGNING_TEXT_BATCH), _SIGNING_TEXT_BATCH):
            texts = dict(fragments.filter(pk__in={row[0] for row in batch}).values_list("pk", "text"))
            for fragment_id, fragment_hash, sender_id, party_id, owner_id in batch:
                if fragment_id in texts:
                    yield Signing(fragment_hash, texts[fragment_id] or "", sender_id, party_id, owner_id)

    def attachments(self) -> Self:
        """Return parts that carry a stored file — a message's attachment parts."""

        return self.filter(file__isnull=False)

    def attached_files(self) -> Self:
        """Return file-bearing attachment Part uses within this queryset's scope.

        Use ``message.parts.attached_files()`` under the usual actor context, or
        ``message.parts.with_actor(actor).attached_files()``. Compose stored-file,
        disposition, role, MIME and byte-size predicates with the existing
        message-bounded, cycle-safe ancestor navigation. Exclude inline decoration
        (Content-ID references, inline files without a filename or of a non-document
        type), signature and delivery-report branches, and zero-byte files; an inline
        PDF or XML with a filename is a document and stays selected. Open one
        parsed ``message/rfc822`` level regardless of multipart depth; skip raw
        message wrappers and deeper forwarded content without parsing file bytes.

        Selection stays lazy, preserving actor, filters, ordering and later writes.
        Each Part use is retained even when several uses share a File.
        """

        return self.attachments().alias(_attachment_skip=self._attached_file_reason()).filter(_attachment_skip="")

    def attached_file_skip_counts(self) -> dict[str, int]:
        """Count each skipped file use by the same first matching reason as selection."""

        rows = self.attachments().annotate(_attachment_skip=self._attached_file_reason()).exclude(
            _attachment_skip="",
        ).order_by().values("_attachment_skip").annotate(count=models.Count("pk"))
        return {row["_attachment_skip"]: row["count"] for row in rows}

    def _attached_file_reason(self) -> AttachedFileSkipReason:
        actor, bypass = self.effective_actor(strict=True)
        tree = self.model.objects.using(self.db)
        readable = (tree.system_context(reason="messaging attached-file lineage for an elevated queryset")
                    if bypass else tree.with_actor(actor)).scoped_for_aggregate()
        return AttachedFileSkipReason(readable)
