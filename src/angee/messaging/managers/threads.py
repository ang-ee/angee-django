"""Threads managers for Messaging; public imports remain on the package."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, models, transaction
from rebac import (
    PermissionDenied,
    generic_target,
    system_context,
)

from angee.base.querysets import row_lock_paths
from angee.base.refs import generic_pointer_target
from angee.base.serialization import strip_null_bytes
from angee.base.signals import mute_changes
from angee.iam.models.base import ProtectedModel
from angee.messaging.querysets import (
    ChannelQuerySet as ChannelQuerySet,
)
from angee.messaging.querysets import (
    MessageQuerySet as MessageQuerySet,
)
from angee.messaging.querysets import (
    NotificationPointerIterable as NotificationPointerIterable,
)
from angee.messaging.querysets import (
    PartQuerySet as PartQuerySet,
)
from angee.messaging.querysets import (
    ThreadFollowerQuerySet as ThreadFollowerQuerySet,
)
from angee.messaging.querysets import (
    ThreadNotificationQuerySet as ThreadNotificationQuerySet,
)
from angee.messaging.querysets import (
    ThreadQuerySet as ThreadQuerySet,
)
from angee.parties.models import Handle
from angee.provenance.contrib.angee.managers import AngeeHistoryManager

if TYPE_CHECKING:

    from angee.messaging.backends import ParsedThread
    from angee.messaging.models import (
        Thread,
        ThreadAttachment,
    )

from angee.messaging.managers.messages import _external_id_q
from angee.messaging.querysets import _external_id_annotated

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fwd|fw|aw|sv|vs|ref|tr|rif)\s*(?:\[\d+\])?\s*:\s*", re.IGNORECASE)


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


_ThreadManagerBase = AngeeHistoryManager.from_queryset(ThreadQuerySet)


class ThreadManager[ThreadT: Thread](_ThreadManagerBase[ThreadT]):
    """Owns thread resolution — the 4-step RFC-5322 priority under a row lock."""

    @staticmethod
    def chat_key_prefix(channel: Any) -> str:
        """The external-id namespace of one channel's named chat threads."""

        return f"chat:{channel.pk if channel is not None else ''}:"


    def fill_chat_titles(self, channel: Any, titles: Mapping[str, str], *, created_by_id: Any = None) -> int:
        """Name a channel's untitled chat threads from source conversation names.

        ``titles`` maps a source conversation id (the ``ParsedThread.external_id``)
        to its name; see :meth:`name_untitled`.
        """

        prefix = self.chat_key_prefix(channel)
        wanted = {f"{prefix}{key}": name for key, name in titles.items()}
        untitled = (
            self.sudo(reason="messaging.thread.fill_chat_titles")
            .filter(channel_id=channel.pk, title__isnull=True, external_id__in=list(wanted))
            .values_list("pk", "external_id")
        )
        return self.name_untitled(
            {pk: wanted[external_id] for pk, external_id in untitled},
            created_by_id=created_by_id,
        )

    def referenced_thread(self, *, platform: str, channel: Any, in_reply_to: str, references: tuple[str, ...]) -> Any:
        """Read the channel-local reference winner without locking or creating a thread."""

        message_model = apps.get_model("messaging", "Message")
        if in_reply_to:
            parent = message_model.objects.with_external_ids([in_reply_to]).filter(
                platform=platform, channel=channel,
            ).select_related("thread").first()
            if parent is not None and parent.thread_id:
                return parent.thread
        if references:
            ref_map = {
                row.external_id: row
                for row in message_model.objects.with_external_ids(list(references)).filter(
                    platform=platform, channel=channel,
                ).select_related("thread")
            }
            for external_id in reversed(references):
                row = ref_map.get(external_id)
                if row is not None and row.thread_id:
                    return row.thread
        return None

    def resolve(
        self,
        *,
        platform: str,
        channel: Any,
        subject: str = "",
        in_reply_to: str = "",
        references: tuple[str, ...] = (),
        message_external_id: str = "",
        created_by_id: Any = None,
        modality: Any = None,
        visibility: Any = None,
        thread: ParsedThread | None = None,
    ) -> Any:
        """Resolve the thread a message belongs to, creating one if needed.

        A source that *names* its conversation (a chat adapter's
        :class:`~angee.messaging.backends.ParsedThread`) resolves first and
        directly: the thread keys on ``chat:<channel>:<external_id>`` — this
        manager owns the deterministic-key namespace (``chat:`` beside
        ``subj:``/``msg:``), so adapters pass raw conversation ids and never
        compose prefixes. Every source thread is channel-scoped: two accounts
        retain separate transport and membership occurrences, and ``same_as``
        evidence folds one into the other as a redirect. The hint's ``modality``/
        ``visibility`` land on a newly created thread only — a broadcast source
        names its feed public at the adapter that knows it, instead of every
        public source re-owning the visibility default. Its ``title`` also fills
        an established thread that has none, and never replaces an existing one.

        Otherwise the email priority applies: ``In-Reply-To`` → ``References``
        (newest-first, i.e. right-to-left, resolved in one batch query) →
        normalised subject → a new thread. The subject match and the create run
        under ``select_for_update`` on a deterministic external id
        (``subj:<normalized>`` or ``msg:<id>``) so two concurrent batches
        resolving the same subject collide on the unique constraint and converge
        to one thread instead of double-creating.

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

        fragment_model = apps.get_model("messaging", "Fragment")
        if thread is not None and thread.external_id:
            title = (
                fragment_model.objects.upsert(text=thread.title, created_by_id=created_by_id) if thread.title else None
            )
            named, _created = self.get_or_create_by_external_id(
                platform=platform,
                external_id=f"{self.chat_key_prefix(channel)}{thread.external_id}",
                defaults={
                    "channel_id": channel.pk if channel is not None else None,
                    "title_id": title.pk if title is not None else None,
                    "modality": thread.modality or modality or self.model.Modality.DIRECT,
                    "visibility": thread.visibility or visibility or self.model.Visibility.PRIVATE,
                    "metadata": thread.metadata,
                    "created_by_id": created_by_id,
                },
            )
            # Sources may learn a conversation's name after its first message (a
            # group subject); fill an untitled thread, never rename one.
            if (
                title is not None
                and named.title_id is None
                and self.name_untitled({named.pk: thread.title}, created_by_id=created_by_id)
            ):
                named.refresh_from_db(fields=["title"])
            return named
        referenced = self.referenced_thread(
            platform=platform, channel=channel, in_reply_to=in_reply_to, references=references,
        )
        if referenced is not None:
            return referenced

        normalized = normalize_subject(subject)
        with transaction.atomic():
            if normalized:
                # Subject grouping is a hash lookup on the content-addressed store:
                # the normalized text's fragment (if any) is the only row a matching
                # thread's title pointer can reference, so the tier costs one unique
                # hash probe plus one indexed FK filter — no normalized-text column.
                title_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                title_fragment = fragment_model._base_manager.filter(hash=title_digest).first()
                if title_fragment is not None:
                    existing = (
                        self.lock_if_supported()
                        .filter(platform=platform, channel=channel, title=title_fragment)
                        .order_by("-created_at")
                        .first()
                    )
                    if existing is not None:
                        return existing
            deterministic_id = f"subj:{normalized}" if normalized else f"msg:{message_external_id}"
            title = fragment_model.objects.upsert(text=normalized, created_by_id=created_by_id) if normalized else None
            thread, _created = self.get_or_create_by_external_id(
                platform=platform,
                external_id=deterministic_id,
                defaults={
                    "channel_id": channel.pk if channel is not None else None,
                    "title_id": title.pk if title is not None else None,
                    "modality": modality or self.model.Modality.EMAIL_THREAD,
                    "visibility": visibility or self.model.Visibility.PRIVATE,
                    "created_by_id": created_by_id,
                },
            )
            return thread

    def get_or_create_by_external_id(
        self,
        *,
        platform: str,
        external_id: str,
        defaults: dict[str, Any],
        ownerless: bool = False,
    ) -> tuple[Any, bool]:
        """Get-or-create a thread on its ``(channel, platform, external_id)`` identity, indexed.

        A plain ``get_or_create(external_id=...)`` would seq-scan (the identity index
        carries the MD5 digest, not the value), so the read side filters through
        :func:`_external_id_q`; the create side relies on the expression unique
        constraint to serialise a concurrent first insert, re-reading on conflict —
        the same converge-on-unique contract the old column constraint provided.
        Channel-bound inserts skip personal ownership through the model's save;
        their access follows the channel while creation attribution remains intact.
        ``ownerless`` inserts a thread nobody owns (a record's chatter without a creator).
        """

        # Identity resolution is system bookkeeping (the callers gate reads at their
        # own surface), so it runs elevated — a REBAC-scoped read that cannot see
        # the existing row would double-create and then dead-end.
        # Let Django's FK descriptor resolve either channel= or channel_id=,
        # and use that same proposed row for the insert if no occurrence exists.
        thread = self.model(platform=platform, external_id=external_id, **defaults)
        manager = self.db_manager(self._db or thread._state.db, hints={"instance": thread})
        using = manager.db
        identities = manager.system_context(reason="messaging.thread.identity").lock_if_supported()
        queryset = _external_id_annotated(identities).filter(
            _external_id_q(external_id), platform=platform, channel_id=thread.channel_id,
        )
        existing = queryset.first()
        if existing is not None:
            return existing, False
        try:
            with transaction.atomic(using=using):
                thread.save(force_insert=True, using=using, ownerless=ownerless)
                return thread, True
        except IntegrityError:
            existing = queryset.first()
            if existing is None:
                raise
            return existing, False

    def teardown_for_channel(self, channel: Any) -> None:
        """Purge every thread and message that belongs to ``channel``.

        Deleting a channel is a purge, not an orphan: its threads and messages FK the
        shared ``integrate.Integration`` parent with ``SET_NULL`` (so a bulk integration
        teardown never cascades into unrelated messages), which means deleting the
        channel row alone would leave its conversation rows behind pointing at nothing.
        This deletes messages first (including messages without a thread), then threads,
        so their ``CASCADE`` subtrees (parts, reactions, participants, followers,
        notifications, attachments) go with them. Only *this*
        channel's ``(channel, external_id)`` message rows are touched, so the same
        logical message reached through another channel — a separate row in that
        channel — survives, and a body ``Fragment`` shared with it is spared (parts FK it
        with ``SET_NULL``). Runs under ``system_context`` like the other messaging
        teardown paths; ``channel`` is a Channel (an MTI child of Integration whose pk is
        the Integration pk), so :meth:`MessageQuerySet.for_channel` /
        :meth:`ThreadQuerySet.for_channel` resolve against the shared FK. Both deletes run
        under :func:`~angee.base.signals.mute_changes`: each Message/Thread declares
        ``changes()``, so without muting the ``post_delete`` publisher would fire once per
        row — an ``exists()`` thread probe plus a buffered ``group_send`` for every purged
        row — turning a 90k-message purge into ~180k queries and 90k broadcasts. The single
        Channel-delete event the console's channel list needs is emitted by the later
        ``channel.delete()``, which runs outside this muted teardown.
        """

        if channel.pk is None:
            return
        message_model = apps.get_model("messaging", "Message")
        # Channel deletion authorizes its thread teardown even when a thread has
        # another owner; the thread's own delete gate remains owner/admin-only.
        with system_context(reason="messaging.channel.teardown"), mute_changes(), transaction.atomic():
            for model in (message_model, self.model):
                # A copy in another channel outlives its purged survivor as a canonical row.
                purged = model.objects.for_channel(channel).order_by().values("pk")
                model._base_manager.filter(merged_into__in=purged).update(merged_into=None)
            message_model.objects.for_channel(channel).delete()
            self.for_channel(channel).delete()


class ThreadAttachmentManager[ThreadAttachmentT: ThreadAttachment](AngeeHistoryManager[ThreadAttachmentT]):
    """Owns the polymorphic edge from a model row to its chatter thread.

    The edge names its record by :func:`angee.base.refs.generic_pointer_target`: a gated
    record and each of its REBAC-typed MTI ancestors share one chatter thread,
    and an ungated host keeps its own content type. ``permissions.zed``
    backs one relation per attachable record type with ``target``, so a source
    edge is created and deleted under the actor with write on its record; the
    chatter edge is the record's own thread, which the record's verbs
    materialize under system context.
    """

    def _label(self, value: str) -> str:
        """Bound the edge's display label; the record and title fragment retain full text."""

        return strip_null_bytes(value)[: self.model._meta.get_field("label").max_length]

    def for_record(self, record: Any, *, role: str = "chatter") -> ThreadAttachmentT | None:
        """Return the existing thread attachment for ``record`` and ``role``."""

        if record.pk is None:
            return None
        edges = self.model._base_manager.db_manager(self._db or record._state.db, hints={"instance": record})
        content_type, object_id = generic_pointer_target(record)
        attachment = (
            edges.select_related("thread")
            .filter(content_type=content_type, object_id=object_id, role=role)
            .first()
        )
        if attachment is not None and role == "chatter" and attachment.thread.merged_into_id is not None:
            thread = attachment.thread.canonical()
            attachment = edges.select_related("thread").get(thread=thread, role=role)
            target = attachment.require_threaded_target()
            read_access = target.thread_read_access
            if isinstance(target, ProtectedModel):
                target.require_access(read_access)
            elif not target._message_read_allowed():
                raise PermissionDenied("Reading a redirected chatter thread requires record read access.")
        return attachment

    def source_threads_for_record(self, record: Any) -> Any:
        """Return every source-conversation edge attached to ``record``."""

        if record.pk is None:
            return self.model._base_manager.none()
        record.require_access("read")
        visible_threads = apps.get_model("messaging", "Thread").objects.all().scoped().values("pk")
        return (
            self.all()
            .scoped()
            .select_related("thread")
            .filter(
                **generic_target(record).lookups(self.model, "target"),
                role="source",
                thread_id__in=models.Subquery(visible_threads),
            )
        )

    def bind_source_thread(
        self,
        record: Any,
        thread: Any,
        *,
        label: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Attach one existing conversation as retained source evidence for ``record``.

        Source edges do not turn the conversation into record chatter. The edge's
        ``create`` requires write on the record through the relation its type
        declares, so a type no relation names is refused under an actor. A
        thread reads its record relations through its attachments, so the
        library's backed-edge gate also requires write on the thread.
        ``ValueError`` names an unsaved or untyped record.
        """

        if thread.pk is None:
            raise ValueError("Source thread attachment requires a saved thread.")
        key = generic_target(record).lookups(self.model, "target")
        values = {
            "label": self._label(label or str(record)),
            "metadata": strip_null_bytes(metadata or {}),
        }
        with transaction.atomic():
            self._lock_ends(key, thread)
            attachment, _created = self.get_or_create(thread_id=thread.pk, role="source", **key, defaults=values)
        return attachment

    def unbind_source_thread(self, record: Any, thread: Any) -> int:
        """Remove only the selected source edge; its ``delete`` requires write on the record and thread."""

        if thread.pk is None:
            return 0
        key = generic_target(record).lookups(self.model, "target")
        with transaction.atomic():
            self._lock_ends(key, thread)
            deleted, _details = self.filter(thread=thread, role="source", **key).delete()
        return int(deleted)

    @staticmethod
    def _lock_ends(key: Mapping[str, Any], thread: Any) -> None:
        """Lock the record and the thread before a source edge is written."""

        target_model = key["content_type"].model_class()
        if target_model is None:
            raise ValueError("Source attachment target model is unavailable.")
        target_model.system_queryset(lock=("self",)).get(pk=key["object_id"])
        type(thread).system_queryset(lock=("self",)).get(pk=thread.pk)

    def ensure_for_record(self, record: Any, *, role: str = "chatter", title: str = "") -> ThreadAttachmentT:
        """Return ``record``'s attachment, creating its private chatter thread if needed.

        Both the thread and the attachment are resolved with ``get_or_create`` on a
        deterministic key (the thread on its ``record:…:role`` external id, the
        attachment on the ``(content_type, object_id, role)`` unique constraint), so
        two concurrent first-posts converge on one row instead of the second raising
        an ``IntegrityError`` — ``select_for_update`` cannot lock a row that does not
        exist yet, so a lock-then-create cannot serialise the first insert. The
        record's label is interned as the thread's title fragment.
        """

        if record.pk is None:
            raise ValueError("Cannot attach a thread to an unsaved record.")
        # The host owns whether its record chatter streams over changes(); stamp its
        # opt-in onto the thread so broadcasts_changes() is O(1) at publish time and
        # never re-resolves the polymorphic target.
        host_broadcasts = bool(getattr(record, "thread_broadcasts_changes", False))
        if existing := self.for_record(record, role=role):
            self._reconcile_broadcast_state(existing.thread, host_broadcasts=host_broadcasts)
            return existing
        content_type, object_id = generic_pointer_target(record)
        _assert_canonical_composes_thread_mixin(record, content_type)
        external_id = f"record:{content_type.app_label}.{content_type.model}:{object_id}:{role}"
        thread_model = self.model.thread.field.related_model
        fragment_model = apps.get_model("messaging", "Fragment")
        title_text = strip_null_bytes(title or str(record)).strip()
        manager = self.db_manager(self._db or record._state.db, hints={"instance": record})
        using = manager.db
        # A chatter edge is the record's own thread: the record's verb has admitted
        # the actor, and the edge and its thread are written as system rows. A
        # source edge is created under the actor, who needs write on the record.
        elevated = (
            system_context(reason="messaging.thread_attachment.chatter")
            if role == self.model.AttachmentRole.CHATTER else nullcontext()
        )
        # A record's chatter belongs to the record's creator, never to whoever first
        # follows or posts: ownership is standing access the record does not grant.
        creator_id = getattr(record, "created_by_id", None) if role == self.model.AttachmentRole.CHATTER else None
        owned = {"owner_id": creator_id} if creator_id is not None else {}
        with elevated, transaction.atomic(using=using):
            title_fragment = fragment_model.objects.db_manager(using).upsert(text=title_text) if title_text else None
            thread, _created = thread_model.objects.db_manager(using).get_or_create_by_external_id(
                platform=Handle.Platform.OTHER,
                external_id=external_id,
                ownerless=role == self.model.AttachmentRole.CHATTER and creator_id is None,
                defaults={
                    **owned,
                    "title_id": title_fragment.pk if title_fragment is not None else None,
                    "modality": thread_model.Modality.GROUP,
                    "visibility": thread_model.Visibility.PRIVATE,
                    "host_broadcasts_changes": host_broadcasts,
                },
            )
            self._reconcile_broadcast_state(thread, host_broadcasts=host_broadcasts)
            attachment, _created = self.model._base_manager.using(using).get_or_create(
                content_type_id=content_type.pk,
                object_id=object_id,
                role=role,
                defaults={
                    "thread_id": thread.pk,
                    "label": self._label(str(record)),
                },
            )
            # The thread is deterministic from (content_type, object_id, role), so an
            # existing attachment for this record+role already points at this thread;
            # prime the FK cache to keep the select_related the callers relied on.
            attachment.thread = thread
            return attachment

    @staticmethod
    def _reconcile_broadcast_state(thread: Any, *, host_broadcasts: bool) -> None:
        """Heal a record thread's broadcast state to match its host, in place.

        Broadcasting hosts release personal thread ownership so their live access
        follows membership. The enclosing record operation owns authorization;
        the base release seam retains the original creation attribution.
        """

        if thread.host_broadcasts_changes != host_broadcasts:
            type(thread)._base_manager.db_manager(thread._state.db, hints={"instance": thread}).filter(
                pk=thread.pk,
            ).update(
                host_broadcasts_changes=host_broadcasts,
            )
            thread.host_broadcasts_changes = host_broadcasts
        if host_broadcasts and thread.owner_id is not None:
            type(thread).objects.db_manager(thread._state.db, hints={"instance": thread}).filter(pk=thread.pk).release(
                get_user_model()(pk=thread.owner_id)
            )
            thread.owner = None

    def teardown_for_target(self, target: Any) -> None:
        """Remove target edges while preserving redirected chatter bookkeeping.

        Rebind followers and notifications before deleting a retired host's edge;
        their physical thread identities, preferences and receipts remain intact.
        Only an unshared, unmerged private chatter thread is deleted with its
        native cascade. Source threads retain their independently owned graph.
        """

        if target.pk is None:
            return
        content_type, object_id = generic_pointer_target(target)
        target_model = content_type.model_class()
        if target_model is None:
            return
        thread_model = self.model.thread.field.related_model
        manager = self.db_manager(hints={"instance": target})
        using = manager.db
        with system_context(reason="messaging.record_thread.teardown"), transaction.atomic(using=using):
            target_model._base_manager.db_manager(hints={"instance": target}).select_for_update(
                of=row_lock_paths(target_model),
            ).filter(
                pk=object_id
            ).exists()
            edges = self.model._base_manager.db_manager(hints={"instance": target})
            attachments = list(
                edges.all()
                .lock_if_supported()
                .filter(content_type=content_type, object_id=object_id)
                .order_by("pk")
            )
            if not attachments:
                return
            thread_ids = {edge.thread_id for edge in attachments}
            threads = {
                thread.pk: thread
                for thread in thread_model._base_manager.db_manager(hints={"instance": target})
                .all()
                .lock_if_supported()
                .filter(pk__in=thread_ids)
                .order_by("pk")
            }
            attachment_ids = [edge.pk for edge in attachments]
            chatter_thread_ids = set()
            replacements = {}
            for edge in attachments:
                if edge.role != self.model.AttachmentRole.CHATTER:
                    continue
                chatter_thread_ids.add(edge.thread_id)
                thread = threads[edge.thread_id]
                if thread.merged_into_id is None:
                    continue
                survivor = thread.canonical()
                replacement = (
                    edges.all()
                    .lock_if_supported()
                    .filter(thread_id=survivor.pk, role=self.model.AttachmentRole.CHATTER)
                    .exclude(pk__in=attachment_ids)
                    .first()
                )
                if replacement is None or replacement.target is None:
                    raise ValidationError("Retained chatter requires a surviving record attachment before deletion.")
                replacements[edge.pk] = replacement.pk
            for old_id, new_id in replacements.items():
                for name in ("ThreadFollower", "ThreadNotification"):
                    apps.get_model("messaging", name)._base_manager.db_manager(
                        hints={"instance": target}
                    ).filter(attachment_id=old_id).update(attachment_id=new_id)
            edges.filter(pk__in=attachment_ids).delete()
            orphaned_chatter_ids = [
                thread_id for thread_id in chatter_thread_ids if not edges.filter(thread_id=thread_id).exists()
            ]
            if orphaned_chatter_ids:
                thread_model._base_manager.db_manager(hints={"instance": target}).filter(
                    pk__in=orphaned_chatter_ids,
                    merged_into__isnull=True,
                ).delete()


def _assert_canonical_composes_thread_mixin(record: Any, canonical_content_type: Any) -> None:
    """Fail fast on a child-composed / parent-uncomposed threaded MTI split.

    ``ThreadedModelMixin`` owns the reverse ``thread_attachments`` GenericRelation while
    the attachment row and target-delete policy key on the *canonical* (topmost REBAC-typed) target
    (:func:`angee.base.refs.generic_pointer_target`). If a child composes the mixin but its canonical ancestor
    does not, the ancestor cannot collect the child's attachment on delete and a reused
    primary key would mis-resolve. Guard it where the write keys — the placement
    invariant stated in :mod:`angee.base.refs`.
    """

    from angee.messaging.models import ThreadedModelMixin

    if not isinstance(record, ThreadedModelMixin):
        return
    canonical_model = canonical_content_type.model_class()
    if canonical_model is None or canonical_model is type(record):
        return
    if not issubclass(canonical_model, ThreadedModelMixin):
        raise ImproperlyConfigured(
            f"{type(record)._meta.label} composes ThreadedModelMixin but its canonical record "
            f"target {canonical_model._meta.label} does not; compose the mixin on the topmost "
            "REBAC-typed MTI ancestor the chatter edge keys on."
        )


def _record_attachment(record: Any, *, role: str = "chatter") -> ThreadAttachment | None:
    """Return the chatter attachment edge for ``record`` via its owning manager.

    The resolution logic (content-type lookup, ``select_related`` on the thread,
    unscoped read) lives once on :meth:`ThreadAttachmentManager.for_record`; the
    follower and notification managers resolve their bookkeeping edge through
    that owner rather than each re-deriving it.
    """

    return apps.get_model("messaging", "ThreadAttachment").objects.for_record(record, role=role)


def _record_thread_title(record: Any) -> str:
    """Return the title text a record declares for its chatter thread."""

    title = getattr(record, "message_thread_title", None)
    return str(title()) if callable(title) else str(record)
