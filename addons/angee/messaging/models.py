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
per source), and channel-scoped for source threads. Cross-account display identity
uses reversible same_as evidence; the digest remains an index implementation detail.
The write path lives on the managers.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models.functions import MD5, Coalesce
from django.utils import timezone
from django.utils.text import capfirst
from rebac import (
    CheckItem,
    PermissionDenied,
    SubjectRef,
    actor_context,
    current_actor,
    system_context,
    to_object_ref,
    to_subject_ref,
)
from rebac.actors import is_sudo
from rebac.backends import backend
from rebac.resources import model_resource_type

from angee.base.actors import actor_user_id
from angee.base.fields import StateField
from angee.base.identity import instance_from_public_id
from angee.base.impl import ImplClassField
from angee.base.merge import MergeableMixin
from angee.base.mixins import (
    AuditMixin,
    CreationKeyMixin,
    HistoryMixin,
    ModelHistory,
    OptimisticLockMixin,
    OwnerMixin,
    SourceKind,
    TrashMixin,
)
from angee.base.models import AngeeDataModel
from angee.base.refs import MergePolicy, RecordRefMixin, RetainedGenericRelation, generic_pointer_model
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.base.serialization import strip_null_bytes
from angee.integrate.models import Bridge, IntegrationCreateMode
from angee.messaging.backends import ChannelBackend
from angee.messaging.delivery import queue_message_delivery
from angee.messaging.managers import (
    ChannelManager,
    FragmentManager,
    MessageEdgeManager,
    MessageManager,
    MessageStarManager,
    PartManager,
    ReactionManager,
    ThreadAttachmentManager,
    ThreadFollowerManager,
    ThreadManager,
    ThreadNotificationManager,
)
from angee.messaging.resources import MessageResource
from angee.messaging.source_copies import SourceCopyMixin, backfill_email_identity
from angee.messaging.webforms import WebformSpec, default_webform_schema
from angee.parties.models import Handle
from angee.resources.mixins import ResourceLoadMixin
from angee.tags.models import TaggedModel


def _actor_user_id(instance: models.Model) -> Any | None:
    """Return attribution for the record-bound actor, falling back to ambient.

    Posting uses the same precedence for replay identity unless the caller supplies
    ``creation_actor`` explicitly. That override does not change attribution or access.
    """

    actor_getter = getattr(instance, "actor", None)
    actor = actor_getter() if callable(actor_getter) else None
    if actor is None:
        actor = current_actor()
    return actor_user_id(actor)


def _user_subject_ref(*, user: Any = None, user_id: Any = None) -> SubjectRef:
    """Return the REBAC subject ref for a user instance or a bare user id.

    The user model owns its subject identity, so a bare ``user_id`` is loaded and
    resolved through :func:`to_subject_ref` rather than assembling an authorization
    ref by hand.
    """

    if user is not None:
        return to_subject_ref(user)
    if user_id is None:
        raise ValueError("A user or user_id is required to build a subject reference.")
    return to_subject_ref(get_user_model()._base_manager.get(pk=user_id))


class SourceIdentityRule(ResourceLoadMixin):
    """Observe pre-upgrade email copies after the rule and its grants load."""

    extends = "provenance.Rule"

    class Meta:
        abstract = True

    @classmethod
    def after_resource_load(cls, instances: Iterable[Any], *, tier: str, source: str) -> None:
        instances = tuple(instances)
        for rule in instances:
            if rule.key == "messaging.source_identity":
                backfill_email_identity(rule)
        super().after_resource_load(instances, tier=tier, source=source)


class NotificationPolicy(models.TextChoices, StrEnum):
    """How a follower or derived audience member wants record updates."""

    INBOX = "inbox", "Inbox"
    EMAIL = "email", "Email"
    MUTED = "muted", "Muted"


@dataclass(frozen=True, kw_only=True)
class NotificationPreference:
    """Shared policy and subtype selection for followers and team members."""

    notification_policy: NotificationPolicy = NotificationPolicy.INBOX
    subtype_keys: tuple[str, ...] = ()

    def subscribed_subtype_q(self) -> models.Q:
        """Project the preference into a message queryset predicate."""

        if self.notification_policy == NotificationPolicy.MUTED:
            return models.Q(pk__in=[])
        if self.subtype_keys:
            return models.Q(subtype__key__in=self.subtype_keys)
        return models.Q(subtype__isnull=True) | models.Q(subtype__default=True, subtype__internal=False)

    def is_subscribed_to(self, subtype: Any) -> bool:
        """Apply the same preference to one message subtype."""

        if self.notification_policy == NotificationPolicy.MUTED:
            return False
        if self.subtype_keys:
            return subtype is not None and str(subtype.key) in self.subtype_keys
        return subtype is None or (bool(subtype.default) and not bool(subtype.internal))


@dataclass(frozen=True, kw_only=True)
class AudienceMember(NotificationPreference):
    """A party key and notification preference, without loading its contact row."""

    party_id: Any


class ThreadAudienceMixin(models.Model):
    """Contract implemented by a team that contributes a record's audience."""

    class Meta:
        """Django options for the audience contract."""

        abstract = True

    def thread_audience(self) -> Iterable[AudienceMember]:
        """Return the team's current members and their notification preferences."""

        raise NotImplementedError


def chatter_record(model_label: str, public_id: str) -> models.Model | None:
    """Return the record ``public_id`` names, read under the actor, when ``model_label`` keeps chatter.

    An unknown label or a model without chatter raises ``ValueError``; a missing or
    unreadable record is ``None``.
    """

    try:
        model = apps.get_model(model_label)
    except (LookupError, ValueError) as error:
        raise ValueError(f"Unknown model {model_label!r}.") from error
    if not issubclass(model, ThreadedModelMixin):
        raise ValueError(f"{model._meta.label} does not inherit ThreadedModelMixin.")
    try:
        record = instance_from_public_id(model, str(public_id), queryset=model._default_manager.all())
        if record is None:
            return None
        attachment = record.message_thread_attachment(create=False)
        return attachment.target if attachment is not None else record
    except ImproperlyConfigured as error:
        raise ValueError(str(error)) from error


class ThreadedModelMixin(models.Model):
    """Add Odoo-style chatter thread behavior to a model row.

    A concrete model opts in by inheriting this abstract mixin. The model remains
    the owner of the record; messaging owns the attached thread edge and the
    message write path.

    **Placement across MTI.** The chatter edge keys on the record's canonical target —
    its topmost REBAC-typed MTI ancestor (:func:`angee.base.refs.generic_pointer_target`)
    — while the retained reverse :attr:`thread_attachments` relation and ``pre_delete``
    teardown filter at the model that composes this mixin. Compose the mixin on that same
    topmost REBAC-typed ancestor (``parties.Party``, not ``parties.Person``), so the write
    content type and the teardown content type match; ``ensure_for_record`` fails fast on a
    child-composed / ancestor-uncomposed split (the placement invariant in
    :mod:`angee.base.refs`).
    """

    thread_attachment_role: ClassVar[str] = "chatter"
    """The attachment role used for the model's primary chatter thread."""

    @transaction.atomic
    def revoke_record_access(self, relation: str, subject: models.Model | SubjectRef) -> None:
        """End person follows whose read disappears with this direct grant."""

        cast(Any, super()).revoke_record_access(relation, subject)
        apps.get_model("messaging", "ThreadFollower").objects.end_unreadable_for_record(
            self, role=self.thread_attachment_role,
        )

    @classmethod
    def thread_messages_expression(cls, record_id: Any) -> models.QuerySet:
        """Return messages of the record's primary thread for a SQL subquery.

        This is a structural, unscoped expression. The calling projection owns
        authorization of the information derived from these message rows.
        """

        owner = generic_pointer_model(cls)
        content_type = ContentType.objects.filter(
            app_label=owner._meta.app_label,
            model=owner._meta.model_name,
        ).values("pk")[:1]
        direct = models.Q(
            thread__attachments__content_type_id=models.Subquery(content_type),
            thread__attachments__object_id=record_id,
            thread__attachments__role=cls.thread_attachment_role,
        )
        redirected = models.Q(
            thread__merged_into__attachments__content_type_id=models.Subquery(content_type),
            thread__merged_into__attachments__object_id=record_id,
            thread__merged_into__attachments__role=cls.thread_attachment_role,
        )
        return system_queryset(apps.get_model("messaging", "Message")).filter(direct | redirected).distinct()

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

    thread_team_field: ClassVar[str | None] = None
    """Foreign key whose team implements :class:`ThreadAudienceMixin`."""

    thread_suggested_recipient_fields: ClassVar[tuple[str, ...]] = ()
    """User FK fields suggested as chatter recipients, like Odoo's ``user_id``."""

    thread_attachments = RetainedGenericRelation(
        "messaging.ThreadAttachment",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="%(app_label)s_%(class)s",
    )
    """Reverse edge to this row's chatter attachments.

    This retained reverse relation supplies queries without scheduling attachment
    cascades before the owner's cleanup. The ``pre_delete`` receiver on every
    threaded model delegates to ``ThreadAttachmentManager.teardown_for_record``
    inside Django's delete transaction, on instance and queryset deletion alike.
    It rebinds retained chatter bookkeeping before deleting the old attachment;
    an unshared, unmerged private thread and its messages still cascade normally.

    Record owners contribute read access in their ``permissions.extends.zed``:
    declare a relation on ``messaging/thread`` backed by
    ``attachments__<app_label>_<model_name>``, filtered by
    ``attachments__role: chatter``, then add ``<relation>->read`` (or the
    declared ``thread_read_access``). The filter excludes source evidence edges.
    Use the canonical MTI ancestor's name. Messaging never imports record owners
    or copies their relationships into grants.
    """

    class Meta:
        """Django model options for the thread behavior mixin."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the record and subscribe its creator; HistoryMixin owns field changes."""

        creating = self._state.adding or self.pk is None
        super().save(*args, **kwargs)
        if creating:
            with self._own_write_scope():
                self._message_after_create()

    def _own_write_scope(self) -> AbstractContextManager[None]:
        """Make this row's write scope ambient for the chatter its save writes.

        A queryset's pinned actor or system scope reaches the row but not the
        threads, followers and notes written on its behalf, which follow the
        ambient scope. A pinned actor then owns a new thread as an ambient one
        would; an elevated insert writes them as system bookkeeping.
        """

        actor, unscoped = self.effective_actor()
        if unscoped and not is_sudo():
            return system_context(reason="messaging.record_write")
        if actor is not None and actor != current_actor():
            return actor_context(actor)
        return nullcontext()

    def delete(self, using: str | None = None, keep_parents: bool = False) -> tuple[int, dict[str, int]]:
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
            return super().delete(using=using, keep_parents=keep_parents)

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
        client_creation_key: str | None = None,
        creation_actor: SubjectRef | None = None,
    ) -> models.Model:
        """Post an internal comment on this row's chatter thread.

        ``message_type`` defaults to :attr:`Message.MessageKind.COMMENT` (resolved by
        the message write path), keeping the enum the single source of truth. A chatter
        comment carries no title of its own — the thread's title fragment is the label.
        ``creation_actor`` supplies replay identity for a keyed system job; otherwise
        the record-bound actor takes precedence over the ambient actor.
        """

        return self._message_post(
            body,
            attachments=attachments,
            recipient_user_ids=recipient_user_ids,
            autofollow_recipients=autofollow_recipients,
            message_type=message_type,
            subtype_key=subtype_key,
            parent=parent,
            autofollow_author=self.thread_autofollow_author,
            client_creation_key=client_creation_key,
            creation_actor=creation_actor,
        )

    def message_log(
        self,
        body: str = "",
        *,
        subtype_key: str = "note",
        message_type: Message.MessageKind | None = None,
        attachments: tuple[models.Model, ...] = (),
        parent: models.Model | None = None,
        client_creation_key: str | None = None,
        creation_actor: SubjectRef | None = None,
    ) -> models.Model:
        """Log a structured system note on this row's chatter thread.

        Defaults to the :attr:`Message.MessageKind.NOTIFICATION` kind.
        ``creation_actor`` sets replay identity with the same precedence as
        :meth:`message_post`, including named non-user subjects for system jobs.
        """

        message_model = apps.get_model("messaging", "Message")
        return self._message_post(
            body,
            attachments=attachments,
            message_type=message_type or message_model.MessageKind.NOTIFICATION,
            subtype_key=subtype_key,
            parent=parent,
            recipient_user_ids=(),
            autofollow_recipients=False,
            autofollow_author=False,
            client_creation_key=client_creation_key,
            creation_actor=creation_actor,
        )

    def _require_thread_message(self, message: models.Model) -> models.Model:
        """Require that a message belongs to this record's chatter thread, outside the trash.

        A trashed message reads as absent here: only the moderation verbs
        (:meth:`message_removed`, :meth:`removed_message`) reach it.
        """

        attachment = self.message_thread_attachment(create=False)
        if (attachment is None or message.is_trashed
                or not attachment.thread.member_threads().filter(pk=message.thread_id).exists()):
            raise ValueError("Message does not belong to this record thread.")
        return attachment

    def _require_moderation(self, verb: str) -> None:
        """Require the record's moderation authority; authorship never suffices."""

        if not self.can_moderate():
            raise PermissionDenied(f"{verb} comments on {self._meta.label} requires moderator access to the record.")

    def message_update_content(self, message: models.Model, *, body: str) -> models.Model:
        """Update an authored comment or moderate one as a record writer."""

        self._require_thread_message(message)
        if error := message.content_edit_error():
            raise ValueError(error)
        return apps.get_model("messaging", "Message").objects.update_content(
            message, body=body, edited_by_id=_actor_user_id(self)
        )

    def message_unlink(self, message: models.Model) -> models.Model:
        """Delete an authored comment or moderate one as a record writer."""

        attachment = self._require_thread_message(message)
        if error := message.delete_error():
            raise ValueError(error)
        return apps.get_model("messaging", "Message").objects.unlink_from_thread(message, thread=attachment.thread)

    def message_trash(self, message: models.Model, *, reason: str = "") -> models.Model:
        """Move a comment to the trash as a record moderator, recording an optional reason.

        Moderation is the record's write authority (:meth:`can_moderate`); an
        author without it never trashes, even their own comment. The trashed
        comment leaves every chatter read except :meth:`message_removed`.
        """

        attachment = self._require_thread_message(message)
        self._require_moderation("Removing")
        return apps.get_model("messaging", "Message").objects.trash_in_thread(
            message, thread=attachment.thread, reason=reason,
        )

    def message_restore(self, message: models.Model) -> models.Model:
        """Take a trashed comment out of the trash as a record moderator."""

        self._require_moderation("Restoring")
        attachment = self.message_thread_attachment(create=False)
        if attachment is None or message.thread_id != attachment.thread_id:
            raise ValueError("Message does not belong to this record thread.")
        return apps.get_model("messaging", "Message").objects.restore_in_thread(message, thread=attachment.thread)

    def message_removed(self, *, role: str = "chatter", limit: int = 50) -> tuple[list[Any], int]:
        """Return the newest trashed chatter messages and their count, for moderators only."""

        self._require_moderation("Listing removed")
        return apps.get_model("messaging", "Message").objects.for_record(self, role=role, limit=limit, trashed=True)

    def record_message(self, message_id: str, *, role: str = "chatter", trashed: bool = False) -> models.Model:
        """Return one message of this record's thread; a trashed one only for moderators."""

        if trashed:
            self._require_moderation("Restoring")
        message = apps.get_model("messaging", "Message").objects.in_record(
            self, message_id, role=role, trashed=trashed,
        )
        if message is None:
            raise ValueError("Message does not belong to this record thread.")
        return message

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
        self._require_thread_message(message)
        message_model = apps.get_model("messaging", "Message")
        return message_model.objects.set_reaction(message, reaction=reaction, action=action, user=user)

    def message_starred(self, message: models.Model, *, user: Any) -> bool:
        """Return whether ``user`` has starred ``message`` in this row's chatter."""

        self._require_thread_message(message)
        star_model = apps.get_model("messaging", "MessageStar")
        return bool(star_model.objects.is_starred(message, user=user))

    def message_set_starred(self, message: models.Model, *, user: Any, starred: bool | None = None) -> bool:
        """Set or toggle ``user``'s star on a message in this row's chatter."""

        if not self._message_read_allowed():
            raise PermissionDenied(
                f"Starring messages on {self._meta.label} requires {self.thread_read_access!r} access."
            )
        self._require_thread_message(message)
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
        attachment = self._require_thread_message(message)
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
        recipient_user_ids: tuple[Any, ...],
        autofollow_recipients: bool,
        autofollow_author: bool,
        client_creation_key: str | None = None,
        creation_actor: SubjectRef | None = None,
    ) -> models.Model:
        """Post one user-authored chatter message after enforcing this row's post policy.

        User posts ride the actor's :attr:`thread_post_access`; the message itself is
        written by the shared system-write owner (:meth:`message_system_post`), so the
        post gate lives in exactly one place and the automatic-log path can reuse that
        write without it.
        """

        attachment = self.message_thread_attachment(create=False)
        target = attachment.target if attachment is not None else self
        if not target.can_post():
            raise PermissionDenied(f"Posting on {target._meta.label} requires {target.thread_post_access!r} access.")
        return self.message_system_post(
            body=body,
            attachments=attachments,
            message_type=message_type,
            subtype_key=subtype_key,
            parent=parent,
            recipient_user_ids=recipient_user_ids,
            autofollow_author=autofollow_author,
            autofollow_recipients=autofollow_recipients,
            client_creation_key=client_creation_key,
            creation_actor=creation_actor,
        )

    @transaction.atomic
    def message_system_post(
        self,
        *,
        body: str = "",
        attachments: tuple[models.Model, ...] = (),
        message_type: Message.MessageKind | None,
        subtype_key: str,
        parent: models.Model | None = None,
        recipient_user_ids: tuple[Any, ...] = (),
        autofollow_author: bool = False,
        autofollow_recipients: bool = False,
        client_creation_key: str | None = None,
        creation_actor: SubjectRef | None = None,
    ) -> models.Model:
        """Write one automatic system message on this row's chatter thread.

        The single owner of the chatter *system* write — record-creation notes and
        retained system notes. These are written by the framework on the
        record's behalf, not authored by the acting user, so this path never consults
        :meth:`can_post` / :attr:`thread_post_access`: an actor authorized to change a
        tracked field but not to post comments must still get the change logged rather
        than have its save rolled back by a post-access denial. User-authored posts go
        through :meth:`_message_post`, which adds the post gate and autofollow policy.
        """

        scope_actor = creation_actor or self.actor() or current_actor()
        attachment = self.message_thread_attachment(create=True)
        if attachment is None:
            raise ValueError("Cannot post a message without a thread.")
        message_model = apps.get_model("messaging", "Message")
        # The framework writes on the record's behalf, so the whole pipeline
        # (message, parts, fanout, receipt advance) runs under
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
                recipient_user_ids=recipient_user_ids,
                autofollow_author=autofollow_author,
                autofollow_recipients=autofollow_recipients,
                client_creation_key=client_creation_key,
                creation_actor=scope_actor,
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
        recipient_user_ids: tuple[Any, ...],
        autofollow_author: bool,
        autofollow_recipients: bool,
        client_creation_key: str | None,
        creation_actor: SubjectRef | None,
    ) -> models.Model:
        """Run the elevated system-post write; split out for readability only."""

        record = attachment.target
        return message_model.objects.post_to_thread(
            attachment.thread,
            body=body,
            created_by_id=_actor_user_id(self),
            attachment=attachment,
            record=record,
            attachments=attachments,
            message_type=message_type,
            subtype_key=subtype_key,
            subtype_model_label=record._meta.label,
            parent=parent,
            recipient_user_ids=recipient_user_ids,
            autofollow_author=autofollow_author,
            autofollow_recipients=autofollow_recipients,
            client_creation_key=client_creation_key,
            creation_actor=creation_actor,
        )

    def message_subscribe(
        self,
        *,
        party: models.Model | None = None,
        user: models.Model | None = None,
        notification_policy: str | None = None,
        subtype_keys: tuple[str, ...] | None = None,
    ) -> models.Model:
        """Follow this record as a party, or resolve the acting user's person.

        Repeated follows preserve preferences unless explicitly changed. The
        follower manager requires the person's live record read; a role owner
        grants access before adding a new follower.
        """

        return apps.get_model("messaging", "ThreadFollower").objects.subscribe(
            self,
            party=party,
            user=user,
            role=self.thread_attachment_role,
            notification_policy=notification_policy,
            subtype_keys=subtype_keys,
        )

    def message_unsubscribe(self, *, party: models.Model | None = None, user: models.Model | None = None) -> bool:
        """Remove a party's explicit follow without changing any shares."""

        return bool(
            apps.get_model("messaging", "ThreadFollower").objects.unsubscribe(
                self,
                party=party,
                user=user,
                role=self.thread_attachment_role,
            )
        )

    def message_is_follower(self, *, party: models.Model | None = None, user: models.Model | None = None) -> bool:
        """Return whether a party, or the acting user's person, follows this record."""

        return bool(
            apps.get_model("messaging", "ThreadFollower").objects.is_following(
                self,
                party=party,
                user=user,
                role=self.thread_attachment_role,
            )
        )

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
        omitted so the composer suggests only additional recipients. Candidates
        are chosen on stored facts; each suggested account is then read as
        ``user`` reads it, so restricted account fields stay behind IAM's gates.
        """

        if not self._message_read_allowed():
            raise PermissionDenied(
                f"Reading message recipients on {self._meta.label} requires {self.thread_read_access!r} access."
            )
        attachment = self.message_thread_attachment(create=False)
        thread = attachment.thread if attachment is not None else None
        follower_ids = {
            str(user_id)
            for user_id in self.message_followers()
            .sudo(reason="messaging suggested recipients follower suppression")
            .values_list("party__person__user_id", flat=True)
        }
        current_user_id = getattr(user, "pk", None)
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(candidate: models.Model | None, *, reason: str, source: str) -> None:
            if candidate is None:
                return
            key = str(candidate.pk)
            if key in seen or key in follower_ids or key == str(current_user_id):
                return
            seen.add(key)
            suggestions.append({"user": candidate, "reason": reason, "source": source})

        for name in self.thread_suggested_recipient_fields:
            field = self._meta.get_field(name)
            add(
                getattr(self, field.name),
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
                add(
                    latest.created_by,
                    reason="Recent message author",
                    source="recent_message_author",
                )
                for notification in (
                    notification_model._base_manager.filter(message=latest).select_related("user").order_by("pk")
                ):
                    add(notification.user, reason="Recent message recipient", source="recent_message_recipient")

        user_model = get_user_model()
        active = user_model._base_manager.filter(pk__in=[item["user"].pk for item in suggestions], is_active=True)
        viewer = user if user is not None else current_actor()
        readable = read_scoped_queryset(user_model, viewer).in_bulk(list(active.values_list("pk", flat=True)))
        return tuple(
            {**item, "user": readable[item["user"].pk]} for item in suggestions if item["user"].pk in readable
        )

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

        created_by_id = _actor_user_id(self)
        follower_model = apps.get_model("messaging", "ThreadFollower")
        if self.thread_create_autofollow_author and created_by_id is not None:
            # System bookkeeping on an already-authorized create; see the
            # autofollow elevation note in MessageManager.post_to_thread.
            with system_context(reason="messaging.autofollow"):
                account = get_user_model()._base_manager.get(pk=created_by_id)
                if account.kind != "person" or self.thread_reader_allowed(account):
                    follower_model.objects.subscribe(
                        self,
                        user=account,
                        role=self.thread_attachment_role,
                    )
        message_model = apps.get_model("messaging", "Message")
        if self.thread_create_log:
            self.message_system_post(
                body=self.message_creation_message(),
                message_type=message_model.MessageKind.NOTIFICATION,
                subtype_key=self.thread_creation_subtype_key,
            )

    def can_post(self, user: Any = None) -> bool:
        """Return whether the actor may post or react through the declared permission."""

        if user is not None and getattr(user, "is_authenticated", True) is False:
            return False
        return self._message_post_allowed()

    def can_moderate(self, user: Any = None) -> bool:
        """Return whether the actor may moderate comments as a record writer."""

        if user is not None and getattr(user, "is_authenticated", True) is False:
            return False
        has_access = getattr(self, "has_access", None)
        return bool(has_access("write")) if callable(has_access) else True

    def _message_post_allowed(self) -> bool:
        """Return whether the ambient actor can post to this row."""

        has_access = getattr(self, "has_access", None)
        if not callable(has_access):
            return True
        return self._message_read_allowed() and bool(has_access(self.thread_post_access))

    def _message_read_allowed(self) -> bool:
        """Return whether the ambient actor can read personal chatter state."""

        has_access = getattr(self, "has_access", None)
        if not callable(has_access):
            return True
        return bool(has_access(self.thread_read_access))

    def thread_reader_allowed(self, user: Any) -> bool:
        """Check one recipient through the same explicit-account audience gate."""

        return actor_user_id(to_subject_ref(user)) in self.thread_reader_ids((user,))

    def thread_reader_ids(self, accounts: Iterable[models.Model | SubjectRef]) -> set[Any]:
        """Return the accounts that hold this record's read permission.

        The permission backend decides the whole audience in one bulk check:
        each item pins its account even under system-context fan-out, and no
        relationship or visibility rule is reconstructed here.
        """

        subjects = (to_subject_ref(account) for account in accounts)
        account_subjects = {actor_user_id(subject): subject for subject in subjects}
        account_subjects.pop(None, None)
        if not account_subjects:
            return set()
        if not callable(getattr(self, "has_access", None)) or not model_resource_type(self):
            return set(account_subjects)
        resource = to_object_ref(self)
        results = backend().check_bulk_permissions(
            CheckItem(subject, self.thread_read_access, resource) for subject in account_subjects.values()
        )
        return {
            account_id
            for account_id, result in zip(account_subjects, results, strict=True)
            if result.allowed
        }

    @classmethod
    def check(cls, **kwargs: Any) -> list[Any]:
        """Validate team and suggested-recipient declarations during Django system checks."""

        errors = super().check(**kwargs)
        user_model = get_user_model()
        for declaration, names, check_id in (
            (
                "thread_team_field",
                (cls.thread_team_field,) if cls.thread_team_field is not None else (),
                "messaging.E001",
            ),
            ("thread_suggested_recipient_fields", cls.thread_suggested_recipient_fields, "messaging.E002"),
        ):
            for name in names:
                try:
                    field = cls._meta.get_field(name)
                except FieldDoesNotExist:
                    errors.append(checks.Error(f"{declaration} names unknown field {name!r}.", obj=cls, id=check_id))
                    continue
                target = field.remote_field.model if isinstance(field, models.ForeignKey) else None
                if declaration == "thread_team_field":
                    valid = isinstance(target, type) and issubclass(target, ThreadAudienceMixin)
                    expected = "a ForeignKey to a ThreadAudienceMixin model"
                else:
                    valid = target is user_model
                    expected = "a ForeignKey to the user model"
                if not valid:
                    errors.append(checks.Error(f"{declaration}.{name} must be {expected}.", obj=cls, id=check_id))
        return errors

    def thread_team(self) -> ThreadAudienceMixin | None:
        """Return this record's declared team, if any; system checks validate the declaration."""

        return getattr(self, self.thread_team_field) if self.thread_team_field is not None else None

    def thread_audience_members(self) -> Iterable[AudienceMember]:
        """Return people named by this record's own columns, beside its team."""

        return ()



class Channel(Bridge):
    """A connected message transport for inbound and outbound email or social data.

    An ``integrate.Integration`` child (credential / owner / status from the
    connection substrate) and a ``Bridge`` (the scheduler + ``syncIntegration`` drive
    it through ``run_sync``). ``backend_class`` selects the protocol, contributed by
    the ``messaging_integrate_*`` addons (``imap``, the chat bridges), and ``config``
    carries source settings. ``sync()`` fetches + parses, then maps each message onto
    the messaging managers; outbound tasks resolve the same backend and call its
    ``deliver`` hook. Content sources may extend Channel while retaining their
    own backend and overlay.
    """

    runtime = True
    rebac_grantable = {"reader": "write", "replier": "write"}
    extends = "integrate.Integration"
    integration_create_mode = IntegrationCreateMode.CONNECT
    live_impl_field = "backend_class"

    backend_class = ImplClassField(ChannelBackend,
        default="manual",
        create_only=True,
    )
    """Registry key for the channel backend bound to this channel."""

    objects = ChannelManager()

    class Meta:
        """Django model options for the channel child model."""

        abstract = True
        rebac_resource_type = "messaging/channel"

    @property
    def backend(self) -> ChannelBackend:
        """Return this channel's selected backend, bound to this row."""

        backend_class = cast("type[ChannelBackend]", self.resolve_impl("backend_class"))
        return backend_class(self)

    @property
    def source_kind(self) -> SourceKind | None:
        """The selected protocol declares whether this channel asserts statements, and as what."""

        return cast("type[ChannelBackend]", self.resolve_impl("backend_class")).source_kind

    def can_reconcile_delivery(self) -> bool:
        """Read the concrete capability's backend declaration without opening it."""

        capability = self.concrete_capability()
        if capability._meta.concrete_model is not self._meta.concrete_model:
            return bool(capability.can_reconcile_delivery())
        return cast("type[ChannelBackend]", self.resolve_impl("backend_class")).reconciles_delivery()

    def delivery_visibility_delay(self) -> timedelta:
        """Read the selected transport's propagation margin without opening it."""

        capability = self.concrete_capability()
        if capability._meta.concrete_model is not self._meta.concrete_model:
            return cast(timedelta, capability.delivery_visibility_delay())
        return cast("type[ChannelBackend]", self.resolve_impl("backend_class")).delivery_visibility_delay

    def validate_webform_answers(self, answers: dict[str, Any]) -> None:
        """Validate domain meaning after the public form's scalar validation.

        Same-row Channel donors may override this hook and call ``super()`` to
        cooperate with other contributors. Raise ``ValidationError`` with answer
        field names as keys so the public form can return structured errors.
        """

        del answers

    def test_connection(self) -> str:
        """Exercise the selected backend's connection (the Integration test contract)."""

        return self.backend.test_connection()

    def purge_blockers(self) -> list[models.Model]:
        """Let model extensions contribute rows protecting this channel from purge.

        The purge owner counts its large ingested subtree separately; extensions
        return only retaining rows, whose names the preview scopes to its viewer.
        Native FK protection remains the authoritative delete check.
        """
        blockers = []
        for label in ("Thread", "Message"):
            pairs = apps.get_model("messaging", label).same_as.model
            blockers.extend(pairs._base_manager.filter(
                models.Q(left__channel_id=self.pk) | models.Q(right__channel_id=self.pk),
            ).order_by("pk"))
        return blockers

    def start_live(self) -> None:
        """Mark this channel live-desired, then dispatch the backend's live ingest.

        The Bridge live contract for channels: the base persists the desired
        state (so a reconciler can restart a dropped session), the selected
        backend owns the vendor dispatch. A poll-only backend's no-op hook makes
        this safely idempotent on any channel. The desire is merged under a row
        lock so a concurrent live session writing its own pairing keys cannot
        clobber it.
        """

        self.merge_subscription_state(desired=self.LiveState.LIVE)
        self.backend.start_live()

    def stop_live(self) -> None:
        """Mark this channel stop-desired, then dispatch the backend's live stop.

        A running live session notices the persisted desire on its next wake and
        exits cooperatively; the backend hook exists for vendors that also need
        an active teardown call. The desire is merged under a row lock so the
        running session cannot clobber it with a stale write.
        """

        self.merge_subscription_state(desired=self.LiveState.STOPPED)
        self.backend.stop_live()

    def _next_sync_at(self, *, now: Any) -> Any:
        """A live-desired channel stays out of the poll loop — push ingest owns it.

        ``record_sync``/``record_sync_error`` recompute the next poll through
        this hook, so a live channel's manual sync or live-session error never
        re-arms polling; a stopped or never-live channel keeps the interval.
        """

        if self.subscription_state.get("desired") == self.LiveState.LIVE:
            return None
        return super()._next_sync_at(now=now)


class ChannelWebform(models.Model):
    """Same-row public-form configuration and message mapping for ``messaging.Channel``."""

    extends = "messaging.Channel"

    hasura_readable_fields = (
        "slug",
        "is_published",
        "form_schema_version",
        "form_schema",
        "max_body_bytes",
        "max_field_bytes",
    )
    hasura_filterable_fields = ("slug", "is_published", "form_schema_version")
    hasura_sortable_fields = hasura_filterable_fields
    hasura_aggregatable_fields: tuple[str, ...] = ()
    hasura_groupable_fields = ("is_published", "form_schema_version")
    hasura_updatable_fields = hasura_readable_fields

    slug = models.SlugField(max_length=80, blank=True, default="")
    is_published = models.BooleanField(default=False)
    form_schema_version = models.PositiveSmallIntegerField(
        default=1,
        validators=(MinValueValidator(1),),
    )
    form_schema = models.JSONField(default=default_webform_schema, blank=True)
    max_body_bytes = models.PositiveIntegerField(
        default=65_536,
        validators=(MinValueValidator(1),),
    )
    max_field_bytes = models.PositiveIntegerField(
        default=4_096,
        validators=(MinValueValidator(1),),
    )

    class Meta:
        """Abstract webform columns and the public-slug identity constraint."""

        abstract = True
        constraints = (
            models.UniqueConstraint(
                fields=("slug",),
                condition=~models.Q(slug=""),
                name="uq_messaging_channel_webform_slug",
            ),
        )

    def clean(self) -> None:
        """Keep published/form-specific facts attached only to webform channels."""

        super().clean()
        is_webform = str(self.backend_class) == "webform"
        errors: dict[str, str] = {}
        if (self.slug or self.is_published) and not is_webform:
            errors["backend_class"] = "A public form requires the webform channel backend."
        if self.is_published and not self.slug:
            errors["slug"] = "A published public form requires a slug."
        if errors:
            raise ValidationError(errors)
        if is_webform:
            self.webform_spec()

    def webform_spec(self) -> WebformSpec:
        """Return this row's server-validated versioned form spec."""

        return WebformSpec.deserialize(
            self.form_schema,
            version=int(self.form_schema_version),
        )

    def webform_message(
        self,
        *,
        submission_id: str,
        answers: dict[str, Any],
    ) -> Any:
        """Map one validated answer envelope to messaging's neutral ingest DTO."""

        from angee.messaging.backends import (
            ParsedHandle,
            ParsedMessage,
            ParsedPart,
            ParsedThread,
        )

        spec = self.webform_spec()
        email_field = spec.email_field
        email = str(answers.get(email_field.name) or "").strip() if email_field else ""
        content_answers = (
            {name: value for name, value in answers.items() if name != email_field.name} if email_field else answers
        )
        stable_id = f"webform:{self.slug}:{submission_id}"
        sender = ParsedHandle(
            platform="other",
            value=stable_id,
            external_id=stable_id,
            display_name="Anonymous",
        )
        title = str(self.display_name or f"Public form {self.slug}")
        now = timezone.now()
        return ParsedMessage(
            external_id=stable_id,
            platform="other",
            sender=sender,
            sent_at=now,
            received_at=now,
            thread=ParsedThread(
                external_id=f"webform:{self.slug}",
                title=title,
                metadata={"webform_slug": self.slug},
            ),
            body=ParsedPart(
                type="text/plain",
                role="body",
                text=spec.render_markdown(content_answers),
            ),
            metadata={
                "webform": {
                    "slug": self.slug,
                    "schema_version": int(self.form_schema_version),
                    "submission_id": submission_id,
                    "answers": content_answers,
                    "unverified_submitter_email": email or None,
                }
            },
        )


class Thread(MergeableMixin, SourceCopyMixin, HistoryMixin, TaggedModel, OwnerMixin, AngeeDataModel):
    """An aggregation of related messages — an email conversation or a social post.

    Two orthogonal axes, both base-owned: ``modality`` (the *shape* — email thread /
    direct / group / public post) and ``visibility`` (*who can see it*). A public
    thread's post link (``subject_url``) has no producer in this base slice, so the
    ``posts`` addon owns that column and folds it onto this same row through the
    same-row ``extends`` seam. ``message_count``/``last_message_at`` are
    denormalised: inserts use ``F()`` deltas; loss, restoration and delivery
    settlement recount surviving rows under the thread lock.

    ``title`` is a pointer at the content-addressed :class:`Fragment` holding the
    thread's normalised subject — a denormalisation that duplicates nothing (the row
    is shared), replaces the old ``subject``/``subject_normalized`` columns, and makes
    subject-based thread grouping an indexed FK lookup by fragment hash. ``NULL``
    means untitled (a DM); untitled threads never share a hot empty-string fragment,
    which would skew the planner's common-value statistics (Zulip works around the
    same skew with an unprintable DM topic sentinel).

    Identity is channel-scoped ``MD5(external_id)``: the
    synthetic keys (``subj:<normalized>``, ``msg:<id>``, ``record:<label>:<pk>:<role>``)
    may exceed btree's entry limit (a 7,970-char Apple Mail subject is real), so the
    index carries a fixed digest while the exact value stays in the unbounded column.
    Every source account retains its own thread. Reversible same_as evidence
    groups readable copies for display without moving messages or read cursors.
    """

    retains_merged = True
    merge_moves_references = False
    history = ModelHistory(inherit=True, excluded_fields=("message_count", "last_message_at"))

    def validate_merge(self, merged: Any) -> None:
        """Operational redirects join record chatter; source copies use same_as."""

        super().validate_merge(merged)
        if not self.is_record_attached() or any(not row.is_record_attached() for row in merged):
            raise ValidationError("Source conversations retain their identity; assert same_as instead.")

    def member_threads(self) -> models.QuerySet:
        """Physical chatter containers under the current, flattened survivor."""

        survivor = self.canonical()
        return type(self)._base_manager.using(survivor._state.db).filter(
            models.Q(pk=survivor.pk) | models.Q(merged_into=survivor),
        )

    def chatter_totals(self) -> dict[str, Any]:
        """Current record summary over retained physical message containers."""

        return self.member_threads().aggregate(count=models.Sum("message_count"), latest=models.Max("last_message_at"))

    runtime = True
    rebac_grantable = {"reader": "share"}

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

    sqid_prefix = "thr_"
    channel = models.ForeignKey(
        "messaging.Channel",
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
        constraints = (
            # The digest, not the unbounded value, is what the btree carries; the
            # planner proves `external_id = '<value>'` implies the partial predicate.
            models.UniqueConstraint(
                models.F("channel"), models.F("platform"), MD5("external_id"),
                condition=models.Q(channel__isnull=False) & ~models.Q(external_id=""),
                name="uq_thread_channel_external_id",
            ),
            models.UniqueConstraint(
                models.F("platform"), MD5("external_id"),
                condition=models.Q(channel__isnull=True) & ~models.Q(external_id=""),
                name="uq_thread_local_external_id",
            ),
            models.CheckConstraint(
                condition=models.Q(channel__isnull=True) | models.Q(owner__isnull=True),
                name="ck_thread_channel_ownerless",
            ),
        )

    def __str__(self) -> str:
        """Return the thread title for Django displays."""

        title = self.title.text if self.title_id else ""
        return title or f"thread:{self.public_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Channel threads inherit access and cannot carry personal ownership."""

        if self.channel_id is not None:
            if self.owner_id is not None:
                raise ValidationError({"owner": "A channel-bound thread cannot have an owner."})
            if self._state.adding:
                kwargs["ownerless"] = True
        super().save(*args, **kwargs)

    def is_record_attached(self) -> bool:
        """Whether this thread is bound to a model row through a ``ThreadAttachment``.

        The one owner of the chatter-attachment fact, used by both the thread's and the
        message's ``broadcasts_changes`` gates: a chatter-attached thread is private,
        reachable only through the record-scoped ``record_thread`` payload (gated on the
        parent record's read) — the emission mirror of ``ThreadQuerySet.inbox()``.
        Source evidence edges deliberately do not change conversation broadcasting.
        """

        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        return attachment_model._base_manager.filter(
            thread_id__in=(self.pk, self.merged_into_id),
            role=attachment_model.AttachmentRole.CHATTER,
        ).exists()

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
        """Grant a user direct ``reader`` access through the declared share surface."""

        self._grant_declared_record_access(
            Thread,
            "reader",
            _user_subject_ref(user=user, user_id=user_id),
        )

    def revoke_reader(self, *, user: models.Model | None = None, user_id: Any = None) -> None:
        """Revoke a user's direct ``reader`` access to this thread (mirror of :meth:`grant_reader`)."""

        self._revoke_declared_record_access(
            Thread,
            "reader",
            _user_subject_ref(user=user, user_id=user_id),
        )


class ThreadAttachment(HistoryMixin, AuditMixin, RecordRefMixin, AngeeDataModel):
    """Polymorphic edge attaching one chatter thread to one model row."""

    merge_policy = MergePolicy.MOVE
    merge_identity = (("thread", "content_type", "object_id", "role"),)
    runtime = True

    class AttachmentRole(models.TextChoices):
        """Why the thread is attached to the target record."""

        CHATTER = "chatter", "Chatter"
        SOURCE = "source", "Source"

    @classmethod
    def record_edge_roles(cls, target: type[models.Model]) -> tuple[str, ...]:
        """Only a threaded model keeps chatter; any declared target retains source conversations."""

        return (cls.AttachmentRole.CHATTER.value,) if issubclass(target, ThreadedModelMixin) else ()

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
        constraints = (
            models.UniqueConstraint(
                fields=("content_type", "object_id"),
                condition=models.Q(role="chatter"),
                name="uq_thread_attachment_target_chatter",
            ),
            models.UniqueConstraint(
                fields=("thread", "content_type", "object_id", "role"),
                condition=models.Q(role="source"),
                name="uq_thread_attachment_source_edge",
            ),
        )
        indexes = (models.Index(fields=("content_type", "object_id", "role")),)

    def __str__(self) -> str:
        """Return a readable attachment label."""

        return self.label or f"{self.content_type}:{self.object_id}"

    @classmethod
    def merge_contract(cls, reference: Any, rows: Any, *, survivor: Any) -> Any:
        """Redirect chatter containers; source edges use the native reference mover."""

        chatter = rows.filter(role=cls.AttachmentRole.CHATTER)
        if not chatter.exists():
            return NotImplemented
        target = cls.objects.db_manager(rows.db).ensure_for_record(survivor, title=str(survivor))
        for edge in chatter.select_related("thread").order_by("pk"):
            thread = edge.thread.canonical()
            if thread.pk != target.thread_id:
                target.thread.fold([thread])
        related = rows.exclude(role=cls.AttachmentRole.CHATTER)
        if related.exists():
            reference.move(related, survivor=survivor)
        return None

    POINTER_COLUMNS: ClassVar[tuple[str, ...]] = ("label", "content_type_id", "object_id")
    """The columns a record pointer reads from an edge: its label and its record's identity."""

    @classmethod
    def pointer_annotations(cls, path: str) -> dict[str, models.Expression]:
        """Annotate the record pointer of the edge a row reaches at ``path``, through the join.

        The edge row itself is never loaded; :meth:`pointer_from_row` rebuilds the
        pointer from these columns. The caller owns whether the row's readers may
        see the edge's record.
        """

        return {f"_{path}_pointer_{column}": models.F(f"{path}__{column}") for column in cls.POINTER_COLUMNS}

    @classmethod
    def pointer_from_row(cls, row: models.Model, path: str) -> ThreadAttachment | None:
        """Return the unsaved edge ``row`` reaches at ``path``, carrying only its record pointer.

        It reads :meth:`pointer_annotations` when the row carries them, else the
        edge by its id under system context (a structural read of the same columns).
        """

        edge_id = getattr(row, f"{path}_id")
        if edge_id is None:
            return None
        prefix = f"_{path}_pointer_"
        if all(hasattr(row, f"{prefix}{column}") for column in cls.POINTER_COLUMNS):
            return cls(**{column: getattr(row, f"{prefix}{column}") for column in cls.POINTER_COLUMNS})
        with system_context(reason="messaging.thread_attachment.pointer"):
            return cls._base_manager.only(*cls.POINTER_COLUMNS).get(pk=edge_id)


class FileSourceThreads(models.Model):
    """Messaging-owned reverse edges from a Storage File to source conversations."""

    extends = "storage.File"
    source_thread_attachments = GenericRelation(
        "messaging.ThreadAttachment",
        content_type_field="content_type",
        object_id_field="object_id",
    )

    class Meta:
        abstract = True


class ThreadFollower(AuditMixin, AngeeDataModel):
    """A party's per-thread subscription row — subscription policy plus read receipt.

    The one row per ``(thread, party)``: it carries how the follower wants updates
    (``notification_policy``/``subtype_keys``) and *where they have read to*
    (``last_read_message``) — the Synapse receipts pattern. Unread is a bounded
    keyset scan from the receipt anchor, never a per-message fan-out row, so read
    state costs O(members × threads) rows regardless of message volume.
    ``attachment`` is set for record-chatter follows and ``NULL`` for a bare
    thread follow (a room membership without a host record).
    This cursor records progress through the record's messages; it does not
    acknowledge individual inbox items, which own ``ThreadNotification.read_at``.
    """

    runtime = True

    NotificationPolicy = NotificationPolicy

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
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="+",
        related_query_name="thread_followers",
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
        ordering = ("party_id", "sqid")
        rebac_resource_type = "messaging/thread_follower"
        constraints = (
            models.UniqueConstraint(
                fields=("thread", "party"),
                name="uq_thread_follower_thread_party",
            ),
        )
        indexes = (
            # (thread, party) rides the unique constraint's btree; back the
            # "my followed threads" sweep from the party side.
            models.Index(fields=("party", "thread"), name="ix_follower_party_thread"),
        )

    def __str__(self) -> str:
        """Return a readable follower label."""

        return f"{self.party_id} follows {self.thread_id}"

    @property
    def notification_preference(self) -> NotificationPreference:
        """Return the shared notification policy for this follow."""

        return NotificationPreference(
            notification_policy=NotificationPolicy(self.notification_policy),
            subtype_keys=tuple(self.subtype_keys or ()),
        )


class MessageSubtype(AuditMixin, AngeeDataModel):
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
        # Named debt: the key names a done record task (projects' completion post).
        # Renaming it means migrating stored subtypes and follower choices.
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


@dataclass(frozen=True)
class WebformSubmission:
    """A public form's answers and claimed email; the email proves no identity."""

    answers: dict[str, Any]
    unverified_submitter_email: str | None


class Message(
    SourceCopyMixin, ResourceLoadMixin, TaggedModel, TrashMixin, CreationKeyMixin,
    OptimisticLockMixin, AuditMixin, AngeeDataModel,
):
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

    Moderation is trash (:class:`~angee.base.mixins.TrashMixin`): a trashed
    message is withheld from every reader but channel managers and admins, and
    record chatter leaves it out of every read but its moderators' removed list.
    """

    runtime = True
    resource_class = MessageResource
    rebac_grantable = {"reader": "write"}
    creation_key_scope = "creation_actor"
    creation_actor = models.CharField(max_length=512, null=True, blank=True, editable=False)
    """REBAC actor identity for keyed posts, independent of nullable user attribution."""

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

    hasura_filter_expressions = {
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
            CreationKeyMixin.creation_key_constraint(scope="creation_actor"),
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

    def trash(self, *, reason: str = "", using: str | None = None, recount: bool = True) -> None:
        """Trash and cancel unsent delivery; restoring never re-arms a send."""

        with transaction.atomic(using=using):
            type(self).objects.cancel_delivery(self, using=using)
            super().trash(reason=reason, using=using)
            if recount:
                self._recount_thread()

    def restore(self, *, using: str | None = None) -> None:
        """Restore this message and recount its thread with it."""

        with transaction.atomic(using=using):
            super().restore(using=using)
            self._recount_thread()

    def _recount_thread(self) -> None:
        """Keep the thread's counters to its untrashed messages; bookkeeping, not access."""

        if self.thread_id is None:
            return
        thread_model = self._meta.get_field("thread").related_model
        with system_context(reason="messaging.message.recount_thread"):
            thread = thread_model._base_manager.get(pk=self.thread_id)
            type(self).objects._recount_thread(thread)

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
            ._base_manager.filter(
                thread_id=self.thread.canonical().pk,
                role="chatter",
            )
            .first()
        )
        if attachment is None:
            return None
        target = attachment.target
        return target if isinstance(target, ThreadedModelMixin) else None

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
        return not thread.is_record_attached()


class ThreadNotification(AuditMixin, AngeeDataModel):
    """A recipient's delivery ledger and acknowledgement of one inbox item.

    ``read_at`` records acknowledgement of this notification independently of
    the delivery lifecycle and failure diagnostics. A derived-audience recipient
    need not follow the record. ``ThreadFollower.last_read_message`` instead
    records an explicit follower's position in the record's message history;
    acknowledging an inbox item neither creates a follow nor advances that cursor.
    Every account told under inbox or email policy gets a delivery row, including
    explicit followers; acknowledgement and the follower cursor remain independent.
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
    read_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(blank=True, default=dict)

    objects = ThreadNotificationManager()

    class Meta:
        """Django model options for thread notifications."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "messaging/thread_notification"
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


class Fragment(AuditMixin, AngeeDataModel):
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


class Part(AuditMixin, AngeeDataModel):
    """One recursive body node of a message (the MIME/JMAP part shape, one model).

    ``type``/``role`` is a genuine discriminator, not MTI: a ``multipart/*`` is a
    container; a text part references a :class:`Fragment`; a byte part references a
    ``storage.File``. Attachments are ``disposition=attachment`` + ``file``, or an
    inline document (``disposition=inline`` with a filename and a document type, as
    forwarded bills arrive); inline images are ``disposition=inline`` + ``cid``.
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


class MessageEdge(AuditMixin, AngeeDataModel):
    """One typed cross-message relation — the unified quote/reference graph.

    ``Message.parent`` stays the single-parent reply pointer and ``Thread`` is
    membership; this carries the M2M/derived relations. A derived *quote* edge sets
    ``fragment`` (the shared content-addressed text) and a ``confidence``; both
    direction indexes back the bulk BFS.
    """

    runtime = True

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


class Participant(AuditMixin, AngeeDataModel):
    """A Handle-keyed membership of a thread/message — the queryable recipient row.

    The raw to/cc/bcc stays in ``Message.metadata`` as the lossless source; this is
    its queryable projection, so the inbox can group/filter by participant.
    One row states one fact: a handle's role on a message, or, without a message,
    in a thread. A merge moving a handle's rows onto one already stating the same
    fact drops the moved copy (``merge_identity``).
    """

    runtime = True
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


class Reaction(AuditMixin, AngeeDataModel):
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

    runtime = True
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
        max_length = cls._meta.get_field("reaction").max_length
        if max_length is not None and len(cleaned) > max_length:
            raise ValueError("Reaction is too long.")
        return cleaned


class MessageStar(AuditMixin, AngeeDataModel):
    """A user's Odoo-style star/favorite marker on a message."""

    runtime = True

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
