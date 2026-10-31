"""Threads models for Messaging; public imports remain on the package."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any, ClassVar

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.db.models.functions import MD5
from django.utils.text import capfirst
from django_compose.models import Composable
from rebac import (
    CheckItem,
    PermissionDenied,
    RebacMixin,
    SubjectRef,
    actor_context,
    current_actor,
    database_context,
    system_context,
    to_object_ref,
    to_subject_ref,
)
from rebac.actors import is_sudo
from rebac.backends import backend
from rebac.resources import model_resource_type

from angee.base.exceptions import DomainError
from angee.base.fields.relations import RetainedGenericRelation
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel, AngeeModel
from angee.base.models.identity import instance_from_public_id
from angee.base.models.merge_references import MergePolicy
from angee.base.models.ownership import OwnerMixin
from angee.base.models.refs import RecordRefMixin
from angee.base.permissions import actor_user_id
from angee.base.refs import RecordRef, generic_pointer_model
from angee.graphql.mixins import ChangeBroadcastMixin
from angee.messaging.managers import (
    ThreadAttachmentManager,
    ThreadManager,
)
from angee.messaging.models.messages import Message
from angee.messaging.models.notifications import AudienceMember, ThreadAudienceMixin, ThreadFollower
from angee.messaging.querysets import MessageQuerySet, ThreadFollowerQuerySet
from angee.messaging.source_copies import SourceCopyMixin
from angee.parties.models import Handle
from angee.provenance.contrib.angee.history import HistoryMixin, ModelHistory
from angee.provenance.contrib.angee.managers import AngeeHistoryMergeableUnscopedManager
from angee.tags.models import TaggedModel


def _actor_user_id(instance: models.Model) -> Any | None:
    """Return attribution for the record-bound actor, falling back to ambient.

    Posting uses the same precedence for replay identity unless the caller supplies
    ``idempotency_actor`` explicitly. That override does not change attribution or access.
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


class ChatterRecordRefused(DomainError, ValueError):
    """A record label that names no model, or a model that keeps no chatter.

    Also a ``ValueError``, so the chatter commands' in-band envelopes project it
    beside their other input refusals; any other caller's schema projects its code.
    """

    code = "BAD_RECORD"


def chatter_record(model_label: str, public_id: str) -> models.Model | None:
    """Return the record ``public_id`` names, read under the actor, when ``model_label`` keeps chatter.

    An unknown label or a model without chatter raises :class:`ChatterRecordRefused`;
    a missing or unreadable record is ``None``.
    """

    try:
        model = apps.get_model(model_label)
    except (LookupError, ValueError) as error:
        raise ChatterRecordRefused() from error
    if not issubclass(model, ThreadedModelMixin):
        raise ChatterRecordRefused()
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
    — while target-delete policy discovers edges through that same type. Compose the mixin on that same
    topmost REBAC-typed ancestor (``parties.Party``, not ``parties.Person``), so the write
    content type and the teardown content type match; ``ensure_for_record`` fails fast on a
    child-composed / ancestor-uncomposed split (the placement invariant in
    :mod:`angee.base.refs`).
    """

    thread_attachment_role: ClassVar[str] = "chatter"
    """The attachment role used for the model's primary chatter thread."""

    @classmethod
    def thread_messages_expression(cls, record_id: Any) -> MessageQuerySet[Message]:
        """Return messages of the record's primary thread for a SQL subquery.

        This is a structural, unscoped expression. The calling projection owns
        authorization of the information derived from these message rows.
        """

        owner = generic_pointer_model(cls)
        content_type = ContentType.objects.filter(
            app_label=owner._meta.app_label,
            model=owner._meta.model_name,
        ).values("pk")[:1]
        message_model = apps.get_model("messaging", "Message")
        thread_model = apps.get_model("messaging", "Thread")
        record_ref = record_id if hasattr(record_id, "resolve_expression") else models.Value(record_id)
        rows = message_model.objects.system_context(reason="messaging.record.messages").alias(_record_id=record_ref)
        survivor = thread_model(pk=rows.survivor_id("thread__", ref=models.OuterRef))
        # The thread rows are structural plumbing for an already-authorized
        # record projection.  The message queryset below is the content-bearing
        # elevated read; auditing this attachment subquery as a second content
        # read adds a duplicate bypass event every time the expression runs.
        attached = thread_model._base_manager.redirect_group(survivor).filter(
            attachments__content_type_id=models.Subquery(content_type),
            attachments__object_id=models.OuterRef("_record_id"),
            attachments__role=cls.thread_attachment_role,
        )
        return rows.alias(_record_attached=models.Exists(attached)).filter(
            thread_id__isnull=False,
            _record_attached=True,
        )

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

    This retained reverse relation supplies queries without Django collecting the
    edge itself. ``RecordRefMixin.on_target_delete`` deletes the edge for instance
    and queryset deletion alike.

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

    @contextmanager
    def _own_write_scope(self) -> Iterator[None]:
        """Carry this saved row's database and actor into its chatter bookkeeping."""

        scope: AbstractContextManager[None] = nullcontext()
        if isinstance(self, RebacMixin):
            actor, unscoped = self.effective_actor()
            if unscoped and not is_sudo():
                scope = system_context(reason="messaging.record_write")
            elif actor is not None and actor != current_actor():
                scope = actor_context(actor)
        using = type(self)._base_manager.db_manager(self._state.db, hints={"instance": self}).db
        with database_context(using), scope:
            yield

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

        if isinstance(self, RebacMixin) and not self.has_access("delete"):
            raise PermissionDenied(f"Denied: cannot delete {self._meta.label}")
        alias = type(self)._base_manager.db_manager(hints={"instance": self}).db
        with system_context(reason="messaging.threaded_record.delete"):
            return super().delete(using=alias, keep_parents=keep_parents)

    def message_thread(self, *, create: bool = True) -> Thread | None:
        """Return this row's chatter thread, optionally creating it."""

        attachment = self.message_thread_attachment(create=create)
        return attachment.thread if attachment is not None else None

    def message_thread_attachment(self, *, create: bool = True) -> ThreadAttachment | None:
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
        parent: Message | None = None,
        idempotency_key: str | None = None,
        idempotency_actor: SubjectRef | None = None,
    ) -> Message:
        """Post an internal comment on this row's chatter thread.

        ``message_type`` defaults to :attr:`Message.MessageKind.COMMENT` (resolved by
        the message write path), keeping the enum the single source of truth. A chatter
        comment carries no title of its own — the thread's title fragment is the label.
        ``idempotency_actor`` supplies replay identity for a keyed system job; otherwise
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
            idempotency_key=idempotency_key,
            idempotency_actor=idempotency_actor,
        )

    def message_log(
        self,
        body: str = "",
        *,
        subtype_key: str = "note",
        message_type: Message.MessageKind | None = None,
        attachments: tuple[models.Model, ...] = (),
        parent: Message | None = None,
        idempotency_key: str | None = None,
        idempotency_actor: SubjectRef | None = None,
    ) -> Message:
        """Log a structured system note on this row's chatter thread.

        Defaults to the :attr:`Message.MessageKind.NOTIFICATION` kind.
        ``idempotency_actor`` sets replay identity with the same precedence as
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
            idempotency_key=idempotency_key,
            idempotency_actor=idempotency_actor,
        )

    def _require_thread_message(self, message: Message) -> ThreadAttachment:
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

    def message_update_content(self, message: Message, *, body: str) -> Message:
        """Update an authored comment or moderate one as a record writer."""

        self._require_thread_message(message)
        if error := message.content_edit_error():
            raise ValueError(error)
        return apps.get_model("messaging", "Message").objects.update_content(
            message, body=body, edited_by_id=_actor_user_id(self)
        )

    def message_unlink(self, message: Message) -> Message:
        """Delete an authored comment or moderate one as a record writer."""

        attachment = self._require_thread_message(message)
        if error := message.delete_error():
            raise ValueError(error)
        return apps.get_model("messaging", "Message").objects.unlink_from_thread(message, thread=attachment.thread)

    def message_trash(self, message: Message, *, reason: str = "") -> Message:
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

    def message_restore(self, message: Message) -> Message:
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

    def record_message(self, message_id: str, *, role: str = "chatter", trashed: bool = False) -> Message:
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
        message: Message,
        *,
        reaction: str,
        action: str = "toggle",
        user: Any,
    ) -> Message:
        """Add, remove, or toggle ``user``'s reaction on a chatter message."""

        if not self.can_post():
            raise PermissionDenied(
                f"Reacting to messages on {self._meta.label} requires {self.thread_post_access!r} access."
            )
        self._require_thread_message(message)
        message_model = apps.get_model("messaging", "Message")
        return message_model.objects.set_reaction(message, reaction=reaction, action=action, user=user)

    def message_starred(self, message: Message, *, user: Any) -> bool:
        """Return whether ``user`` has starred ``message`` in this row's chatter."""

        self._require_thread_message(message)
        star_model = apps.get_model("messaging", "MessageStar")
        return bool(star_model.objects.is_starred(message, user=user))

    def message_set_starred(self, message: Message, *, user: Any, starred: bool | None = None) -> bool:
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

    def message_set_done(self, message: Message, *, user: Any) -> int:
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
        parent: Message | None,
        recipient_user_ids: tuple[Any, ...],
        autofollow_recipients: bool,
        autofollow_author: bool,
        idempotency_key: str | None = None,
        idempotency_actor: SubjectRef | None = None,
    ) -> Message:
        """Post one user-authored chatter message after enforcing this row's post policy.

        User posts ride the actor's :attr:`thread_post_access`; the message itself is
        written by the shared system-write owner (:meth:`message_system_post`), so the
        post gate lives in exactly one place and the automatic-log path can reuse that
        write without it.
        """

        attachment = self.message_thread_attachment(create=False)
        target = attachment.require_threaded_target() if attachment is not None else self
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
            idempotency_key=idempotency_key,
            idempotency_actor=idempotency_actor,
        )

    def message_system_post(
        self,
        *,
        body: str = "",
        attachments: tuple[models.Model, ...] = (),
        message_type: Message.MessageKind | None,
        subtype_key: str,
        parent: Message | None = None,
        recipient_user_ids: tuple[Any, ...] = (),
        autofollow_author: bool = False,
        autofollow_recipients: bool = False,
        idempotency_key: str | None = None,
        idempotency_actor: SubjectRef | None = None,
    ) -> Message:
        """Write one automatic system message on this row's chatter thread.

        The single owner of the chatter *system* write — record-creation notes and
        retained system notes. These are written by the framework on the
        record's behalf, not authored by the acting user, so this path never consults
        :meth:`can_post` / :attr:`thread_post_access`: an actor authorized to change a
        tracked field but not to post comments must still get the change logged rather
        than have its save rolled back by a post-access denial. User-authored posts go
        through :meth:`_message_post`, which adds the post gate and autofollow policy.
        """

        using = type(self)._base_manager.db_manager(self._state.db, hints={"instance": self}).db
        with database_context(using), transaction.atomic(using=using):
            bound_actor = self.actor() if isinstance(self, RebacMixin) else None
            scope_actor = idempotency_actor or bound_actor or current_actor()
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
                    idempotency_key=idempotency_key,
                    idempotency_actor=scope_actor,
                )

    def _system_post_pipeline(
        self,
        message_model: type[Message],
        attachment: ThreadAttachment,
        *,
        body: str,
        attachments: tuple[models.Model, ...],
        message_type: Message.MessageKind | None,
        subtype_key: str,
        parent: Message | None,
        recipient_user_ids: tuple[Any, ...],
        autofollow_author: bool,
        autofollow_recipients: bool,
        idempotency_key: str | None,
        idempotency_actor: SubjectRef | None,
    ) -> Message:
        """Run the elevated system-post write; split out for readability only."""

        record = attachment.require_threaded_target()
        return message_model.objects.db_manager(self._state.db).post_to_thread(
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
            idempotency_key=idempotency_key,
            idempotency_actor=idempotency_actor,
        )

    def message_subscribe(
        self,
        *,
        party: models.Model | None = None,
        user: models.Model | None = None,
        notification_policy: str | None = None,
        subtype_keys: tuple[str, ...] | None = None,
    ) -> ThreadFollower:
        """Follow this record as a party, or resolve the acting user's person.

        Repeated follows preserve preferences unless explicitly changed. The
        follower manager requires the person's live record read; a role owner
        grants access before adding a new follower.
        """

        return apps.get_model("messaging", "ThreadFollower").objects.db_manager(self._state.db).subscribe(
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
            apps.get_model("messaging", "ThreadFollower").objects.db_manager(self._state.db).unsubscribe(
                self,
                party=party,
                user=user,
                role=self.thread_attachment_role,
            )
        )

    def message_is_follower(self, *, party: models.Model | None = None, user: models.Model | None = None) -> bool:
        """Return whether a party, or the acting user's person, follows this record."""

        return bool(
            apps.get_model("messaging", "ThreadFollower").objects.db_manager(self._state.db).is_following(
                self,
                party=party,
                user=user,
                role=self.thread_attachment_role,
            )
        )

    def message_followers(self) -> ThreadFollowerQuerySet[ThreadFollower]:
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
            if not isinstance(field, models.Field):
                raise ImproperlyConfigured(f"{self._meta.label}.{name} must be a forward recipient field.")
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
        readable_rows = (
            user_model.objects.none()
            if viewer is None
            else user_model.objects.with_actor(viewer).with_action("read")
        )
        readable = readable_rows.in_bulk(list(active.values_list("pk", flat=True)))
        return tuple(
            {**item, "user": readable[item["user"].pk]} for item in suggestions if item["user"].pk in readable
        )

    def message_thread_title(self) -> str:
        """Return the default title text for this row's chatter thread.

        Interned as a content-addressed fragment and stamped onto the thread's
        ``title`` pointer at attachment; override to label the record's room.
        """

        return str(self)

    def _message_after_create(self) -> None:
        """Subscribe the creator; the creation itself is the record's first history line."""

        created_by_id = _actor_user_id(self)
        follower_model = apps.get_model("messaging", "ThreadFollower")
        if self.thread_create_autofollow_author and created_by_id is not None:
            # System bookkeeping on an already-authorized create; see the
            # autofollow elevation note in MessageManager.post_to_thread.
            with system_context(reason="messaging.autofollow"):
                account = get_user_model()._base_manager.using(self._state.db).get(pk=created_by_id)
                if account.kind != "person" or self.thread_reader_allowed(account):
                    follower_model.objects.db_manager(self._state.db).subscribe(
                        self,
                        user=account,
                        role=self.thread_attachment_role,
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


class ChatterHistory(ModelHistory):
    """History of a thread and its edge; a record's own chatter reverts under the record's write.

    Record chatter is bookkeeping the record's verbs write as system rows, so a
    revert of the record's changeset (its creation, a merge) is authorized by the
    record's writers. A source conversation keeps the thread's own rule.
    """

    def authorize_revert(self, first: Any, last: Any, previous: Any, actor: Any) -> None:
        record = self.chatter_record(previous or last)
        if record is None:
            super().authorize_revert(first, last, previous, actor)
            return
        if not isinstance(record, AngeeModel):
            raise ImproperlyConfigured("Reverting record chatter requires an Angee model host.")
        record.require_access("write", actor=actor)

    def revert_row(self, first: Any, last: Any, previous: Any) -> list[Any]:
        """Never remove a living record's chatter: the record's deletion tears it down."""

        removal = previous is None or previous.history_type == "-"
        if removal and self.chatter_record(previous or last) is not None:
            return []
        return super().revert_row(first, last, previous)

    @staticmethod
    def chatter_record(snapshot: Any) -> models.Model | None:
        """Return the stored record whose chatter this version belongs to, or ``None``."""

        using = snapshot._state.db
        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        chatter = attachment_model.AttachmentRole.CHATTER
        if isinstance(snapshot.instance, attachment_model):
            if snapshot.role != chatter:
                return None
            content_type_id, object_id = snapshot.content_type_id, snapshot.object_id
        else:
            edge = attachment_model._base_manager.using(using).filter(
                thread_id=snapshot.id, role=chatter,
            ).values_list("content_type_id", "object_id").first()
            if edge is None:
                return None
            content_type_id, object_id = edge
        model = ContentType.objects.db_manager(using).get_for_id(content_type_id).model_class()
        if model is None:
            return None
        return model._base_manager.using(using).filter(pk=object_id).first()


class Thread(
    ChangeBroadcastMixin, SourceCopyMixin, HistoryMixin, TaggedModel, OwnerMixin, AngeeDataModel, concrete=True
):
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
    Every source account retains its own thread. A thread folds as a retained
    redirect (``merged_into``) with its messages and read cursors in place: a
    record merge folds the retired record's chatter into the survivor's, and
    ``same_as`` evidence folds one channel's copy of a conversation into another's.
    """

    _angee_base = AngeeHistoryMergeableUnscopedManager()

    history = ChatterHistory(inherit=True, excluded_fields=("message_count", "last_message_at"))

    def member_threads(self) -> models.QuerySet:
        """Physical chatter containers under the current, flattened survivor."""

        survivor = self.canonical()
        return type(self)._base_manager.using(survivor._state.db).redirect_group(survivor)

    def chatter_record_ref(self) -> RecordRef | None:
        """The canonical chatter host, without loading messages or the document body."""
        survivor = self.canonical()
        attachment = (
            apps.get_model("messaging", "ThreadAttachment")
            ._base_manager.using(survivor._state.db).filter(
                thread_id=survivor.pk,
                role="chatter",
            )
            .first()
        )
        if attachment is None:
            return None
        model = attachment.content_type.model_class()
        return attachment.record_ref if model is not None and issubclass(model, ThreadedModelMixin) else None

    def chatter_totals(self) -> dict[str, Any]:
        """Current record summary over retained physical message containers."""

        return self.member_threads().aggregate(count=models.Sum("message_count"), latest=models.Max("last_message_at"))

    rebac_grantable = {"reader": "share"}

    class Modality(models.TextChoices):
        """The structural shape of a thread."""

        EMAIL_THREAD = "email_thread", "Email thread"
        DIRECT = "direct", "Direct"
        GROUP = "group", "Group"
        STATUS = "status", "Status updates"
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

        return self.display_title or f"thread:{self.public_id}"

    @property
    def display_title(self) -> str:
        """Name a status feed by its category, never by unrelated participants."""

        if self.modality == self.Modality.STATUS:
            return str(self.Modality.STATUS.label)
        fragment = self.title
        return fragment.text.strip() if fragment is not None else ""

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
        members = self.member_threads()
        return attachment_model._base_manager.using(members.db).filter(
            thread_id__in=members.values("pk"),
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
            Thread.record_access_permission,
            "reader",
            _user_subject_ref(user=user, user_id=user_id),
        )

    def revoke_reader(self, *, user: models.Model | None = None, user_id: Any = None) -> None:
        """Revoke a user's direct ``reader`` access to this thread (mirror of :meth:`grant_reader`)."""

        self._revoke_declared_record_access(
            Thread.record_access_permission,
            "reader",
            _user_subject_ref(user=user, user_id=user_id),
        )


class ThreadAttachment(HistoryMixin, AuditMixin, RecordRefMixin, AngeeDataModel, concrete=True):
    """Polymorphic edge attaching one chatter thread to one model row."""

    history = ChatterHistory(inherit=True)
    merge_policy = MergePolicy.MOVE
    on_target_delete = models.CASCADE
    merge_identity = (("thread", "content_type", "object_id", "role"),)

    class AttachmentRole(models.TextChoices):
        """Why the thread is attached to the target record."""

        CHATTER = "chatter", "Chatter"
        SOURCE = "source", "Source"

    @classmethod
    def record_edge_roles(cls, target: type[models.Model]) -> tuple[str, ...]:
        """Only a threaded model keeps chatter; any declared target retains source conversations."""

        return (str(cls.AttachmentRole.CHATTER),) if issubclass(target, ThreadedModelMixin) else ()

    @classmethod
    def teardown_for_target(cls, target: models.Model) -> None:
        """Run messaging's retained-state cleanup for a deleted attachment target."""

        cls.objects.db_manager(hints={"instance": target}).teardown_for_target(target)

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

    def require_threaded_target(self) -> ThreadedModelMixin:
        """Return the surviving chatter host required by posting and follow operations."""

        target = self.target
        if not isinstance(target, ThreadedModelMixin):
            raise ValueError("Chatter requires a surviving threaded record.")
        return target

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


class FileSourceThreads(Composable, extends="storage.File"):
    """Messaging-owned reverse edges from a Storage File to source conversations."""

    source_thread_attachments = GenericRelation(
        "messaging.ThreadAttachment",
        content_type_field="content_type",
        object_id_field="object_id",
    )
    """Reverse relation collecting the File's retained source-conversation edges."""

    class Meta:
        abstract = True
