"""Threaded-record and audience contracts, independent of messaging model and manager imports."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models.fields.json import DataContains
from django.db.models.functions import JSONArray
from django.db.models.sql.compiler import SQLCompiler
from django.utils.text import capfirst
from rebac import (
    CheckItem,
    PermissionDenied,
    RebacMixin,
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

from angee.base.exceptions import DomainError
from angee.base.fields.relations import RetainedGenericRelation
from angee.base.models.identity import instance_from_public_id
from angee.base.permissions import actor_user_id
from angee.base.refs import generic_pointer_model

if TYPE_CHECKING:
    from angee.messaging.managers import MessageQuerySet, ThreadFollowerQuerySet
    from angee.messaging.models import Message, Thread, ThreadAttachment, ThreadFollower


def _actor_user_id(instance: models.Model) -> Any | None:
    """Return attribution for the record-bound actor, falling back to ambient.

    Posting uses the same precedence for replay identity unless the caller supplies
    ``idempotency_actor`` explicitly. That override does not change attribution or access.
    """

    actor = instance.actor() if isinstance(instance, RebacMixin) else None
    return actor_user_id(actor if actor is not None else current_actor())


def user_subject_ref(*, user: Any = None, user_id: Any = None) -> SubjectRef:
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


class NotificationPolicy(models.TextChoices):
    """How a follower or derived audience member wants record updates."""

    INBOX = "inbox", "Inbox"
    EMAIL = "email", "Email"
    MUTED = "muted", "Muted"


class _SelectedSubtype(models.Lookup):
    """Membership in a follower's JSON list through native database JSON operators."""

    def as_sql(self, compiler: SQLCompiler, connection: BaseDatabaseWrapper) -> tuple[str, Sequence[Any]]:
        return compiler.compile(DataContains(self.lhs, JSONArray(self.rhs)))

    def as_sqlite(self, compiler: SQLCompiler, connection: BaseDatabaseWrapper) -> tuple[str, list[Any]]:
        selected, selected_params = compiler.compile(self.lhs)
        subtype, subtype_params = compiler.compile(self.rhs)
        return (
            f"EXISTS (SELECT 1 FROM json_each({selected}) AS selected_subtype "
            f"WHERE selected_subtype.type = 'text' AND selected_subtype.value = {subtype})",
            [*selected_params, *subtype_params],
        )


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

    @classmethod
    def subscribed_row_q(cls, *, notification_policy: str, subtype_keys: str) -> models.Q:
        """Project stored preferences onto a message query without loading each follow."""

        defaults = models.Q(**{subtype_keys: []}) & cls().subscribed_subtype_q()
        selected = models.Q(_SelectedSubtype(models.F(subtype_keys), models.F("subtype__key")))
        return ~models.Q(**{notification_policy: NotificationPolicy.MUTED}) & (defaults | selected)

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
            attachments__role=apps.get_model("messaging", "ThreadAttachment").AttachmentRole.CHATTER,
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
        """Carry this saved row's actor into its chatter bookkeeping."""

        scope: AbstractContextManager[None] = nullcontext()
        if isinstance(self, RebacMixin):
            actor, unscoped = self.effective_actor()
            if unscoped and not is_sudo():
                scope = system_context(reason="messaging.record_write")
            elif actor is not None and actor != current_actor():
                scope = actor_context(actor)
        with scope:
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
        with system_context(reason="messaging.threaded_record.delete"):
            return super().delete(using=using, keep_parents=keep_parents)

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
                role=apps.get_model("messaging", "ThreadAttachment").AttachmentRole.CHATTER,
                title=self.message_thread_title(),
            )
        return attachment_model.objects.for_record(self, role=attachment_model.AttachmentRole.CHATTER)

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

        with transaction.atomic():
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

        return apps.get_model("messaging", "ThreadFollower").objects.subscribe(
            self,
            party=party,
            user=user,
            role=apps.get_model("messaging", "ThreadAttachment").AttachmentRole.CHATTER,
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
                role=apps.get_model("messaging", "ThreadAttachment").AttachmentRole.CHATTER,
            )
        )

    def message_is_follower(self, *, party: models.Model | None = None, user: models.Model | None = None) -> bool:
        """Return whether a party, or the acting user's person, follows this record."""

        return bool(
            apps.get_model("messaging", "ThreadFollower").objects.is_following(
                self,
                party=party,
                user=user,
                role=apps.get_model("messaging", "ThreadAttachment").AttachmentRole.CHATTER,
            )
        )

    def message_followers(self) -> ThreadFollowerQuerySet[ThreadFollower]:
        """Return this row's chatter followers."""

        follower_model = apps.get_model("messaging", "ThreadFollower")
        return follower_model.objects.for_record(
            self, role=apps.get_model("messaging", "ThreadAttachment").AttachmentRole.CHATTER,
        )

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
                account = get_user_model()._base_manager.get(pk=created_by_id)
                if account.kind != "person" or self.thread_reader_allowed(account):
                    follower_model.objects.subscribe(
                        self,
                        user=account,
                        role=apps.get_model("messaging", "ThreadAttachment").AttachmentRole.CHATTER,
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
        return self.has_access("write") if isinstance(self, RebacMixin) else True

    def _message_post_allowed(self) -> bool:
        """Return whether the ambient actor can post to this row."""

        if not isinstance(self, RebacMixin):
            return True
        return self._message_read_allowed() and self.has_access(self.thread_post_access)

    def _message_read_allowed(self) -> bool:
        """Return whether the ambient actor can read personal chatter state."""

        return self.has_access(self.thread_read_access) if isinstance(self, RebacMixin) else True

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
        if not isinstance(self, RebacMixin) or not model_resource_type(self):
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
