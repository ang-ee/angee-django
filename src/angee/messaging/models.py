"""Source models for the messaging addon.

Messaging is threads and messages built on the parties contacts foundation: a
message's sender and recipients are :class:`~angee.parties.models.Handle` rows,
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

import logging
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from typing import Any, ClassVar, Self, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import models, transaction
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import MD5, Coalesce
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from django_compose.models import Composable
from rebac import (
    PermissionDenied,
    SubjectRef,
    current_actor,
    system_context,
    to_subject_ref,
)

from angee.base.fields import ModelLabelField
from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.locks import task_locks_are_cross_process
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel, AngeeModel
from angee.base.models.locks import OptimisticLockMixin
from angee.base.models.merge_references import MergePolicy
from angee.base.models.ownership import OwnerMixin
from angee.base.models.refs import RecordRefMixin
from angee.base.models.trash import TrashMixin
from angee.base.permissions import require_rebac_queryset, user_subject_ref
from angee.base.prefetch import prefetched, prefetched_objects
from angee.base.refs import RecordRef
from angee.base.serialization import strip_null_bytes
from angee.graphql.mixins import ChangeBroadcastMixin
from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import (
    PairingProjection,
    armed_material_key,
    await_session_exit,
    reset_session_store,
    skipped_password_marker,
)
from angee.integrate.models import Bridge, IntegrationCreateMode, IntegrationLifecycle, IntegrationRuntimeStatus
from angee.messaging.backends import (
    ChannelBackend,
    OutboundEmailChannelMissing,
    PairingActionError,
)
from angee.messaging.constants import DELIVERY_TASK_EXPIRES
from angee.messaging.delivery import queue_message_delivery
from angee.messaging.email import header_text
from angee.messaging.managers import (
    ChannelManager,
    DeliveryReconciliationBusy,
    FragmentManager,
    MessageEdgeManager,
    MessageManager,
    MessagePointers,
    MessageStarManager,
    MessageSubtypeManager,
    PartManager,
    ReactionManager,
    ThreadAttachmentManager,
    ThreadFollowerManager,
    ThreadManager,
    ThreadNotificationManager,
    _require_at_most,
    normalize_subject,
)
from angee.messaging.mixins import (
    AudienceMember as AudienceMember,
)
from angee.messaging.mixins import (
    ChatterRecordRefused as ChatterRecordRefused,
)
from angee.messaging.mixins import (
    NotificationPolicy as NotificationPolicy,
)
from angee.messaging.mixins import (
    NotificationPreference as NotificationPreference,
)
from angee.messaging.mixins import (
    ThreadAudienceMixin as ThreadAudienceMixin,
)
from angee.messaging.mixins import (
    ThreadedModelMixin as ThreadedModelMixin,
)
from angee.messaging.mixins import (
    chatter_record as chatter_record,
)
from angee.messaging.resources import MessageResource
from angee.messaging.source_copies import SourceCopyMixin
from angee.parties.models import Handle
from angee.provenance.contrib.angee.history import HistoryMixin, ModelHistory
from angee.provenance.contrib.angee.managers import AngeeHistoryMergeableUnscopedManager
from angee.provenance.statements import (
    SourceKind,
)
from angee.resources.mixins import ResourceLoadMixin
from angee.tags.models import TaggedModel

logger = logging.getLogger(__name__)


class Channel(Bridge, inherits="integrate.Integration"):
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

    rebac_grantable = {"reader": "write", "replier": "write", "annotator": "write"}
    reply_permission: ClassVar[str] = "reply"
    """The channel permission that admits sending on it.

    An answer (``Message.require_reply``) and a new message (``MessageManager.compose``) both decide it.
    """
    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.CONNECT
    live_impl_field: ClassVar[str | None] = "backend_class"

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

        backend_class = self.get_backend_class_impl()
        return backend_class(self)

    @property
    def source_kind(self) -> SourceKind | None:
        """The selected protocol declares whether this channel asserts statements, and as what."""

        return self.get_backend_class_impl().source_kind

    @property
    def system_only(self) -> bool:
        """Whether this channel's protocol serves the system channel alone (``ChannelBackend.system_only``)."""

        return self.get_backend_class_impl().system_only

    def can_reconcile_delivery(self) -> bool:
        """Read the concrete capability's backend declaration without opening it."""

        capability = self.concrete_capability()
        if capability._meta.concrete_model is not self._meta.concrete_model:
            return bool(capability.can_reconcile_delivery())
        return self.get_backend_class_impl().reconciles_delivery()

    def delivery_visibility_delay(self) -> timedelta:
        """Read the selected transport's propagation margin without opening it."""

        capability = self.concrete_capability()
        if capability._meta.concrete_model is not self._meta.concrete_model:
            return cast(timedelta, capability.delivery_visibility_delay())
        return self.get_backend_class_impl().delivery_visibility_delay

    def outbound_sender(self) -> tuple[str, str]:
        """Return this connected channel's backend-owned default email sender.

        A channel that is not connected raises :class:`OutboundEmailChannelMissing`.
        """

        if self.lifecycle != self.Lifecycle.CONNECTED:
            raise OutboundEmailChannelMissing
        return self.backend.outbound_sender()

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

    def require_live_backend(self) -> LiveBridgeImpl:
        """Return the selected live implementation or reject a poll-only channel."""

        impl = self.live_impl
        if not isinstance(impl, LiveBridgeImpl):
            raise PairingActionError("This action requires a live channel.")
        return impl

    def pairing(self) -> PairingProjection:
        """Return this live channel's vendor-neutral pairing projection."""

        return self.require_live_backend().pairing()

    def resume_pairing(self) -> None:
        """Start this live channel, clearing its runtime error but retaining identity.

        A stale pairing report (a dead QR, a rejected account) is dropped only when
        no session provably runs: a running session owns the report it is showing,
        and a process-local lock backend cannot see a worker's session at all.
        """

        impl = self.require_live_backend()
        with system_context(reason="messaging.resume_channel_pairing"):
            self.refresh_from_db(from_queryset=type(self)._base_manager.select_related("credential"))
            if task_locks_are_cross_process() and not self.is_syncing:
                self.update_live_state(identity_key=impl.state_identity_key, drop_pairing_report=True)
            self.set_lifecycle(IntegrationLifecycle.CONNECTED)
            self.report_status(IntegrationRuntimeStatus.OK)
            self.start_live()

    @sensitive_variables("password", "material")
    def submit_pairing_password(self, password: str) -> None:
        """Store one transient secret under the armed material key and signal readiness."""

        self.require_live_backend()
        if not password:
            raise PairingActionError("A channel password is required.")
        with system_context(reason="messaging.submit_channel_password"):
            self.refresh_from_db(from_queryset=type(self)._base_manager.select_related("credential"))
            material_key = armed_material_key(self.subscription_state)
            if not material_key:
                raise PairingActionError("This channel is not awaiting a password.")
            credential = self.credential
            if credential is None:
                raise PairingActionError("This channel has no credential for password input.")
            credential.update_material(**{material_key: password})
            # See ``LiveSession._mark_awaiting_password`` for the awaiting tri-state.
            self.merge_subscription_state(awaiting="")

    def skip_pairing_password(self) -> None:
        """Skip one armed optional secret round and signal that choice to the session."""

        impl = self.require_live_backend()
        with system_context(reason="messaging.skip_channel_password"):
            self.refresh_from_db(from_queryset=type(self)._base_manager.select_related("credential"))
            material_key = armed_material_key(self.subscription_state)
            if not material_key:
                raise PairingActionError("This channel is not awaiting a password.")
            if not impl.pairing().can_skip:
                raise PairingActionError("This channel password cannot be skipped.")
            self.merge_subscription_state(awaiting=skipped_password_marker(material_key))

    def reset_pairing(self) -> None:
        """Stop this live channel, wipe its released session store, and restart pairing.

        The store is wiped only after the session provably exited; an unproven
        shutdown keeps it intact and refuses, so the caller can retry shortly.
        """

        impl = self.require_live_backend()
        with system_context(reason="messaging.reset_channel_pairing"):
            self.stop_live()
            try:
                await_session_exit(self)
            except TimeoutError as error:
                raise PairingActionError("The channel is still shutting down; try again shortly.") from error
            except ImproperlyConfigured as error:
                raise PairingActionError(
                    "Resetting this channel requires a cross-process task lock backend.",
                ) from error
            self.refresh_from_db(from_queryset=type(self)._base_manager.select_related("credential"))
            if self.credential is not None:
                self.credential.update_material(**dict.fromkeys(impl.transient_material_keys))
            reset_session_store(self)
            impl.mark_disconnected(clear_identity=True)
        self.resume_pairing()

    def disconnect_pairing(self) -> None:
        """Stop this live channel and release its account while retaining pairing material."""

        impl = self.require_live_backend()
        with system_context(reason="messaging.disconnect_channel"):
            self.stop_live()
            impl.mark_disconnected(clear_identity=False)

    def start_live(self) -> None:
        """Mark this channel live-desired, then dispatch the backend's live ingest.

        The Bridge live contract for channels: the base persists the desired
        state (so a reconciler can restart a dropped session), the selected
        backend owns the vendor dispatch. A poll-only backend's no-op hook makes
        this safely idempotent on any channel. The desire is merged under a row
        lock so a concurrent live session writing its own pairing keys cannot
        clobber it. An explicit start ends a transient-failure retry streak.
        """

        self.end_live_retry()
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

        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        chatter = attachment_model.AttachmentRole.CHATTER
        if isinstance(snapshot.instance, attachment_model):
            if snapshot.role != chatter:
                return None
            content_type_id, object_id = snapshot.content_type_id, snapshot.object_id
        else:
            edge = attachment_model._base_manager.filter(
                thread_id=snapshot.id, role=chatter,
            ).values_list("content_type_id", "object_id").first()
            if edge is None:
                return None
            content_type_id, object_id = edge
        model = ContentType.objects.get_for_id(content_type_id).model_class()
        if model is None:
            return None
        return model._base_manager.filter(pk=object_id).first()


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
        return type(self)._base_manager.redirect_group(survivor)

    def chatter_record_ref(self) -> RecordRef | None:
        """The canonical chatter host, without loading messages or the document body."""
        attachment = (
            apps.get_model("messaging", "ThreadAttachment")
            ._base_manager.filter(
                thread_id=self.canonical().pk,
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

    @classmethod
    def display_title_fields(cls) -> tuple[str, ...]:
        """Fields :attr:`display_title`, and so ``__str__``, reads from a projected row (``title`` selected)."""

        return ("title__text", "modality")

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
        return attachment_model._base_manager.filter(
            thread_id__in=self.member_threads().values("pk"),
            role=attachment_model.AttachmentRole.CHATTER,
        ).exists()

    def broadcasts_changes(self) -> bool:
        """Whether this thread's changes reach the generic ``changes`` subscription.

        Record chatter stays off the generic surface: its thread's own grants and
        admin read would otherwise deliver change events to a subject who cannot read
        the record.
        A host opts back in per model (a chat room): ``host_broadcasts_changes``, stamped
        from :attr:`ThreadedModelMixin.thread_broadcasts_changes` at attachment, streams
        the thread's changes to its members (who hold ``messaging/thread.reader``) while
        every non-opted record thread stays silent.
        """

        return self.host_broadcasts_changes or not self.is_record_attached()

    @staticmethod
    def _reader_subject(*, user: models.Model | None, user_id: Any) -> SubjectRef:
        """Resolve an existing user before changing direct reader access."""

        if user is None and user_id is not None:
            user = get_user_model()._base_manager.get(pk=user_id)
        return user_subject_ref(user=user, user_id=user_id)

    def grant_reader(self, *, user: models.Model | None = None, user_id: Any = None) -> None:
        """Grant an existing user direct ``reader`` access through the declared share surface."""

        self._grant_declared_record_access(
            Thread.record_access_permission,
            "reader",
            self._reader_subject(user=user, user_id=user_id),
        )

    def revoke_reader(self, *, user: models.Model | None = None, user_id: Any = None) -> None:
        """Revoke an existing user's direct ``reader`` access (mirror of :meth:`grant_reader`)."""

        self._revoke_declared_record_access(
            Thread.record_access_permission,
            "reader",
            self._reader_subject(user=user, user_id=user_id),
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

        cls.objects.teardown_for_target(target)

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
        target = cls.objects.ensure_for_record(survivor, title=str(survivor))
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


class ThreadFollower(AuditMixin, HistoryMixin, AngeeDataModel, concrete=True):
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
    History tracks the subscription, not the receipt: a read is not a version.
    """

    NotificationPolicy = NotificationPolicy

    history = ModelHistory(inherit=True, excluded_fields=("last_read_message",))
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


class MessageSubtype(AuditMixin, AngeeDataModel, concrete=True):
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
class ReplyEnvelope:
    """Whom an answer to a message goes to, under which subject, from which address (``Message.reply_envelope``).

    An email answer goes to ``addresses`` (the ``Reply-To``'s, each ``(address,
    display name)``), else to the handle ``handle_id`` names (the sender); it is
    titled ``subject`` and sent from the channel's default ``sender``, ``(name,
    address)``. An answer of another kind carries none of these: its transport's
    own sender answers, and it addresses nobody.
    """

    addresses: tuple[tuple[str, str], ...] = ()
    handle_id: Any = None
    subject: str = ""
    sender: tuple[str, str] | None = None


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
    TrashMixin,
    OptimisticLockMixin,
    AuditMixin,
    AngeeDataModel,
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
    in ``metadata`` as the lossless source behind :class:`Recipient`.

    Edits are data, not shadow rows: ``edit_history`` appends newest-first
    ``{edited_at, edited_by_id, prev_fragment_hashes}`` entries while the replaced
    text survives as immutable content-addressed fragments — no per-save history
    table doubling the hot write path.

    Moderation is trash (:class:`~angee.base.models.trash.TrashMixin`): a trashed
    message is withheld from every reader but channel managers and admins, and
    record chatter leaves it out of every read but its moderators' removed list.

    A handle merge keeps a message on the handle it was sent from; readers
    resolve ``sender`` through the handle's merge redirect.
    """

    merge_policies: ClassVar[Mapping[str, Any]] = {"sender": MergePolicy.KEEP}

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
        ingest producers and by ``MessageManager.compose`` (an outgoing message
        keeps its parent's kind, and a new one is an email), ``NOTIFICATION`` by
        the retained system log. Add a value only together with its producer.
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
        # The metadata.mailbox group axis drills by exact match on this one key, so the
        # provider metadata document itself is not a filter.
        "metadata_mailbox": lambda rows: KeyTextTransform(
            "mailbox", "metadata", output_field=models.TextField(null=True),
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

    @staticmethod
    def local_metadata_annotation() -> dict[str, Any]:
        """Annotate a list's rows with their local facts, which :meth:`local_fact` then reads.

        The ``local`` object is read whole, as JSON, so every fact keeps its
        stored type on SQLite as on PostgreSQL. Reading one key would not:
        SQLite decodes a text that looks like JSON (``"0"``) as another value,
        and renders ``false`` or ``null`` as text.
        """

        return {"_local_metadata": models.F("metadata__local")}

    def local_fact(self, key: str) -> Any:
        """The local fact ``key``, from the row's :meth:`local_metadata_annotation` when a list loaded it."""

        local = self.__dict__.get("_local_metadata", models.DEFERRED)
        if local is models.DEFERRED:
            return self.local_metadata.get(key)
        return local.get(key) if isinstance(local, dict) else None

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

        if self.is_trashed and not self.delivery_needs_reconciliation:
            return True
        return bool(self.local_fact("delivery_conflict"))

    delivery_action_permission: ClassVar[str] = "send_held"

    def delivery_actions(self, permissions: Collection[str]) -> list[str]:
        """Delivery recovery verbs admitted by the held permission and retained envelope."""

        if self.delivery_action_permission not in permissions:
            return []
        return [
            action for action in ("retry", "acknowledge")
            if self.delivery_resolution_error(retry=action == "retry") is None
        ]

    def delivery_error(self, permissions: Collection[str]) -> str | None:
        """Why delivery last failed, for a viewer holding :attr:`delivery_action_permission`; else ``None``.

        Settlement records the refusal in words safe for operators
        (``safe_integration_failure``), so it is read by those who may act on it,
        as :meth:`delivery_actions` is. Acceptance clears it; an operator's retry
        keeps it until the next settlement.
        """

        if self.delivery_action_permission not in permissions:
            return None
        error = self.local_fact("delivery_error")
        return str(error) if error else None

    @property
    def delivery_attempts(self) -> int:
        """The provider attempts settlement has counted, ``0`` before the first; an operator's retry resets it."""

        return int(self.local_fact("delivery_attempts") or 0)

    def delivery_resolution_error(self, *, retry: bool) -> ValidationError | None:
        """The retained row's admission for an operator retry or acknowledgement.

        The locked mutation and the viewer's delivery-actions projection use
        this predicate. Authorization and observed-revision checks stay with
        their respective owners.
        """

        if (
            self.status == self.MessageStatus.UNCERTAIN and self.scheduled_at is None
            and self.delivery_lease_until is not None and self.delivery_lease_until > timezone.now()
        ):
            return DeliveryReconciliationBusy()
        allowed = (
            (self.MessageStatus.UNCERTAIN, self.MessageStatus.FAILED) if retry else (self.MessageStatus.UNCERTAIN,)
        )
        if self.status not in allowed or self.delivery_blocked:
            return ValidationError(
                "Retry requires a retained failed or uncertain delivery; acknowledgement requires uncertainty."
            )
        if retry:
            try:
                self.validate_delivery()
            except ValidationError as error:
                return error
        return None

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
            # Cross-channel identity reads (an email's copies in other accounts, a
            # platform's post ids) span channels, which the channel-led unique index
            # cannot serve — this digest probe can. Reply threading stays channel-scoped.
            models.Index(
                MD5("external_id"),
                name="ix_message_external_id_md5",
                condition=~models.Q(external_id=""),
            ),
        )

    def source_header_values(self, name: str, *, actor: Any = None) -> tuple[str, ...]:
        """Read original envelope values, falling back to canonical header parts.

        Transport adapters may retain the complete envelope in metadata while
        projecting only selected headers into Parts. Read that original source
        before a mutable Handle reference; neither source establishes authenticity.
        Repeated headers remain repeated so callers can reject ambiguous addresses.
        Malformed retained values yield no usable header, never a partial identity.
        """
        actor = self.require_access("read", actor=actor)
        header = name.casefold()
        headers = self.metadata.get("headers") if isinstance(self.metadata, dict) else None
        if isinstance(headers, dict):
            values = [value for key, value in headers.items() if str(key).casefold() == header]
            if values:
                retained: list[str] = []
                for value in values:
                    items = (value,) if isinstance(value, str) else value
                    if not isinstance(items, list | tuple) or any(not isinstance(item, str) for item in items):
                        return ()
                    retained.extend(items)
                return tuple(retained)
        parts = self.parts.with_actor(actor) if actor is not None else self.parts.all()
        return tuple(parts.filter(
            parent__isnull=True, role="header", name__iexact=name, fragment__isnull=False,
        ).order_by("position", "pk").values_list("fragment__text", flat=True))

    def reply_addresses(self, *, actor: Any = None) -> dict[str, str]:
        """Return the addresses this message's ``Reply-To`` names, each with its display name.

        The header is read through :meth:`source_header_values` with the caller's
        access, in header order. Which entries are one address is the spelling
        key's (``Handle.spelling_key_of``); each is returned as the header first
        spells it, with that entry's display name: what an answer is landed on
        (``MessageManager.compose``). An address no handle holds yet is stored and
        sent in that spelling; an address a handle holds is sent as that handle
        stores it, with its display name, as every recipient is. An entry the
        handle owner's predicate (``Handle.is_email_address``) rejects is no
        address. Without a ``Reply-To`` the mapping is empty.
        """

        addresses: dict[str, str] = {}
        named: set[str] = set()
        for name, address in getaddresses(self.source_header_values("reply-to", actor=actor)):
            address = address.strip()
            key = Handle.spelling_key_of(Handle.Platform.EMAIL, address)
            if Handle.is_email_address(address) and key not in named:
                named.add(key)
                addresses[address] = name.strip()
        return addresses

    def require_reply(self, actor: Any) -> Channel:
        """Return the channel ``actor`` answers this message on, once they may read it and reply there.

        Read on the message is decided first, so a refusal reads nothing of a
        channel the actor may not know of; the channel is then read once, under
        the actor, and its ``reply`` decided. Who may answer is decided here alone;
        whether the message can be answered is :meth:`reply_envelope`'s. A
        refusal raises ``PermissionDenied``.
        """

        if self.pk is None or self.channel_id is None or not self.with_actor(actor).has_access("read"):
            raise PermissionDenied("Answering a message requires reading it and reply on its channel.")
        channel = apps.get_model("messaging", "Channel").objects.with_actor(actor).filter(pk=self.channel_id).first()
        if channel is None or not channel.has_access(channel.reply_permission):
            raise PermissionDenied("Answering a message requires reading it and reply on its channel.")
        return cast(Channel, channel)

    def reply_envelope(self, *, actor: Any = None, channel: Channel | None = None) -> ReplyEnvelope:
        """Return whom ``MessageManager.compose`` answers this message to, or raise why it cannot.

        An answer needs a saved message with a thread and a channel (``parent``,
        code ``reply_incomplete``). An email is answered from its channel's default
        sender (``Channel.outbound_sender``, whose refusal is raised as it is) under
        ``Re: <thread title>``, to its ``Reply-To`` (:meth:`reply_addresses`, read
        with ``actor``'s access). A ``Reply-To`` that names no address is refused
        (``reply_to``, ``reply_to_invalid``) rather than answered at its sender,
        which may be a relay; only a message with no ``Reply-To`` is answered at
        its sender (``parent``, ``reply_unaddressed`` when it has none, or one the
        handle owner's predicate does not take as an address). A
        ``Reply-To`` naming more than :meth:`MessageManager.recipient_limit` addresses
        is refused (``reply_to``, ``too_many``), as a typed list of as many is. Any other
        kind is answered by its transport's own sender: the envelope is empty.
        ``channel`` is the transport channel when the caller has read it
        (:meth:`require_reply`); otherwise it is read here. Who may answer is
        :meth:`require_reply`'s.
        """

        if self.pk is None or self.thread_id is None or self.channel_id is None:
            raise ValidationError({"parent": ValidationError(
                "An answer needs a saved message with a thread and a channel.", code="reply_incomplete",
            )})
        if self.message_type != self.MessageKind.EMAIL:
            return ReplyEnvelope()
        if channel is None:
            channel = cast(Channel, self.transport_channel(reason="messaging.reply_envelope"))
        sender = channel.outbound_sender()
        addresses = self.reply_addresses(actor=actor)
        _require_at_most(MessageManager.recipient_limit(), "recipients", (("reply_to", tuple(addresses)),))
        if not addresses and any(value.strip() for value in self.source_header_values("reply-to", actor=actor)):
            raise ValidationError({"reply_to": ValidationError(
                "The message's Reply-To names no address to answer.", code="reply_to_invalid",
            )})
        if not addresses and (self.sender is None or not Handle.is_email_address(self.sender.value)):
            # The sender an answer falls back to is checked as a Reply-To address is.
            raise ValidationError({"parent": ValidationError(
                "An email answer needs a Reply-To or a sender to address.", code="reply_unaddressed",
            )})
        title = normalize_subject(cast(Thread, self.thread).display_title)
        return ReplyEnvelope(
            addresses=tuple(addresses.items()),
            handle_id=None if addresses else self.sender_id,
            subject=header_text(f"Re: {title}") if title else "",
            sender=sender,
        )

    def reply_error(self, *, actor: Any = None, channel: Channel | None = None) -> ValidationError | None:
        """Why ``MessageManager.compose`` cannot answer this message (:meth:`reply_envelope`'s refusal), or ``None``.

        Side-effect free; who may answer is :meth:`require_reply`'s.
        """

        try:
            self.reply_envelope(actor=actor, channel=channel)
        except ValidationError as error:
            return error
        return None

    def webform_submission(self) -> WebformSubmission | None:
        """Read the ``webform`` envelope a form submission's message carries in its metadata.

        The envelope holds the ``answers`` by question name and the
        ``unverified_submitter_email``. Consumers read it here and never
        interpret the metadata themselves; a message without one returns ``None``.
        """

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

        if self.message_type != self.MessageKind.COMMENT:
            return "Only comment messages can be deleted."
        return None

    def trash_error(self) -> str | None:
        """Only comments and received messages may be trashed; sent mail and system notes stay on record.

        Trash is a record moderator's verb, asked on the record's own thread
        (``ThreadedModelMixin.message_trash``, the chatter's ``can_trash``). A
        message received there came from outside, whoever sent it (an answer that
        its threading headers placed on the chatter, or anything mailed with a
        chatter mail's Message-ID), so its moderator may take it off as a comment.
        A record's chatter thread has no channel, nor has one a record merge folded
        in: a message whose own thread has one is mail in a mailbox's conversation
        that an earlier version merged into the record, and stays its owner's.
        """

        if self.message_type == self.MessageKind.COMMENT:
            return None
        if self.thread_id is not None and cast(Thread, self.thread).channel_id is not None:
            return "Mail in its owner's own mailbox conversation stays theirs to remove."
        if self.direction == self.Direction.INBOUND:
            return None
        return "Only comments and received messages can be removed."

    def can_trash(self, *, moderate_access: bool) -> bool:
        """Return whether a record moderator may move this comment to the trash."""

        return moderate_access and not self.is_trashed and self.trash_error() is None

    def trash(self, *, reason: str = "", recount: bool = True) -> None:
        """Trash and cancel unsent delivery; restoring never re-arms a send."""

        with transaction.atomic():
            type(self).objects.cancel_delivery(self)
            super().trash(reason=reason)
            if recount:
                self._recount_thread()

    def restore(self) -> None:
        """Restore this message and recount its thread with it."""

        with transaction.atomic():
            super().restore()
            self._recount_thread()

    def _recount_thread(self) -> None:
        """Keep the thread's counters to its untrashed messages; bookkeeping, not access."""

        if self.thread_id is None:
            return
        thread_model = type(self).thread.field.related_model
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

        reactions = prefetched_objects(self, "reactions")
        if reactions is None:
            reactions = list(
                apps.get_model("messaging", "Reaction")
                ._base_manager.filter(message=self)
                .select_related("handle")
                .order_by("pk")
            )
        user_id = (user.pk if user is not None else None)
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

        reference = self.threaded_record_ref()
        if reference is None:
            return None
        return apps.get_model(reference.model_label)._base_manager.filter(pk=reference.object_id).first()

    def threaded_record_ref(self) -> RecordRef | None:
        """The chatter host's identity, without loading its document body."""

        thread = self.thread
        if thread is None:
            return None
        return thread.chatter_record_ref()

    def sender_name(self) -> str:
        """Return the actor-visible sender label, using its selected SQL value."""

        annotated = self.__dict__.get("_sender_name", None)
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

        annotated = self.__dict__.get("_thread_title", None)
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

        annotated = self.__dict__.get("_channel_vendor_name", None)
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

        annotated = self.__dict__.get("_title_text", None)
        if annotated is not None:
            # A list-scale read annotated the title in SQL (with_title_text) —
            # Coalesce makes "" the no-title value, so None only means unannotated.
            return str(annotated)
        parts = prefetched_objects(self, "parts")
        if parts is not None:
            for part in parts:
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
        """Reserve a due draft/queue or eligible uncertain recovery under its row lock.

        Returns the locked row stamped with a bounded dispatch lease, or ``None``
        when nothing is due. FAILED rows require the explicit operator retry verb.
        Uncertain rows retain their status and revision; recovery reserves only a
        possibly accepted send. This reservation fences broker submission; the
        worker's ``MessageManager.claim_delivery`` is the separate effect claim.
        """

        if self.pk is None:
            raise ValidationError("Cannot deliver an unsaved message.")
        with system_context(reason="messaging.delivery.queue"), transaction.atomic():
            row = type(self).objects.sudo(reason="messaging.delivery.queue").lock_if_supported().get(pk=self.pk)
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
                type(row)._base_manager.filter(pk=row.pk).update(
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

    # A row's str is its preview, so an error report (DEBUG off) hides every local from here down, for any mail.
    @sensitive_variables()
    def deliver(self) -> bool:
        """Queue this outbound message through messaging's fenced delivery owner.

        The transport always runs through ``angee.jobs``, never in the request.
        """

        return queue_message_delivery(self)

    # The operator's verbs hold the row, whose str is its preview: an error report (DEBUG off)
    # hides every local from here down, the manager's resolution and Django's save included.
    @sensitive_variables()
    def acknowledge_delivery(self, *, actor: Any, expected_revision: int) -> Any:
        """Confirm an uncertain provider effect as sent without making another call."""

        return type(self).objects.resolve_uncertain_delivery(
            self, actor=actor, expected_revision=expected_revision, retry=False,
        )

    @sensitive_variables()
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


_MESSAGE_POINTER = "_inbox_message_pointer"
"""Where a notification's narrow message context lands, through ``ThreadNotification.message_pointers_prefetch``."""


class ThreadNotification(AuditMixin, AngeeDataModel, concrete=True):
    """One recipient's inbox item and its acknowledgement.

    ``read_at`` records acknowledgement of this notification. A derived-audience
    recipient need not follow the record. ``ThreadFollower.last_read_message`` instead
    records an explicit follower's position in the record's message history;
    acknowledging an inbox item neither creates a follow nor advances that cursor.
    Every account told under inbox or email policy gets a delivery row, including
    explicit followers; acknowledgement and the follower cursor remain independent.
    """

    class NotificationType(models.TextChoices):
        """How this notification should be delivered."""

        INBOX = "inbox", "Inbox"
        EMAIL = "email", "Email"

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
    read_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(blank=True, default=dict)

    objects = ThreadNotificationManager()
    _message_pointers = MessagePointers()

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
        indexes = (models.Index(fields=("thread", "user")),)

    def __str__(self) -> str:
        """Return a readable notification label."""

        return f"{self.user_id} notified for {self.message_id}"

    @classmethod
    def message_pointers_prefetch(cls, actor: Any) -> models.Prefetch:
        """Read a notification list's narrow message context for ``actor`` once, as a native prefetch hint."""

        return models.Prefetch(
            "_message_pointers", queryset=require_rebac_queryset(cls, actor=actor), to_attr=_MESSAGE_POINTER,
        )

    def message_pointer(self, actor: Any) -> Any:
        """This notification's narrow message context for ``actor``, from its list's batch when one read it."""

        return prefetched(self, _MESSAGE_POINTER, lambda: type(self).message_pointers_prefetch(actor))


class Fragment(AuditMixin, AngeeDataModel, concrete=True):
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


class Part(AuditMixin, AngeeDataModel, concrete=True):
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


class MessageEdge(AuditMixin, AngeeDataModel, concrete=True):
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


class Recipient(AuditMixin, AngeeDataModel, concrete=True):
    """A handle a message is addressed to, in one envelope role: the queryable recipient row.

    The raw To/Cc/Bcc stays in ``Message.metadata`` as the lossless source; this is
    its queryable projection, so the inbox can group and filter by recipient. The
    sender is ``Message.sender``, never a row here. ``thread`` is its message's
    thread, set by :meth:`save` on insert and moved with the message by
    ``MessageManager.correct_source_thread``. A handle merge keeps the row on the
    handle it names; readers resolve ``handle`` through the handle's merge redirect.
    ``address`` is the spelling a writer was handed for the handle, which the
    message is mailed at (:attr:`envelope_address`); readers and search still go by
    the handle.
    """

    merge_policies: ClassVar[Mapping[str, Any]] = {"handle": MergePolicy.KEEP}

    class RecipientRole(models.TextChoices):
        """The RFC-5322 envelope role of a recipient."""

        TO = "to", "To"
        CC = "cc", "Cc"
        BCC = "bcc", "Bcc"

    sqid_prefix = "rcp_"
    thread = models.ForeignKey(
        "messaging.Thread",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    message = models.ForeignKey(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    handle = models.ForeignKey(
        "parties.Handle",
        on_delete=models.CASCADE,
        related_name="recipient_rows",
    )
    role = StateField(choices_enum=RecipientRole, default=RecipientRole.TO)
    address = models.CharField(max_length=512, blank=True, default="", db_default="", editable=False)
    """The address the caller handed for this recipient, as its recipient rule cleaned it
    (``MessageManager.clean_recipient``): a spelling of the handle's address (``Handle.is_spelling_of``).
    Empty for a recipient no caller handed as an address (one a reply inherits from its parent, one known
    only as a handle, an ingested one), which is mailed at the handle's own ``value``. It leaves with the
    row: with its handle, its message and a channel's purge."""

    class Meta:
        """Django model options for the recipient source model."""

        abstract = True
        ordering = ("role", "sqid")
        rebac_resource_type = "messaging/recipient"
        constraints = (
            # One row per envelope fact; the write path dedupes a repeated
            # address, the constraint keeps a concurrent rebuild honest.
            models.UniqueConstraint(
                fields=("message", "handle", "role"),
                name="uq_recipient_message_handle_role",
            ),
        )

    def __str__(self) -> str:
        """Return a readable recipient label for Django displays."""

        return f"{self.handle_id} ({self.role})"

    @property
    def envelope_address(self) -> str:
        """The address this row is mailed at: :attr:`address` while it spells the handle's address, else the handle's
        own ``value``.

        A row whose ``address`` names another address, written past :meth:`save` (a queryset ``update``), is mailed at
        its handle's ``value`` with a warning naming the row, so mail never goes where its handle does not name.
        """

        handle = self.handle
        if self.address and not handle.is_spelling_of(self.address):
            logger.warning("Recipient %s holds an address its handle does not name; it is mailed at the handle's own.",
                           self.pk)
            return str(handle.value)
        return self.address or str(handle.value)

    def clean(self) -> None:
        """Validate the row as :meth:`save` does: an ``address`` is a spelling of the handle's address."""

        super().clean()
        self._require_its_address()

    def _require_its_address(self) -> None:
        if self.address and not self.handle.is_spelling_of(self.address):
            raise ValidationError({"address": ValidationError(
                "A recipient's address is a spelling of its handle's address.", code="not_its_handle",
            )})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Insert the row on its message's thread; a different ``thread`` raises ``ValueError``.

        Pass ``message=`` (the instance) so the message is not read again, and ``handle=`` when ``address`` is set, so
        the handle is not read either. An ``address`` that is not a spelling of the handle's address raises
        ``ValidationError`` on ``address`` before anything is written.
        """

        self._require_its_address()
        if self._state.adding:
            thread_id = self.message.thread_id
            if self.thread_id is not None and self.thread_id != thread_id:
                raise ValueError("A recipient row is on its message's thread.")
            self.thread_id = thread_id
        super().save(*args, **kwargs)


class Reaction(AuditMixin, HistoryMixin, AngeeDataModel, concrete=True):
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


class MessageStar(AuditMixin, AngeeDataModel, concrete=True):
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
