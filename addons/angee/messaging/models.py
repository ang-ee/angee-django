"""Source models for the messaging addon.

Messaging is threads and messages built on the parties contacts foundation: a
message's sender and participants are :class:`~angee.parties.models.Handle` rows,
so the dependency points one way (messaging → parties). A :class:`Channel` is an
``integrate.Integration`` child (a bridge) that ingests messages from an external
source; the email/social mapping lands in ``messaging_integrate_*`` backends.

The shapes mirror JMAP/Gmail/RFC-5322: a :class:`Thread` aggregates :class:`Message`
rows; a message's body is a recursive :class:`Part` tree whose text nodes reference
a content-addressed :class:`Fragment` (dedup + quotation + signature isolation) and
whose byte nodes reference a ``storage.File``; cross-message relations (quote/reply/
mention) live on :class:`MessageEdge`. A subject is not a column: it is a sparse
``TITLE`` part pointing at a shared fragment, and a thread's display/grouping title
is a fragment FK — so only messages that *have* a title pay for one, and a re-quoted
subject exists once. Ingestion idempotency rests on expression unique constraints
over ``MD5(external_id)`` — channel-scoped for messages (one row per provider event
per source), platform-scoped for threads (cross-account mail merges into one
conversation); the digest is an index implementation detail, never a model field.
The write path lives on the managers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import Any, ClassVar, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import close_old_connections, connection, connections, models, transaction
from django.db.models.functions import MD5, Coalesce
from django.utils import timezone
from django.utils.text import capfirst
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    current_actor,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.actors import actor_user_id
from angee.base.fields import SqidField, StateField
from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel
from angee.base.refs import RecordRefMixin
from angee.integrate.models import Bridge
from angee.integrate.sync import bridge_progress_context, current_bridge_progress
from angee.messaging.backends import ChannelBackend
from angee.messaging.managers import (
    FragmentManager,
    MessageEdgeManager,
    MessageManager,
    MessageStarManager,
    PartManager,
    ReactionManager,
    ThreadActivityManager,
    ThreadAttachmentManager,
    ThreadFollowerManager,
    ThreadManager,
    ThreadNotificationManager,
    strip_null_bytes,
)
from angee.messaging.tracking import FieldTracker, TrackingChange
from angee.parties.models import Handle
from angee.tasks.autoconfig import SETTINGS as _TASK_SETTINGS

# Partitioned channel syncs (IMAP mailboxes) drain up to this many partitions
# concurrently unless config["sync_parallelism"] says otherwise.
_DEFAULT_SYNC_PARALLELISM = 4


def _owner_user_id(instance: models.Model) -> Any | None:
    """Return the user FK id for this model's effective REBAC actor."""

    actor_getter = getattr(instance, "actor", None)
    actor = actor_getter() if callable(actor_getter) else None
    if actor is None:
        actor = current_actor()
    return actor_user_id(actor)


def _user_subject_ref(*, user: Any = None, user_id: Any = None) -> SubjectRef:
    """Return the REBAC subject ref for a user instance or a bare user id.

    The user model owns its subject identity (its ``rebac_id_attr`` sqid, not the raw
    pk), so a bare ``user_id`` is loaded and resolved through :func:`to_subject_ref`
    rather than assembling an ``auth/user:<pk>`` ref by hand.
    """

    if user is not None:
        return to_subject_ref(user)
    if user_id is None:
        raise ValueError("A user or user_id is required to build a subject reference.")
    return to_subject_ref(get_user_model()._base_manager.get(pk=user_id))


class ThreadedModelMixin(models.Model):
    """Add Odoo-style chatter thread behavior to a model row.

    A concrete model opts in by inheriting this abstract mixin. The model remains
    the owner of the record; messaging owns the attached thread edge and the
    message write path.
    """

    thread_attachment_role: ClassVar[str] = "chatter"
    """The attachment role used for the model's primary chatter thread."""

    thread_post_access: ClassVar[str] = "write"
    """Record permission required to post a chatter message."""

    thread_broadcasts_changes: ClassVar[bool] = False
    """Whether this host streams its record-attached chatter over ``changes(Thread)``.

    Default ``False`` keeps record chatter isolated to the record-scoped
    ``record_thread`` surface: a threaded record stays silent on the generic
    ``changes`` subscription. A host that opts in (a chat room) stamps the flag
    onto its chatter thread at attachment (``host_broadcasts_changes``), so its
    members — holding ``messaging/thread.reader`` — receive member-gated
    ``threadChanged`` events while every other record's chatter stays isolated.
    """

    thread_read_access: ClassVar[str] = "read"
    """Record permission required to read/react to personal chatter state."""

    thread_autofollow_author: ClassVar[bool] = True
    """Whether posting a message subscribes the posting actor to replies."""

    thread_create_autofollow_author: ClassVar[bool] = True
    """Whether creating a threaded row subscribes the creating actor."""

    thread_create_log: ClassVar[bool] = True
    """Whether creating a threaded row logs a creation message."""

    thread_creation_subtype_key: ClassVar[str] = "record_created"
    """Subtype key used for automatic record creation messages."""

    thread_activity_access: ClassVar[str] = "write"
    """Record permission required to schedule or update chatter activities."""

    thread_tracking_fields: ClassVar[tuple[str, ...]] = ()
    """Model field names automatically tracked into the chatter on save."""

    thread_tracking_subtype_key: ClassVar[str] = "record_updated"
    """Subtype key used for automatic field tracking messages."""

    thread_suggested_recipient_fields: ClassVar[tuple[str, ...]] = ()
    """User FK fields suggested as chatter recipients, like Odoo's ``user_id``."""

    thread_attachments = GenericRelation(
        "messaging.ThreadAttachment",
        content_type_field="content_type",
        object_id_field="object_id",
    )
    """Reverse edge to this row's chatter attachments.

    The polymorphic ``ThreadAttachment`` binds through a ``GenericForeignKey``, which
    Django's delete collector cannot follow on its own; declaring the reverse
    ``GenericRelation`` makes any delete of this row (instance or bulk queryset)
    collect its attachments, so no attachment is left keyed to a reused primary key.
    Full thread-graph teardown (the private ``Thread`` and its messages, which the
    attachment's FK cannot cascade *up* to) runs on both delete paths through the
    ``pre_delete`` receiver messaging wires onto every threaded model
    (``angee.messaging.signals``), inside the delete collector's own transaction.
    """

    class Meta:
        """Django model options for the thread behavior mixin."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist this row and log configured field changes in its chatter."""

        creating = self._state.adding or self.pk is None
        tracker = self._field_tracker()
        tracking_before = tracker.snapshot(kwargs.get("update_fields"))
        super().save(*args, **kwargs)
        if creating:
            self._message_after_create()
            return
        changes = tracker.changes(tracking_before)
        if changes:
            self.message_track(changes, subtype_key=self.thread_tracking_subtype_key)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete this row after authorizing the record, then elevate its cascade.

        Composing this mixin means an instance delete checks this record's own
        ``delete`` permission explicitly, then runs the entire Django delete collector
        under ``system_context`` so messaging's private chatter graph can be torn down.
        A host model must not attach independently-authorized ``on_delete=CASCADE``
        children under the same record; such children must derive their delete
        permission through the record in their own zed. ``QuerySet.delete()`` does not
        call this override, so bulk deletion of threaded records is a system-context
        maintenance path only.
        """

        if not self.has_access("delete"):
            raise PermissionDenied(f"Denied: cannot delete {self._meta.label}")
        with system_context(reason="messaging.threaded_record.delete"):
            return super().delete(*args, **kwargs)

    def message_thread(self, *, create: bool = True) -> models.Model | None:
        """Return this row's chatter thread, optionally creating it."""

        attachment = self.message_thread_attachment(create=create)
        return attachment.thread if attachment is not None else None

    def message_thread_attachment(self, *, create: bool = True) -> models.Model | None:
        """Return this row's chatter thread attachment, optionally creating it."""

        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        if create:
            return attachment_model.objects.ensure_for_record(
                self,
                role=self.thread_attachment_role,
                title=self.message_thread_title(),
            )
        return attachment_model.objects.for_record(self, role=self.thread_attachment_role)

    def message_post(
        self,
        body: str,
        *,
        attachments: tuple[models.Model, ...] = (),
        recipient_user_ids: tuple[Any, ...] = (),
        autofollow_recipients: bool = False,
        message_type: Message.MessageKind | None = None,
        subtype_key: str = "comment",
        parent: models.Model | None = None,
    ) -> models.Model:
        """Post an internal comment on this row's chatter thread.

        ``message_type`` defaults to :attr:`Message.MessageKind.COMMENT` (resolved by
        the message write path), keeping the enum the single source of truth. A chatter
        comment carries no title of its own — the thread's title fragment is the label.
        """

        return self._message_post(
            body,
            attachments=attachments,
            recipient_user_ids=recipient_user_ids,
            autofollow_recipients=autofollow_recipients,
            message_type=message_type,
            subtype_key=subtype_key,
            parent=parent,
            tracking_values=(),
            autofollow_author=self.thread_autofollow_author,
        )

    def message_log(
        self,
        body: str = "",
        *,
        subtype_key: str = "note",
        message_type: Message.MessageKind | None = None,
        tracking_values: tuple[TrackingChange | dict[str, Any], ...] = (),
        attachments: tuple[models.Model, ...] = (),
        parent: models.Model | None = None,
    ) -> models.Model:
        """Log a structured system note on this row's chatter thread.

        Defaults to the :attr:`Message.MessageKind.NOTIFICATION` kind; callers logging
        a tracked change (``message_track``) pass ``AUTO_COMMENT``.
        """

        message_model = apps.get_model("messaging", "Message")
        return self._message_post(
            body,
            attachments=attachments,
            message_type=message_type or message_model.MessageKind.NOTIFICATION,
            subtype_key=subtype_key,
            parent=parent,
            tracking_values=tracking_values,
            recipient_user_ids=(),
            autofollow_recipients=False,
            autofollow_author=False,
        )

    def message_track(
        self,
        changes: tuple[TrackingChange | dict[str, Any], ...],
        *,
        body: str = "",
        subtype_key: str = "record_updated",
    ) -> models.Model:
        """Log Odoo-style field tracking values in this row's chatter thread.

        Field tracking is an automatic system write: the log belongs to the record,
        not to the actor whose save triggered it, so it goes through
        :meth:`_message_system_post` — the system-write owner that never consults
        :meth:`can_post`. An actor authorized to change a tracked field but not to
        post comments must still get its change logged instead of having the whole
        save rolled back by a post-access denial.
        """

        message_model = apps.get_model("messaging", "Message")
        return self._message_system_post(
            body=body,
            subtype_key=subtype_key,
            message_type=message_model.MessageKind.AUTO_COMMENT,
            tracking_values=changes,
        )

    def message_update_content(self, message: models.Model, *, body: str) -> models.Model:
        """Update a comment in this row's chatter thread."""

        if not self.can_post():
            raise PermissionDenied(
                f"Updating messages on {self._meta.label} requires {self.thread_post_access!r} access."
            )
        attachment = self.message_thread_attachment(create=False)
        if attachment is None or message.thread_id != attachment.thread_id:
            raise ValueError("Message does not belong to this record thread.")
        message_model = apps.get_model("messaging", "Message")
        owner_id = _owner_user_id(self)
        return message_model.objects.update_content(message, body=body, owner_id=owner_id)

    def message_unlink(self, message: models.Model) -> models.Model:
        """Delete a message from this row's chatter thread."""

        if not self.can_post():
            raise PermissionDenied(
                f"Deleting messages on {self._meta.label} requires {self.thread_post_access!r} access."
            )
        attachment = self.message_thread_attachment(create=False)
        if attachment is None or message.thread_id != attachment.thread_id:
            raise ValueError("Message does not belong to this record thread.")
        message_model = apps.get_model("messaging", "Message")
        return message_model.objects.unlink_from_thread(message, thread=attachment.thread)

    def message_reaction(
        self,
        message: models.Model,
        *,
        reaction: str,
        action: str = "toggle",
        user: Any,
    ) -> models.Model:
        """Add, remove, or toggle ``user``'s reaction on a chatter message."""

        if not self.can_post():
            raise PermissionDenied(
                f"Reacting to messages on {self._meta.label} requires {self.thread_post_access!r} access."
            )
        attachment = self.message_thread_attachment(create=False)
        if attachment is None or message.thread_id != attachment.thread_id:
            raise ValueError("Message does not belong to this record thread.")
        message_model = apps.get_model("messaging", "Message")
        return message_model.objects.set_reaction(message, reaction=reaction, action=action, user=user)

    def message_starred(self, message: models.Model, *, user: Any) -> bool:
        """Return whether ``user`` has starred ``message`` in this row's chatter."""

        attachment = self.message_thread_attachment(create=False)
        if attachment is None or message.thread_id != attachment.thread_id:
            raise ValueError("Message does not belong to this record thread.")
        star_model = apps.get_model("messaging", "MessageStar")
        return bool(star_model.objects.is_starred(message, user=user))

    def message_set_starred(self, message: models.Model, *, user: Any, starred: bool | None = None) -> bool:
        """Set or toggle ``user``'s star on a message in this row's chatter."""

        if not self._message_read_allowed():
            raise PermissionDenied(
                f"Starring messages on {self._meta.label} requires {self.thread_read_access!r} access."
            )
        attachment = self.message_thread_attachment(create=False)
        if attachment is None or message.thread_id != attachment.thread_id:
            raise ValueError("Message does not belong to this record thread.")
        star_model = apps.get_model("messaging", "MessageStar")
        return bool(star_model.objects.set_starred(message, user=user, starred=starred))

    def message_unstar_all(self, *, user: Any) -> int:
        """Remove all Odoo-style stars owned by ``user``."""

        if not self._message_read_allowed():
            raise PermissionDenied(
                f"Unstarring messages on {self._meta.label} requires {self.thread_read_access!r} access."
            )
        star_model = apps.get_model("messaging", "MessageStar")
        return int(star_model.objects.unstar_all(user=user))

    def message_set_done(self, message: models.Model, *, user: Any) -> int:
        """Advance ``user``'s read receipt to ``message`` (mark read up to it).

        Read state is positional (a follower's ``last_read_message`` receipt), so
        "done" means everything at or before ``message`` in feed order counts read —
        the IM semantics that replaced the per-message notification flags.
        """

        if not self._message_read_allowed():
            raise PermissionDenied(
                f"Marking messages done on {self._meta.label} requires {self.thread_read_access!r} access."
            )
        attachment = self.message_thread_attachment(create=False)
        if attachment is None or message.thread_id != attachment.thread_id:
            raise ValueError("Message does not belong to this record thread.")
        follower_model = apps.get_model("messaging", "ThreadFollower")
        return int(follower_model.objects.mark_read_up_to(attachment.thread, user=user, message=message))

    def _message_post(
        self,
        body: str,
        *,
        attachments: tuple[models.Model, ...],
        message_type: Message.MessageKind | None,
        subtype_key: str,
        parent: models.Model | None,
        tracking_values: tuple[TrackingChange | dict[str, Any], ...],
        recipient_user_ids: tuple[Any, ...],
        autofollow_recipients: bool,
        autofollow_author: bool,
    ) -> models.Model:
        """Post one user-authored chatter message after enforcing this row's post policy.

        User posts ride the actor's :attr:`thread_post_access`; the message itself is
        written by the shared system-write owner (:meth:`_message_system_post`), so the
        post gate lives in exactly one place and the automatic-log path can reuse that
        write without it.
        """

        if not self.can_post():
            raise PermissionDenied(
                f"Posting on {self._meta.label} requires {self.thread_post_access!r} access."
            )
        message = self._message_system_post(
            body=body,
            attachments=attachments,
            message_type=message_type,
            subtype_key=subtype_key,
            parent=parent,
            tracking_values=tracking_values,
            recipient_user_ids=recipient_user_ids,
        )
        owner_id = _owner_user_id(self)
        follower_model = apps.get_model("messaging", "ThreadFollower")
        # Autofollow is messaging-owned bookkeeping reacting to an already-
        # authorized post (the thread_post_access gate above): it runs under
        # system_context so a non-user actor species (an agent posting through
        # its service user) cannot be denied on the private follower rows —
        # the same elevation rule as the delete cascade. The user-facing
        # follow verb (message_subscribe, incl. grant_read) stays actor-gated.
        if autofollow_author and owner_id is not None:
            with system_context(reason="messaging.autofollow"):
                follower_model.objects.subscribe(
                    self,
                    user_id=owner_id,
                    role=self.thread_attachment_role,
                )
                # A first post on an unfollowed record: the write path's own receipt
                # advance ran before this autofollow existed, so seed the fresh
                # follower's receipt at the just-posted message — an author never
                # sees their own post as unread.
                follower_model.objects.mark_read_up_to(message.thread, user_id=owner_id, message=message)
        if autofollow_recipients:
            with system_context(reason="messaging.autofollow"):
                for user_id in recipient_user_ids:
                    follower_model.objects.subscribe(
                        self,
                        user_id=user_id,
                        role=self.thread_attachment_role,
                    )
        return message

    def _message_system_post(
        self,
        *,
        body: str = "",
        attachments: tuple[models.Model, ...] = (),
        message_type: Message.MessageKind | None,
        subtype_key: str,
        parent: models.Model | None = None,
        tracking_values: tuple[TrackingChange | dict[str, Any], ...] = (),
        recipient_user_ids: tuple[Any, ...] = (),
    ) -> models.Model:
        """Write one automatic system message on this row's chatter thread.

        The single owner of the chatter *system* write — record-creation notes and
        field-tracking auto-comments. These are written by the framework on the
        record's behalf, not authored by the acting user, so this path never consults
        :meth:`can_post` / :attr:`thread_post_access`: an actor authorized to change a
        tracked field but not to post comments must still get the change logged rather
        than have its save rolled back by a post-access denial. User-authored posts go
        through :meth:`_message_post`, which adds the post gate and the follower fan-out.
        """

        attachment = self.message_thread_attachment(create=True)
        if attachment is None:
            raise ValueError("Cannot post a message without a thread.")
        message_model = apps.get_model("messaging", "Message")
        # The framework writes on the record's behalf, so the whole pipeline
        # (message, parts, tracking rows, fanout, receipt advance) runs under
        # system_context: a user actor passed the record gate already, and a
        # non-user actor species (an agent authoring through its service user)
        # must not be denied on messaging-private bookkeeping rows.
        with system_context(reason="messaging.system_post"):
            return self._system_post_pipeline(
                message_model,
                attachment,
                body=body,
                attachments=attachments,
                message_type=message_type,
                subtype_key=subtype_key,
                parent=parent,
                tracking_values=tracking_values,
                recipient_user_ids=recipient_user_ids,
            )

    def _system_post_pipeline(
        self,
        message_model: type[models.Model],
        attachment: models.Model,
        *,
        body: str,
        attachments: tuple[models.Model, ...],
        message_type: Message.MessageKind | None,
        subtype_key: str,
        parent: models.Model | None,
        tracking_values: tuple[TrackingChange | dict[str, Any], ...],
        recipient_user_ids: tuple[Any, ...],
    ) -> models.Model:
        """Run the elevated system-post write; split out for readability only."""

        return message_model.objects.post_to_thread(
            attachment.thread,
            body=body,
            owner_id=_owner_user_id(self),
            attachment=attachment,
            attachments=attachments,
            message_type=message_type,
            subtype_key=subtype_key,
            subtype_model_label=self._meta.label,
            parent=parent,
            tracking_values=tracking_values,
            recipient_user_ids=recipient_user_ids,
        )

    def message_subscribe(
        self,
        *,
        user: models.Model | None = None,
        notification_policy: str | None = None,
        subtype_keys: tuple[str, ...] | None = None,
        grant_read: bool = False,
    ) -> models.Model:
        """Subscribe a user to this row's chatter thread.

        ``notification_policy`` / ``subtype_keys`` seed a first subscribe and default to
        an inbox follow with no subtype filter; a re-subscribe preserves an existing
        follower's state unless a value is passed. ``grant_read`` also grants the user
        ``reader`` on the thread in the same write (a chat-room membership) — a consumer
        that manages room membership composes this rather than writing the tuple itself.
        """

        follower_model = apps.get_model("messaging", "ThreadFollower")
        return follower_model.objects.subscribe(
            self,
            user=user,
            role=self.thread_attachment_role,
            notification_policy=notification_policy,
            subtype_keys=subtype_keys,
            grant_read=grant_read,
        )

    def message_unsubscribe(self, *, user: models.Model | None = None, revoke_read: bool = False) -> bool:
        """Unsubscribe a user from this row's chatter thread.

        ``revoke_read`` also revokes the user's thread ``reader`` grant (the mirror of
        :meth:`message_subscribe`'s ``grant_read``) — expelling a chat-room member drops
        the follow and the read that kept the member's ``threadChanged`` socket live.
        """

        follower_model = apps.get_model("messaging", "ThreadFollower")
        return bool(
            follower_model.objects.unsubscribe(
                self, user=user, role=self.thread_attachment_role, revoke_read=revoke_read
            )
        )

    def message_is_follower(self, *, user: models.Model | None = None) -> bool:
        """Return whether a user follows this row's chatter thread."""

        follower_model = apps.get_model("messaging", "ThreadFollower")
        return bool(follower_model.objects.is_following(self, user=user, role=self.thread_attachment_role))

    def message_followers(self) -> models.QuerySet:
        """Return this row's chatter followers."""

        follower_model = apps.get_model("messaging", "ThreadFollower")
        return follower_model.objects.for_record(self, role=self.thread_attachment_role)

    def message_suggested_recipients(
        self,
        *,
        role: str = "chatter",
        reply_discussion: bool = True,
        user: models.Model | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return Odoo-style suggested recipients for this record's chatter.

        Suggestions come from fields the record declares as recipient owners and,
        when there is a discussion, from the latest user-facing comment's direct
        notification recipients. Existing followers and the current user are
        omitted so the composer suggests only additional recipients.
        """

        if not self._message_read_allowed():
            raise PermissionDenied(
                f"Reading message recipients on {self._meta.label} requires {self.thread_read_access!r} access."
            )
        user_model = apps.get_model(settings.AUTH_USER_MODEL)
        attachment = self.message_thread_attachment(create=False)
        thread = attachment.thread if attachment is not None else None
        follower_ids = {
            str(user_id)
            for user_id in self.message_followers()
            .sudo(reason="messaging suggested recipients follower suppression")
            .values_list("user_id", flat=True)
        }
        current_user_id = getattr(user, "pk", None)
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(candidate: Any, *, reason: str, source: str) -> None:
            resolved = _message_suggestion_user(user_model, candidate)
            if resolved is None:
                return
            key = str(resolved.pk)
            if key in seen or key in follower_ids or key == str(current_user_id):
                return
            if getattr(resolved, "is_active", True) is False:
                return
            seen.add(key)
            suggestions.append({"user": resolved, "reason": reason, "source": source})

        for field in self._message_suggested_recipient_model_fields():
            add(
                getattr(self, field.name, None),
                reason=capfirst(str(field.verbose_name or field.name)),
                source=field.name,
            )

        if reply_discussion and thread is not None:
            message_model = apps.get_model("messaging", "Message")
            notification_model = apps.get_model("messaging", "ThreadNotification")
            latest = (
                message_model._base_manager.filter(
                    thread=thread,
                    message_type__in=(
                        message_model.MessageKind.COMMENT,
                        message_model.MessageKind.EMAIL,
                    ),
                )
                .order_by("-sent_at", "-created_at", "-pk")
                .first()
            )
            if latest is not None:
                add(latest.created_by_id, reason="Recent message author", source="recent_message_author")
                for notification in (
                    notification_model._base_manager.filter(message=latest)
                    .select_related("user")
                    .order_by("pk")
                ):
                    add(notification.user, reason="Recent message recipient", source="recent_message_recipient")

        return tuple(suggestions)

    def activity_schedule(
        self,
        *,
        user: models.Model | None = None,
        summary: str,
        note: str = "",
        due_date: object | None = None,
        activity_type: str = "todo",
        metadata: dict[str, object] | None = None,
    ) -> models.Model:
        """Schedule an activity on this row's chatter thread."""

        if not self._message_activity_allowed():
            raise PermissionDenied(
                f"Scheduling activities on {self._meta.label} requires {self.thread_activity_access!r} access."
            )
        activity_model = apps.get_model("messaging", "ThreadActivity")
        return activity_model.objects.schedule(
            self,
            user=user,
            role=self.thread_attachment_role,
            summary=summary,
            note=note,
            due_date=due_date,
            activity_type=activity_type,
            metadata=metadata,
        )

    def activity_ids(self, *, include_done: bool = True) -> models.QuerySet:
        """Return this row's scheduled chatter activities."""

        activity_model = apps.get_model("messaging", "ThreadActivity")
        return activity_model.objects.for_record(
            self,
            role=self.thread_attachment_role,
            include_done=include_done,
        )

    def activity_feedback(self, activity: models.Model, *, feedback: str = "") -> models.Model:
        """Mark an activity done and log the feedback in the chatter thread."""

        if not self._message_activity_allowed():
            raise PermissionDenied(
                f"Completing activities on {self._meta.label} requires {self.thread_activity_access!r} access."
            )
        activity_model = apps.get_model("messaging", "ThreadActivity")
        return activity_model.objects.complete(activity, feedback=feedback)

    def activity_unlink(self, activity: models.Model) -> models.Model:
        """Cancel a scheduled activity without logging a completion message."""

        if not self._message_activity_allowed():
            raise PermissionDenied(
                f"Canceling activities on {self._meta.label} requires {self.thread_activity_access!r} access."
            )
        activity_model = apps.get_model("messaging", "ThreadActivity")
        return activity_model.objects.cancel(activity)

    def message_thread_title(self) -> str:
        """Return the default title text for this row's chatter thread.

        Interned as a content-addressed fragment and stamped onto the thread's
        ``title`` pointer at attachment; override to label the record's room.
        """

        return str(self)

    def message_creation_message(self) -> str:
        """Return the automatic chatter body logged when this row is created."""

        return f"{capfirst(str(self._meta.verbose_name))} created"

    def _message_after_create(self) -> None:
        """Run Odoo-style chatter side effects after this row is first saved."""

        owner_id = _owner_user_id(self)
        if owner_id is None:
            return
        follower_model = apps.get_model("messaging", "ThreadFollower")
        if self.thread_create_autofollow_author:
            # System bookkeeping on an already-authorized create; see the
            # autofollow elevation note in _message_post.
            with system_context(reason="messaging.autofollow"):
                follower_model.objects.subscribe(
                    self,
                    user_id=owner_id,
                    role=self.thread_attachment_role,
                )
        create_changes = self._field_tracker().create_changes()
        message_model = apps.get_model("messaging", "Message")
        if self.thread_create_log:
            self._message_system_post(
                body=self.message_creation_message(),
                message_type=message_model.MessageKind.NOTIFICATION,
                subtype_key=self.thread_creation_subtype_key,
            )
        if create_changes:
            self._message_system_post(
                body="",
                message_type=message_model.MessageKind.AUTO_COMMENT,
                subtype_key=self.thread_tracking_subtype_key,
                tracking_values=tuple(create_changes),
            )

    def can_post(self, user: Any = None) -> bool:
        """Return whether ``user`` may post to this row's chatter thread.

        The single public owner of chatter post access — the record's configured
        :attr:`thread_post_access`, resolved against the ambient rebac actor. The
        chatter write path (``message_post``/``message_update_content``/
        ``message_unlink``/``message_reaction``) and the :meth:`Message.can_edit` /
        :meth:`Message.can_delete` read projections both consult it, so the write gate
        and the projection can never drift. An explicitly unauthenticated ``user`` is
        denied; ``None`` defers to the ambient actor the write path already runs under.
        """

        if user is not None and getattr(user, "is_authenticated", True) is False:
            return False
        return self._message_post_allowed()

    def _message_post_allowed(self) -> bool:
        """Return whether the ambient actor can post to this row."""

        has_access = getattr(self, "has_access", None)
        if not callable(has_access):
            return True
        return bool(has_access(self.thread_post_access))

    def _message_read_allowed(self) -> bool:
        """Return whether the ambient actor can read personal chatter state."""

        has_access = getattr(self, "has_access", None)
        if not callable(has_access):
            return True
        return bool(has_access(self.thread_read_access))

    def _message_activity_allowed(self) -> bool:
        """Return whether the ambient actor can schedule/update activities."""

        has_access = getattr(self, "has_access", None)
        if not callable(has_access):
            return True
        return bool(has_access(self.thread_activity_access))

    def _field_tracker(self) -> FieldTracker:
        """Return the field-change tracker bound to this row's tracked fields.

        The generic snapshot/diff/render mechanism lives on :class:`FieldTracker`
        (``messaging.tracking``); the mixin only composes it and keeps the chatter verbs.
        """

        return FieldTracker(self, self.thread_tracking_fields)

    @classmethod
    def _message_suggested_recipient_model_fields(cls) -> tuple[models.Field[Any, Any], ...]:
        """Return configured user FK fields used for recipient suggestions."""

        if not cls.thread_suggested_recipient_fields:
            return ()
        user_model = apps.get_model(settings.AUTH_USER_MODEL)
        fields: list[models.Field[Any, Any]] = []
        for name in cls.thread_suggested_recipient_fields:
            try:
                field = cls._meta.get_field(name)
            except FieldDoesNotExist as error:
                raise ImproperlyConfigured(
                    f"{cls._meta.label}.thread_suggested_recipient_fields includes unknown field {name!r}."
                ) from error
            if not isinstance(field, models.ForeignKey) or field.remote_field.model is not user_model:
                raise ImproperlyConfigured(
                    f"{cls._meta.label}.thread_suggested_recipient_fields can only include user ForeignKey fields."
                )
            fields.append(field)
        return tuple(fields)


class Channel(Bridge):
    """A connected message source that ingests threads/messages from email or social.

    An ``integrate.Integration`` child (credential / owner / status from the
    connection substrate) and a ``Bridge`` (the scheduler + ``syncIntegration`` drive
    it through ``run_sync``). ``backend_class`` selects the protocol — ``imap``
    (contributed by ``messaging_integrate_imap``), later ``youtube``/``facebook`` —
    and ``config`` carries source settings. ``sync()`` fetches + parses, then maps
    each message onto the messaging managers.
    """

    runtime = True
    extends = "integrate.Integration"
    integration_kind_label = "Channel"

    backend_class = ImplClassField(
        base_class=ChannelBackend,
        registry_setting="ANGEE_CHANNEL_BACKEND_CLASSES",
        default="manual",
    )
    """Registry key for the channel backend bound to this channel."""

    objects = AngeeManager()

    class Meta:
        """Django model options for the channel child model."""

        abstract = True
        rebac_resource_type = "messaging/channel"
        rebac_id_attr = "sqid"

    @property
    def backend(self) -> ChannelBackend:
        """Return this channel's selected backend, bound to this row."""

        backend_class = cast("type[ChannelBackend]", self.resolve_impl("backend_class"))
        return backend_class(self)

    def sync(self) -> int:
        """Sync the channel's source (the Bridge child-sync contract); report the landed count.

        A backend that partitions its source (:meth:`ChannelBackend.sync_partitions`
        — IMAP mailboxes) drains each partition on its own backend instance and
        transport connection, in parallel threads capped by
        ``config["sync_parallelism"]`` (default ``4``). Every other backend keeps
        the serial single-drain path. The whole run stays under the bridge's one
        advisory sync lock either way; parallelism across *channels* rides the
        worker fleet, parallelism within a channel rides these threads.
        """

        backend = self.backend
        deadline = self._sync_deadline()
        cap = self._sync_parallelism()
        # Enumerating partitions costs a transport round-trip; skip it entirely
        # when the drain is pinned serial (SQLite, or an operator cap of 1).
        partitions = tuple(backend.sync_partitions()) if cap > 1 else ()
        parallelism = min(len(partitions), cap) if partitions else 0
        if parallelism > 1:
            # Partition drains own their own transports; release the discovery
            # connection this instance opened enumerating them.
            backend.close()
            return self._sync_parallel(partitions, parallelism, deadline=deadline)
        return self._drain(backend, deadline=deadline)

    def _sync_deadline(self) -> float:
        """Return the monotonic instant this run must stop draining by.

        Celery hard-kills a task at Celery's ``task_time_limit`` with SIGKILL — no
        exception, no cleanup, a stuck ``syncing`` stage and a dropped lock. A
        backfill is bigger than any one task budget, so the drain stops cleanly
        inside the soft limit instead, records the partial run, and the scheduler
        resumes from the persisted cursor watermarks on the next poll.
        ``config["sync_time_budget"]`` (seconds) overrides.
        """

        config = self.config if isinstance(self.config, dict) else {}
        soft_limit = float(
            cast(
                "float | int",
                getattr(
                    settings,
                    "CELERY_TASK_SOFT_TIME_LIMIT",
                    _TASK_SETTINGS["CELERY_TASK_SOFT_TIME_LIMIT"],
                ),
            )
        )
        # An unparsable operator value raises into record_sync_error — silently
        # substituting the default would hide the misconfiguration.
        budget = float(cast("float | int | str", config.get("sync_time_budget", max(60.0, soft_limit - 60.0))))
        return monotonic() + max(0.0, budget)

    def _sync_parallelism(self) -> int:
        """Return the configured per-channel partition thread cap (min 1).

        Parallel partitions need a database that takes concurrent writers with
        row locks; SQLite (the zero-config dev fallback) cannot, so any other
        vendor pins the drain serial — same vendor gate as fragment full-text.
        """

        if connection.vendor != "postgresql":
            return 1
        config = self.config if isinstance(self.config, dict) else {}
        value = int(config.get("sync_parallelism", _DEFAULT_SYNC_PARALLELISM))
        return max(1, value)

    def _sync_parallel(self, partitions: tuple[str, ...], parallelism: int, *, deadline: float) -> int:
        """Drain every partition concurrently; fail the run if any partition failed.

        Each worker gets a ``copy_context()`` so the scheduler's ``system_context``
        elevation and the bridge progress reporter propagate into the thread. A
        failed partition never hides a healthy one's progress: the healthy slices
        are already persisted, and the raised error names which partitions broke
        so ``record_sync_error`` reports something actionable.
        """

        landed = 0
        failures: list[tuple[str, Exception]] = []
        with ThreadPoolExecutor(
            max_workers=parallelism, thread_name_prefix=f"channel-{self.pk}-sync"
        ) as pool:
            futures = {
                pool.submit(copy_context().run, self._drain_partition, name, deadline): name
                for name in partitions
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    landed += int(future.result())
                except Exception as error:  # noqa: BLE001 — collected, then re-raised below.
                    failures.append((name, error))
        # Partition threads persisted their cursor slices onto the row; reload the
        # merged cursor so the caller's post-run save cannot clobber it with this
        # instance's stale in-memory copy.
        self.refresh_from_db(fields=["cursor"])
        if failures:
            names = ", ".join(sorted(name for name, _ in failures))
            raise RuntimeError(f"Channel sync failed for partition(s): {names}") from failures[0][1]
        return landed

    def _drain_partition(self, partition: str, deadline: float | None = None) -> int:
        """Drain one partition on this thread — own channel row, own backend, own connection."""

        close_old_connections()
        try:
            # A per-thread channel instance keeps the in-memory cursor private to
            # this partition: a shared instance would let one thread's slice save
            # persist a sibling's pre-ingest advance (a crash could then skip mail).
            channel = type(self)._base_manager.get(pk=self.pk)
            backend = channel.backend
            backend.partition = partition
            # Rebind the progress reporter to this thread's own row: the copied
            # context would otherwise share the parent's reporter — one model
            # instance mutated and saved from every pool thread concurrently.
            with bridge_progress_context(channel):
                return channel._drain(backend, partition=partition, deadline=deadline)
        finally:
            # close_all, not close_old: a healthy young connection on a dying
            # pool thread would otherwise leak to GC under persistent CONN_MAX_AGE.
            connections.close_all()

    def _drain(
        self, backend: ChannelBackend, *, partition: str | None = None, deadline: float | None = None
    ) -> int:
        """Drain one backend batch by batch and ingest each.

        The batch/drain contract lives on :meth:`ChannelBackend.fetch_messages`;
        this loop holds one backend instance across it (that is where the in-run
        paging state and in-memory cursor advance live), releases the backend's
        transport when the run ends either way, and fails loudly when a backend
        stops making progress — a repeated batch with an unmoved cursor would
        otherwise spin a worker forever. A partition drain persists only its own
        cursor slice (under a row lock); the serial drain persists the whole cursor.
        """

        message_model = apps.get_model("messaging", "Message")
        landed = 0
        previous: tuple[tuple[str, ...], Any] | None = None
        reporter = current_bridge_progress()
        if reporter is not None:
            reporter.report(
                str(self.SyncStage.SYNCING),
                message="Starting channel sync",
                details=self._sync_details(backend, partition=partition, landed=landed),
            )
        try:
            while deadline is None or monotonic() < deadline:
                batch = backend.fetch_messages()
                if not batch:
                    break
                current = (tuple(parsed.external_id for parsed in batch), deepcopy(self.cursor))
                if current == previous:
                    raise RuntimeError(
                        f"{type(backend).__name__} returned the same batch twice without advancing its cursor."
                    )
                previous = current
                landed += len(message_model.objects.ingest(batch, channel=self))
                if partition is None:
                    self.save(update_fields=["cursor", "updated_at"])
                else:
                    self._persist_cursor_slice(backend, partition)
                if reporter is not None:
                    reporter.report(
                        str(self.SyncStage.SYNCING),
                        message="Ingested message batch",
                        details=self._sync_details(
                            backend, partition=partition, landed=landed, batch_size=len(batch)
                        ),
                    )
            else:
                # Budget reached with the source not yet drained: the cursor is
                # persisted, so the next scheduled run resumes where this stopped.
                if reporter is not None:
                    reporter.report(
                        str(self.SyncStage.SYNCING),
                        message="Sync time budget reached; resuming next run",
                        details=self._sync_details(
                            backend, partition=partition, landed=landed, budget_exhausted=True
                        ),
                    )
        finally:
            backend.close()
        return landed

    def _sync_details(
        self, backend: ChannelBackend, *, partition: str | None, **extra: Any
    ) -> dict[str, Any]:
        """Return one progress-report detail payload, merged over the stored details."""

        details: dict[str, Any] = {}
        if isinstance(self.sync_progress, dict):
            details = dict(self.sync_progress.get("details") or {})
        details.update({"backend": type(backend).__name__, **extra})
        if partition is not None:
            details["partition"] = partition
        return details

    def _persist_cursor_slice(self, backend: ChannelBackend, partition: str) -> None:
        """Merge one partition's cursor fragment into the persisted cursor, row-locked.

        Parallel partitions each write only the nested slice they own, so a save
        never clobbers a sibling's persisted watermark and never persists a
        sibling's in-memory advance whose batch has not been ingested yet.
        """

        path, value = backend.partition_cursor_slice(partition)
        if not path or value is None:
            return
        with transaction.atomic():
            row = (
                type(self)
                .objects.sudo(reason="messaging.channel.cursor_slice")
                .lock_if_supported()
                .get(pk=self.pk)
            )
            cursor = row.cursor if isinstance(row.cursor, dict) else {}
            node = cursor
            for key in path[:-1]:
                child = node.get(key)
                if not isinstance(child, dict):
                    child = {}
                    node[key] = child
                node = child
            node[path[-1]] = value
            row.cursor = cursor
            row.save(update_fields=["cursor", "updated_at"])


class Thread(SqidMixin, AuditMixin, AngeeModel):
    """An aggregation of related messages — an email conversation or a social post.

    Two orthogonal axes, both base-owned: ``modality`` (the *shape* — email thread /
    direct / group / public post) and ``visibility`` (*who can see it*). A public
    thread's post payload (``subject_url``/``body``/``tags``/``parent``) has no producer
    in this base slice, so the ``social`` addon owns those columns and folds them onto
    this same row through the same-row ``extends`` seam. ``message_count``/
    ``last_message_at`` are denormalised and maintained with ``F()`` deltas by the
    ingest write path.

    ``title`` is a pointer at the content-addressed :class:`Fragment` holding the
    thread's normalised subject — a denormalisation that duplicates nothing (the row
    is shared), replaces the old ``subject``/``subject_normalized`` columns, and makes
    subject-based thread grouping an indexed FK lookup by fragment hash. ``NULL``
    means untitled (a DM); untitled threads never share a hot empty-string fragment,
    which would skew the planner's common-value statistics (Zulip works around the
    same skew with an unprintable DM topic sentinel).

    Identity is the platform-scoped ``MD5(external_id)`` expression constraint: the
    synthetic keys (``subj:<normalized>``, ``msg:<id>``, ``record:<label>:<pk>:<role>``)
    may exceed btree's entry limit (a 7,970-char Apple Mail subject is real), so the
    index carries a fixed digest while the exact value stays in the unbounded column.
    Threads stay platform-scoped (messages are channel-scoped) so the same
    conversation reached through two accounts merges into one thread.
    """

    runtime = True

    class Modality(models.TextChoices):
        """The structural shape of a thread."""

        EMAIL_THREAD = "email_thread", "Email thread"
        DIRECT = "direct", "Direct"
        GROUP = "group", "Group"
        PUBLIC_THREAD = "public_thread", "Public thread"

    class Visibility(models.TextChoices):
        """Who can see a thread."""

        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        PRIVATE = "private", "Private"
        RESTRICTED = "restricted", "Restricted"

    sqid = SqidField(real_field_name="id", prefix="thr_", min_length=8)
    channel = models.ForeignKey(
        "integrate.Integration",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messaging_threads",
    )
    platform = StateField(choices_enum=Handle.Platform, default=Handle.Platform.EMAIL)
    modality = StateField(choices_enum=Modality, default=Modality.EMAIL_THREAD)
    visibility = StateField(choices_enum=Visibility, default=Visibility.PRIVATE)
    external_id = models.TextField(blank=True, default="")
    title = models.ForeignKey(
        "messaging.Fragment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    message_count = models.PositiveIntegerField(default=0, db_index=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    metadata = models.JSONField(blank=True, default=dict)
    host_broadcasts_changes = models.BooleanField(default=False)
    """Whether the attached host opted this record thread into ``changes`` broadcast.

    Stamped from the host's :attr:`ThreadedModelMixin.thread_broadcasts_changes` at
    attachment. A composition fact, not a client-writable column: it never enters the
    thread resource's write surface. Default ``False`` keeps record chatter isolated; a
    host that opts in flips :meth:`broadcasts_changes` on for its thread only.
    """

    objects = ThreadManager()

    class Meta:
        """Django model options for the thread source model."""

        abstract = True
        # NULLs last: a thread that never landed a message must not float above
        # the live conversations (Postgres puts NULLS FIRST on a bare DESC).
        ordering = (models.F("last_message_at").desc(nulls_last=True), "sqid")
        rebac_resource_type = "messaging/thread"
        rebac_id_attr = "sqid"
        constraints = (
            # The digest, not the unbounded value, is what the btree carries; the
            # planner proves `external_id = '<value>'` implies the partial predicate.
            models.UniqueConstraint(
                models.F("platform"),
                MD5("external_id"),
                condition=~models.Q(external_id=""),
                name="uq_thread_platform_external_id",
            ),
        )

    def __str__(self) -> str:
        """Return the thread title for Django displays."""

        title = self.title.text if self.title_id else ""
        return title or f"thread:{self.public_id}"

    def is_record_attached(self) -> bool:
        """Whether this thread is bound to a model row through a ``ThreadAttachment``.

        The one owner of the record-attachment fact, used by both the thread's and the
        message's ``broadcasts_changes`` gates: a record-attached thread is chatter,
        reachable only through the record-scoped ``record_thread`` payload (gated on the
        parent record's read) — the emission mirror of ``ThreadQuerySet.inbox()``.
        """

        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        return attachment_model._base_manager.filter(thread_id=self.pk).exists()

    def broadcasts_changes(self) -> bool:
        """Whether this thread's changes reach the generic ``changes`` subscription.

        Record chatter stays off the generic surface: its own ``owner``/``admin`` read
        would otherwise deliver change events to a subject who cannot read the record.
        A host opts back in per model (a chat room): ``host_broadcasts_changes``, stamped
        from :attr:`ThreadedModelMixin.thread_broadcasts_changes` at attachment, streams
        the thread's changes to its members (who hold ``messaging/thread.reader``) while
        every non-opted record thread stays silent.
        """

        return self.host_broadcasts_changes or not self.is_record_attached()

    def grant_reader(self, *, user: models.Model | None = None, user_id: Any = None) -> None:
        """Grant a user direct ``reader`` access to this thread.

        The one owner of the ``messaging/thread#reader`` write: the relation name and
        the tuple shape live here beside ``permissions.zed``, so a consumer that manages
        room membership composes this instead of hand-writing the tuple with a literal
        relation name. :meth:`revoke_reader` is the mirror, and
        :meth:`~angee.messaging.managers.ThreadFollowerManager.subscribe` with
        ``grant_read`` writes it atomically with the follower row.
        """

        write_relationships(
            [
                RelationshipTuple(
                    resource=to_object_ref(self),
                    relation="reader",
                    subject=_user_subject_ref(user=user, user_id=user_id),
                )
            ]
        )

    def revoke_reader(self, *, user: models.Model | None = None, user_id: Any = None) -> None:
        """Revoke a user's direct ``reader`` access to this thread (mirror of :meth:`grant_reader`)."""

        delete_relationship(
            RelationshipTuple(
                resource=to_object_ref(self),
                relation="reader",
                subject=_user_subject_ref(user=user, user_id=user_id),
            )
        )


class ThreadAttachment(SqidMixin, AuditMixin, RecordRefMixin, AngeeModel):
    """Polymorphic edge attaching one chatter thread to one model row."""

    runtime = True

    class AttachmentRole(models.TextChoices):
        """Why the thread is attached to the target record."""

        CHATTER = "chatter", "Chatter"

    sqid_prefix = "tha_"
    thread = models.ForeignKey(
        "messaging.Thread",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    role = StateField(choices_enum=AttachmentRole, default=AttachmentRole.CHATTER)
    label = models.CharField(max_length=256, blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)

    objects = ThreadAttachmentManager()

    class Meta:
        """Django model options for thread attachments."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "messaging/thread_attachment"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("content_type", "object_id", "role"),
                name="uq_thread_attachment_target_role",
            ),
        )
        indexes = (models.Index(fields=("content_type", "object_id", "role")),)

    def __str__(self) -> str:
        """Return a readable attachment label."""

        return self.label or f"{self.content_type}:{self.object_id}"


class ThreadFollower(SqidMixin, AuditMixin, AngeeModel):
    """A user's per-thread membership row — subscription policy plus read receipt.

    The one row per ``(thread, user)``: it carries how the follower wants updates
    (``notification_policy``/``subtype_keys``) and *where they have read to*
    (``last_read_message``) — the Synapse receipts pattern. Unread is a bounded
    keyset scan from the receipt anchor, never a per-message fan-out row, so read
    state costs O(members × threads) rows regardless of message volume.
    ``attachment`` is set for record-chatter follows and ``NULL`` for a bare
    thread follow (a room membership without a host record).
    """

    runtime = True

    class NotificationPolicy(models.TextChoices):
        """How a follower wants to receive updates for this thread."""

        INBOX = "inbox", "Inbox"
        EMAIL = "email", "Email"
        MUTED = "muted", "Muted"

    sqid_prefix = "tfl_"
    thread = models.ForeignKey(
        "messaging.Thread",
        on_delete=models.CASCADE,
        related_name="followers",
    )
    attachment = models.ForeignKey(
        "messaging.ThreadAttachment",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="followers",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    notification_policy = StateField(choices_enum=NotificationPolicy, default=NotificationPolicy.INBOX)
    subtype_keys = models.JSONField(blank=True, default=list)
    last_read_message = models.ForeignKey(
        "messaging.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    metadata = models.JSONField(blank=True, default=dict)

    objects = ThreadFollowerManager()

    class Meta:
        """Django model options for thread followers."""

        abstract = True
        ordering = ("user_id", "sqid")
        rebac_resource_type = "messaging/thread_follower"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("thread", "user"),
                name="uq_thread_follower_thread_user",
            ),
        )
        indexes = (
            # (thread, user) rides the unique constraint's btree; back the
            # "my followed threads" sweep from the user side.
            models.Index(fields=("user", "thread")),
        )

    def __str__(self) -> str:
        """Return a readable follower label."""

        return f"{self.user_id} follows {self.thread_id}"


class ThreadActivity(SqidMixin, AuditMixin, AngeeModel):
    """A scheduled activity attached to a model chatter thread."""

    runtime = True

    class ActivityStatus(models.TextChoices):
        """Stored lifecycle for an activity."""

        TODO = "todo", "Todo"
        DONE = "done", "Done"
        CANCELED = "canceled", "Canceled"

    sqid_prefix = "tac_"
    thread = models.ForeignKey(
        "messaging.Thread",
        on_delete=models.CASCADE,
        related_name="activities",
    )
    attachment = models.ForeignKey(
        "messaging.ThreadAttachment",
        on_delete=models.CASCADE,
        related_name="activities",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    activity_type = models.CharField(max_length=64, default="todo")
    summary = models.CharField(max_length=256)
    note = models.TextField(blank=True, default="")
    due_date = models.DateField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    feedback = models.TextField(blank=True, default="")
    status = StateField(choices_enum=ActivityStatus, default=ActivityStatus.TODO, db_index=True)
    metadata = models.JSONField(blank=True, default=dict)

    objects = ThreadActivityManager()

    class Meta:
        """Django model options for thread activities."""

        abstract = True
        ordering = ("status", "due_date", "sqid")
        rebac_resource_type = "messaging/thread_activity"
        rebac_id_attr = "sqid"
        indexes = (
            models.Index(fields=("thread", "status", "due_date")),
            models.Index(fields=("attachment", "status", "due_date")),
            models.Index(fields=("user", "status", "due_date")),
        )

    @property
    def activity_state(self) -> str:
        """Return the Odoo-style activity state for presentation."""

        if self.status == self.ActivityStatus.DONE:
            return "done"
        if self.status == self.ActivityStatus.CANCELED:
            return "canceled"
        if self.due_date is None:
            return "planned"
        today = timezone.localdate()
        if self.due_date < today:
            return "overdue"
        if self.due_date == today:
            return "today"
        return "planned"

    def completion_message(self) -> str:
        """Return the chatter body posted when this activity is completed."""

        return f"Activity done: {self.summary}"

    def __str__(self) -> str:
        """Return a readable activity label."""

        return self.summary


class MessageSubtype(SqidMixin, AuditMixin, AngeeModel):
    """A typed chatter event category, mirroring Odoo's message subtypes.

    Subtypes classify system notifications and comments so followers can later
    opt into precise event families. ``model_label`` scopes a subtype to one
    model; an empty value is a global subtype.
    """

    runtime = True

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
        ("activity_done", "Activity done", "Activity done"),
    )

    sqid_prefix = "mst_"
    key = models.CharField(max_length=128)
    # No standalone index: ``model_label`` is the leading column of
    # ``uq_message_subtype_model_key``, whose btree already serves a lone
    # ``model_label`` lookup — a separate single-column index would be redundant.
    model_label = models.CharField(max_length=128, blank=True, default="")
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


class Message(SqidMixin, AuditMixin, AngeeModel):
    """One message — the unit of a thread. The root post is itself a Message.

    Dedup key is ``(channel, external_id)`` — one row per provider event per
    source, carried by the ``MD5(external_id)`` expression constraint so an
    unbounded provider id never overflows a btree entry. The same event reached
    through two channels is two messages (related through :class:`MessageEdge` /
    a shared thread), matching the "message identity ≠ content identity" rule.
    ``parent`` is the single-parent reply pointer (In-Reply-To); richer
    cross-message relations live on :class:`MessageEdge`. The body — including a
    sparse ``TITLE`` part for the subject and ``HEADER`` parts for retained
    envelope headers — is the :class:`Part` tree; raw envelope recipients are kept
    in ``metadata`` as the lossless source behind :class:`Participant`.

    Edits are data, not shadow rows: ``edit_history`` appends newest-first
    ``{edited_at, edited_by_id, prev_fragment_hashes}`` entries while the replaced
    text survives as immutable content-addressed fragments — no per-save history
    table doubling the hot write path.
    """

    runtime = True

    class Direction(models.TextChoices):
        """Whether a message came in, went out, or is internal."""

        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"
        INTERNAL = "internal", "Internal"

    class MessageStatus(models.TextChoices):
        """Lifecycle + public moderation state of a message."""

        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        SYNCED = "synced", "Synced"
        EDITED = "edited", "Edited"
        HIDDEN = "hidden", "Hidden"
        REMOVED = "removed", "Removed"
        FAILED = "failed", "Failed"

    class MessageKind(models.TextChoices):
        """Odoo-style functional kind of a message."""

        COMMENT = "comment", "Comment"
        EMAIL = "email", "Email"
        NOTIFICATION = "notification", "Notification"
        AUTO_COMMENT = "auto_comment", "Auto comment"
        USER_NOTIFICATION = "user_notification", "User notification"
        OUT_OF_OFFICE = "out_of_office", "Out of office"

    sqid = SqidField(real_field_name="id", prefix="msg_", min_length=8)
    thread = models.ForeignKey(
        "messaging.Thread",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
        # Covered: every composite index below leads with thread (the Zulip
        # covered-FK rule — a redundant single-column index can misprice plans).
        db_index=False,
    )
    channel = models.ForeignKey(
        "integrate.Integration",
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
    edit_history = models.JSONField(blank=True, default=list)
    metadata = models.JSONField(blank=True, default=dict)

    objects = MessageManager()

    class Meta:
        """Django model options for the message source model."""

        abstract = True
        ordering = ("-sent_at", "sqid")
        rebac_resource_type = "messaging/message"
        rebac_id_attr = "sqid"
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
            # The exact keyset the feed orders and cursors by
            # (``_MESSAGE_ORDER_ANNOTATION`` + pk tiebreak): one expression index
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

    def content_edit_error(self) -> str | None:
        """Return why this message's body cannot be edited, or ``None`` if it can.

        The Odoo mail edit rule: only an internally authored plain comment carrying
        no tracking values may be re-edited; a tracked, ingested, or system message
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
        if self._has_tracking_values():
            return "Messages with tracking values cannot be edited."
        return None

    def _has_tracking_values(self) -> bool:
        """Return whether this message carries tracking values, reusing any prefetch."""

        cache = getattr(self, "_prefetched_objects_cache", None)
        if cache is not None and "tracking_values" in cache:
            return bool(self.tracking_values.all())
        return self.tracking_values.exists()

    def can_edit(self, *, post_access: bool) -> bool:
        """Return whether a post-authorised actor may edit this message's body.

        Composes the caller-supplied record thread post access with the mail edit rule
        (:meth:`content_edit_error`) — the exact two-part guard the
        ``update_record_message`` mutation enforces. Owned once here so the write guard
        and the ``can_edit`` projection stay one predicate; the caller resolves (and, in
        the schema, memoizes per thread) ``post_access`` through
        :meth:`ThreadedModelMixin.can_post`.
        """

        return post_access and self.content_edit_error() is None

    def can_delete(self, *, post_access: bool) -> bool:
        """Return whether a post-authorised actor may delete this message.

        Deletion carries no mail-kind restriction of its own, so the record thread's
        post access is the whole gate; owned beside :meth:`can_edit` so the projection
        mirrors the ``delete_record_message`` mutation without reassembling the rule.
        """

        return post_access

    def reaction_groups(self, user: Any = None) -> list[MessageReactionGroup]:
        """Return this message's reactions grouped by content, with ``user``'s state.

        The chatter feed shows reactions grouped by content — each with a count, the
        reacting handles, and whether ``user`` reacted. A ``reactions`` prefetch is
        reused when present so a page of messages groups without a per-row query. This
        is the single owner of the grouping fact; the GraphQL resolver only projects it.
        """

        cache = getattr(self, "_prefetched_objects_cache", None)
        if cache is not None and "reactions" in cache:
            reactions = list(self.reactions.all())
        else:
            reactions = list(
                apps.get_model("messaging", "Reaction")
                ._base_manager.filter(message=self)
                .select_related("handle")
                .order_by("pk")
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
                handles=tuple(row.handle for row in rows if row.handle is not None),
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

        if self.thread_id is None:
            return None
        attachment = (
            apps.get_model("messaging", "ThreadAttachment")
            ._base_manager.filter(thread_id=self.thread_id)
            .first()
        )
        if attachment is None:
            return None
        target = attachment.target
        return target if isinstance(target, ThreadedModelMixin) else None

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
                    return part.fragment.text if part.fragment_id else ""
            return ""
        part = (
            apps.get_model("messaging", "Part")
            ._base_manager.filter(message=self, role=Part.PartRole.TITLE)
            .select_related("fragment")
            .first()
        )
        if part is None or part.fragment_id is None:
            return ""
        return part.fragment.text

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
        return not self.thread.is_record_attached()


class ThreadNotification(SqidMixin, AuditMixin, AngeeModel):
    """One per-recipient *delivery* row for a chatter message — a ledger, not read state.

    The Angee equivalent of Odoo's ``mail.notification``, narrowed to what only a
    per-recipient row can own: the delivery lifecycle (ready → sent → bounced) and
    its failure diagnostics. Read state moved to the follower's positional receipt
    (:attr:`ThreadFollower.last_read_message`), so a row exists only when a
    delivery actually needs tracking — an inbox-policy follower generates none.
    """

    runtime = True

    class NotificationType(models.TextChoices):
        """How this notification should be delivered."""

        INBOX = "inbox", "Inbox"
        EMAIL = "email", "Email"

    class NotificationStatus(models.TextChoices):
        """Delivery lifecycle for a notification."""

        READY = "ready", "Ready to send"
        PROCESS = "process", "Processing"
        PENDING = "pending", "Sent"
        SENT = "sent", "Delivered"
        BOUNCE = "bounce", "Bounced"
        EXCEPTION = "exception", "Exception"
        CANCELED = "canceled", "Canceled"

    sqid_prefix = "ntf_"
    thread = models.ForeignKey(
        "messaging.Thread",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    attachment = models.ForeignKey(
        "messaging.ThreadAttachment",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    follower = models.ForeignKey(
        "messaging.ThreadFollower",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    notification_type = StateField(choices_enum=NotificationType, default=NotificationType.INBOX, db_index=True)
    notification_status = StateField(
        choices_enum=NotificationStatus,
        default=NotificationStatus.READY,
        db_index=False,
    )
    failure_type = models.CharField(max_length=64, blank=True, default="")
    failure_reason = models.TextField(blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)

    objects = ThreadNotificationManager()

    class Meta:
        """Django model options for thread notifications."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "messaging/thread_notification"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("message", "user"),
                name="uq_thread_notification_message_user",
            ),
        )
        indexes = (
            models.Index(fields=("thread", "user")),
            # The delivery worker's queue: only undelivered rows enter the index
            # (the Zulip scheduled-send pattern), so it stays tiny at any volume.
            models.Index(
                fields=("notification_status", "created_at"),
                condition=models.Q(notification_status__in=("ready", "process")),
                name="ix_thread_notification_pending",
            ),
            # Delivery-error surfacing per user.
            models.Index(
                fields=("user", "notification_status"),
                condition=models.Q(notification_status__in=("bounce", "exception")),
                name="ix_thread_notification_failed",
            ),
        )

    def __str__(self) -> str:
        """Return a readable notification label."""

        return f"{self.user_id} notified for {self.message_id}"


class TrackingValue(SqidMixin, AuditMixin, AngeeModel):
    """One tracked old/new field value attached to a chatter message."""

    runtime = True

    sqid_prefix = "mtv_"
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="tracking_values",
    )
    position = models.PositiveIntegerField(default=0)
    field_name = models.CharField(max_length=128)
    field_label = models.CharField(max_length=160)
    field_type = models.CharField(max_length=64, blank=True, default="")
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    old_display = models.TextField(blank=True, default="")
    new_display = models.TextField(blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        """Django model options for tracking values."""

        abstract = True
        ordering = ("message", "position", "sqid")
        rebac_resource_type = "messaging/tracking_value"
        rebac_id_attr = "sqid"
        indexes = (
            models.Index(fields=("message", "position")),
            models.Index(fields=("field_name",)),
        )

    def __str__(self) -> str:
        """Return a compact tracked change label."""

        return f"{self.field_label}: {self.old_display} -> {self.new_display}"


class Fragment(SqidMixin, AuditMixin, AngeeModel):
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

    runtime = True

    class FragmentKind(models.TextChoices):
        """What a fragment of text is."""

        PARAGRAPH = "paragraph", "Paragraph"
        QUOTE = "quote", "Quote"
        SIGNATURE = "signature", "Signature"
        CODE = "code", "Code"
        HEADER = "header", "Header"

    sqid = SqidField(real_field_name="id", prefix="frg_", min_length=8)
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
        """How many parts (across all messages) share this fragment.

        Deliberately corpus-global: the row is unscoped substrate and the count is
        the dedup fact itself — an actor-scoped count would falsify it. Row counts
        only, never content; each probe rides the fragment FK index (sub-ms at the
        measured million-message scale), bounded by the page size that reads it.
        """

        return int(apps.get_model("messaging", "Part")._base_manager.filter(fragment=self).count())

    def message_count(self) -> int:
        """How many distinct messages reference this fragment (see :meth:`part_count`)."""

        return int(
            apps.get_model("messaging", "Part")
            ._base_manager.filter(fragment=self)
            .values("message_id")
            .distinct()
            .count()
        )

    def __str__(self) -> str:
        """Return a truncated preview for Django displays."""

        return (self.text[:60] + "…") if len(self.text) > 60 else self.text


class Part(SqidMixin, AuditMixin, AngeeModel):
    """One recursive body node of a message (the MIME/JMAP part shape, one model).

    ``type``/``role`` is a genuine discriminator, not MTI: a ``multipart/*`` is a
    container; a text part references a :class:`Fragment`; a byte part references a
    ``storage.File``. Attachments are ``disposition=attachment`` + ``file``; inline
    images are ``disposition=inline`` + ``cid``.
    """

    runtime = True

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

    sqid = SqidField(real_field_name="id", prefix="prt_", min_length=8)
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
        related_name="+",
    )

    objects = PartManager()

    class Meta:
        """Django model options for the part source model."""

        abstract = True
        ordering = ("message", "position", "sqid")
        rebac_resource_type = "messaging/part"
        rebac_id_attr = "sqid"
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


class MessageEdge(SqidMixin, AuditMixin, AngeeModel):
    """One typed cross-message relation — the unified quote/reply/reference graph.

    ``Message.parent`` stays the single-parent reply pointer and ``Thread`` is
    membership; this carries the M2M/derived relations. A derived *quote* edge sets
    ``fragment`` (the shared content-addressed text) and a ``confidence``; both
    direction indexes back the bulk BFS.
    """

    runtime = True

    class EdgeKind(models.TextChoices):
        """The type of cross-message relation.

        ``quote`` is produced by the messaging quotation builder; ``mention``/
        ``crosspost``/``forward`` are produced by the ``social`` feed overlay onto this
        shared graph (through ``MessageEdgeManager.relate``). ``reply`` (carried instead
        by ``Message.parent``) and ``duplicate`` have no producer in the shipped slice.
        """

        REPLY = "reply", "Reply"
        QUOTE = "quote", "Quote"
        MENTION = "mention", "Mention"
        CROSSPOST = "crosspost", "Crosspost"
        FORWARD = "forward", "Forward"
        DUPLICATE = "duplicate", "Duplicate"

    sqid = SqidField(real_field_name="id", prefix="mge_", min_length=8)
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
        rebac_id_attr = "sqid"
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


class Participant(SqidMixin, AuditMixin, AngeeModel):
    """A Handle-keyed membership of a thread/message — the queryable recipient row.

    The raw to/cc/bcc stays in ``Message.metadata`` as the lossless source; this is
    its queryable projection, so the inbox can group/filter by participant.
    """

    runtime = True

    class ParticipantRole(models.TextChoices):
        """The RFC-5322 envelope role of a participant.

        Base messaging owns only the mail-envelope roles. The ``social`` addon layers
        public-membership semantics (``author``/``owner``/``moderator``/``viewer``) as
        additional documented string values on this same ``role`` field: a same-row
        ``extends`` cannot widen an existing enum, and ``StateField`` stores the raw
        string, so social writes those values without a schema change here.
        """

        FROM = "from", "From"
        TO = "to", "To"
        CC = "cc", "Cc"
        BCC = "bcc", "Bcc"

    sqid = SqidField(real_field_name="id", prefix="ptp_", min_length=8)
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
        rebac_id_attr = "sqid"
        constraints = (
            # One row per envelope fact; the write path dedupes a repeated
            # address, the constraint keeps a concurrent rebuild honest.
            models.UniqueConstraint(
                fields=("message", "handle", "role"),
                condition=models.Q(message__isnull=False),
                name="uq_participant_message_handle_role",
            ),
        )

    def __str__(self) -> str:
        """Return a readable participant label for Django displays."""

        return f"{self.handle_id} ({self.role})"


class Reaction(SqidMixin, AuditMixin, AngeeModel):
    """One attributed reaction to a message, keyed by the reactor's parties ``Handle``.

    This is the single per-actor reaction store: ``MessageManager.set_reaction``
    (reached from ``ThreadedModelMixin.message_reaction``) adds/removes/toggles a row
    per ``(message, handle, reaction)``, and ``Message.reaction_groups`` reads the rows
    back grouped by content for the chatter feed. The ``social`` addon reuses this same
    table for public reactions (``like``/``repost`` are reaction values on the shared
    ``messaging.Message``), so there is one reaction table, not two; the rolled-up
    public counts live separately on ``social.PostMetrics``.

    Dedup — one reaction of a given content per reactor — is enforced only for an
    *attributed* row (``handle`` set): the unique constraint is partial on
    ``handle IS NOT NULL``. A row whose ``handle`` was ``SET_NULL`` by a later
    ``Handle`` delete is de-attributed history, not a live reactor, so it falls out
    of the invariant rather than colliding (SQL treats NULLs as distinct regardless).
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="rxn_", min_length=8)
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
        rebac_id_attr = "sqid"
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
        max_length = cls._meta.get_field("reaction").max_length
        if max_length is not None and len(cleaned) > max_length:
            raise ValueError("Reaction is too long.")
        return cleaned


class MessageStar(SqidMixin, AuditMixin, AngeeModel):
    """A user's Odoo-style star/favorite marker on a message."""

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="msr_", min_length=8)
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
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("message", "user"),
                name="uq_message_star_message_user",
            ),
        )

    def __str__(self) -> str:
        """Return a readable message star label."""

        return f"{self.user_id} starred {self.message_id}"


def _message_suggestion_user(user_model: type[models.Model], candidate: Any) -> models.Model | None:
    """Return a user row from a candidate object/id for recipient suggestions."""

    if candidate is None:
        return None
    if isinstance(candidate, user_model):
        return candidate
    if isinstance(candidate, models.Model):
        candidate = candidate.pk
    if candidate in (None, ""):
        return None
    return user_model._default_manager.filter(pk=candidate).first()
