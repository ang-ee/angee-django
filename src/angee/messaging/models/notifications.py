"""Notifications models for Messaging; public imports remain on the package."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models.fields.json import DataContains
from django.db.models.functions import JSONArray
from django.db.models.sql.compiler import SQLCompiler

from angee.base.fields import DiagnosticTextField
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.messaging.managers import (
    ThreadFollowerManager,
    ThreadNotificationManager,
)


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


class ThreadFollower(AuditMixin, AngeeDataModel, concrete=True):
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


class ThreadNotification(AuditMixin, AngeeDataModel, concrete=True):
    """A recipient's delivery ledger and acknowledgement of one inbox item.

    ``read_at`` records acknowledgement of this notification independently of
    the delivery lifecycle and failure diagnostics. A derived-audience recipient
    need not follow the record. ``ThreadFollower.last_read_message`` instead
    records an explicit follower's position in the record's message history;
    acknowledging an inbox item neither creates a follow nor advances that cursor.
    Every account told under inbox or email policy gets a delivery row, including
    explicit followers; acknowledgement and the follower cursor remain independent.
    """


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
    failure_reason = DiagnosticTextField(blank=True, default="")
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
