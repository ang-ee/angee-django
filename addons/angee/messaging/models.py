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
mention) live on :class:`MessageEdge`. Ingestion idempotency rests on
``(platform, external_id)`` unique constraints; the write path lives on the
managers.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from django.utils import timezone
from django.utils.text import capfirst
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    current_actor,
    delete_relationship,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.managers import RebacManager

from angee.base.actors import actor_user_id
from angee.base.fields import ImplClassField, SqidField, StateField
from angee.base.mixins import AuditMixin, HistoryMixin, SqidMixin
from angee.base.models import AngeeModel, public_id_for
from angee.integrate.models import Bridge
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
                subject=self.message_thread_subject(),
            )
        return attachment_model.objects.for_record(self, role=self.thread_attachment_role)

    def message_post(
        self,
        body: str,
        *,
        subject: str = "",
        attachments: tuple[models.Model, ...] = (),
        recipient_user_ids: tuple[Any, ...] = (),
        autofollow_recipients: bool = False,
        message_type: Message.MessageKind | None = None,
        subtype_key: str = "comment",
        parent: models.Model | None = None,
    ) -> models.Model:
        """Post an internal comment on this row's chatter thread.

        ``message_type`` defaults to :attr:`Message.MessageKind.COMMENT` (resolved by
        the message write path), keeping the enum the single source of truth.
        """

        return self._message_post(
            body,
            subject=subject,
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
        subject: str = "",
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
            subject=subject,
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
        subject: str = "",
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
            subject=subject,
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
        """Remove Odoo-style needaction from ``message`` for ``user``."""

        if not self._message_read_allowed():
            raise PermissionDenied(
                f"Marking messages done on {self._meta.label} requires {self.thread_read_access!r} access."
            )
        attachment = self.message_thread_attachment(create=False)
        if attachment is None or message.thread_id != attachment.thread_id:
            raise ValueError("Message does not belong to this record thread.")
        notification_model = apps.get_model("messaging", "ThreadNotification")
        return int(notification_model.objects.mark_read_for_message(message, user=user))

    def _message_post(
        self,
        body: str,
        *,
        subject: str,
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
            subject=subject,
            attachments=attachments,
            message_type=message_type,
            subtype_key=subtype_key,
            parent=parent,
            tracking_values=tracking_values,
            recipient_user_ids=recipient_user_ids,
        )
        owner_id = _owner_user_id(self)
        follower_model = apps.get_model("messaging", "ThreadFollower")
        if autofollow_author and owner_id is not None:
            follower_model.objects.subscribe(
                self,
                user_id=owner_id,
                role=self.thread_attachment_role,
            )
        if autofollow_recipients:
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
        subject: str = "",
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
        return message_model.objects.post_to_thread(
            attachment.thread,
            body=body,
            subject=subject or self.message_thread_subject(),
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

    def message_thread_subject(self) -> str:
        """Return the default subject for this row's chatter thread."""

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

    objects = RebacManager()

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
        """Drain the backend batch by batch and ingest each (the Bridge child-sync contract).

        The batch/drain contract lives on :meth:`ChannelBackend.fetch_messages`;
        this loop holds one backend instance across it (that is where the in-run
        paging state and in-memory cursor advance live), releases the backend's
        transport when the run ends either way, and fails loudly when a backend
        stops making progress — a repeated batch with an unmoved cursor would
        otherwise spin a worker forever. Reports how many messages landed.
        """

        message_model = apps.get_model("messaging", "Message")
        backend = self.backend
        landed = 0
        previous: tuple[tuple[str, ...], Any] | None = None
        try:
            while batch := backend.fetch_messages():
                current = (tuple(parsed.external_id for parsed in batch), deepcopy(self.cursor))
                if current == previous:
                    raise RuntimeError(
                        f"{type(backend).__name__} returned the same batch twice without advancing its cursor."
                    )
                previous = current
                landed += len(message_model.objects.ingest(batch, channel=self))
        finally:
            backend.close()
        return landed


class Thread(SqidMixin, AuditMixin, AngeeModel):
    """An aggregation of related messages — an email conversation or a social post.

    Two orthogonal axes, both base-owned: ``modality`` (the *shape* — email thread /
    direct / group / public post) and ``visibility`` (*who can see it*). A public
    thread's post payload (``subject_url``/``body``/``tags``/``parent``) has no producer
    in this base slice, so the ``social`` addon owns those columns and folds them onto
    this same row through the same-row ``extends`` seam. ``message_count``/
    ``last_message_at`` are denormalised and maintained with ``F()`` deltas by the
    ingest write path.
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
    external_id = models.CharField(max_length=512, blank=True, default="")
    subject = models.CharField(max_length=512, blank=True, default="")
    subject_normalized = models.CharField(max_length=512, blank=True, default="", db_index=True)
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
        ordering = ("-last_message_at", "sqid")
        rebac_resource_type = "messaging/thread"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("platform", "external_id"),
                condition=~models.Q(external_id=""),
                name="uq_thread_platform_external_id",
            ),
        )

    def __str__(self) -> str:
        """Return the thread subject for Django displays."""

        return self.subject or f"thread:{self.public_id}"

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


class ThreadAttachment(SqidMixin, AuditMixin, AngeeModel):
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

    @property
    def target_model_label(self) -> str:
        """Return the ``app_label.ModelName`` label of the attached target's model.

        Resolves the model from the process-cached ``ContentType`` by id
        (``ContentType.objects.get_for_id``), so a list of rows projects the pointer
        without a per-row ``content_type`` join.
        """

        model_class = ContentType.objects.get_for_id(self.content_type_id).model_class()
        return model_class._meta.label if model_class is not None else ""

    @property
    def target_public_id(self) -> str:
        """Return the attached target's stable public id (its sqid).

        Encoded from the stored ``content_type``/``object_id`` alone — via the
        process-cached ``ContentType.objects.get_for_id`` — so the parent pointer
        resolves without loading (or re-gating, or per-row joining) the target row;
        navigating the pointer re-gates through the target's own record read.
        """

        model_class = ContentType.objects.get_for_id(self.content_type_id).model_class()
        if model_class is None:
            return ""
        return public_id_for(model_class, self.object_id)

    def __str__(self) -> str:
        """Return a readable attachment label."""

        return self.label or f"{self.content_type}:{self.object_id}"


class ThreadFollower(SqidMixin, AuditMixin, AngeeModel):
    """A user's subscription to a model-attached chatter thread."""

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
                fields=("attachment", "user"),
                name="uq_thread_follower_attachment_user",
            ),
        )
        indexes = (
            models.Index(fields=("thread", "user")),
            models.Index(fields=("attachment", "user")),
        )

    def __str__(self) -> str:
        """Return a readable follower label."""

        return f"{self.user_id} follows {self.attachment_id}"


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


class Message(SqidMixin, AuditMixin, AngeeModel, HistoryMixin):
    """One message — the unit of a thread. The root post is itself a Message.

    Dedup key is ``(platform, external_id)`` (NOT thread-scoped — the same comment
    can surface under two threads). ``parent`` is the single-parent reply pointer
    (In-Reply-To); richer cross-message relations live on :class:`MessageEdge`. The
    body is the :class:`Part` tree; raw envelope recipients are kept in ``metadata``
    as the lossless source behind :class:`Participant`.
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
    external_id = models.CharField(max_length=512, blank=True, default="")
    subject = models.CharField(max_length=512, blank=True, default="")
    preview = models.CharField(max_length=512, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    received_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(blank=True, default=dict)

    objects = MessageManager()

    class Meta:
        """Django model options for the message source model."""

        abstract = True
        ordering = ("-sent_at", "sqid")
        rebac_resource_type = "messaging/message"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("platform", "external_id"),
                condition=~models.Q(external_id=""),
                name="uq_message_platform_external_id",
            ),
        )
        indexes = (
            # The chatter feed reads a single thread ordered by send time
            # (``MessageManager.for_record``); back that hot filter+order with a
            # composite so a large public thread does not scan on ``sent_at`` alone.
            models.Index(fields=("thread", "sent_at")),
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

    def __str__(self) -> str:
        """Return a readable message label for Django displays."""

        return self.subject or self.preview or f"message:{self.public_id}"

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
    """One per-recipient notification/read row for a chatter message.

    This is the Angee equivalent of Odoo's ``mail.notification`` for
    model-attached chatter: followers receive notification rows when a message is
    posted, and the row owns delivery/read state for that recipient.
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
        db_index=True,
    )
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    failure_type = models.CharField(max_length=64, blank=True, default="")
    failure_reason = models.TextField(blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)

    objects = ThreadNotificationManager()

    class Meta:
        """Django model options for thread notifications."""

        abstract = True
        ordering = ("is_read", "-created_at", "sqid")
        rebac_resource_type = "messaging/thread_notification"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("message", "user"),
                name="uq_thread_notification_message_user",
            ),
        )
        indexes = (
            models.Index(fields=("thread", "user", "is_read")),
            models.Index(fields=("attachment", "user", "is_read")),
            models.Index(fields=("user", "is_read", "notification_status")),
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

    objects = FragmentManager()

    class Meta:
        """Django model options for the fragment source model."""

        abstract = True
        # No rebac_resource_type: a content-addressed shared row is unscoped
        # substrate, gated through the owning Part/Message (see the class docstring).

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
        """The semantic role of a part — the primary quotation/search filter axis."""

        BODY = "body", "Body"
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
    cid = models.CharField(max_length=256, blank=True, default="")
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
