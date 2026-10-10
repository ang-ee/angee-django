"""Source models for the posts addon — public feeds, engagement, and following.

Posts is the public-post surface layered on ``messaging``. It reuses the one
idempotent ``Message.objects.ingest`` write path (a public post *is* a
``messaging.Message`` in a ``messaging.Thread``) and adds the posts overlay:

- :class:`Feed` — a ``messaging.Channel`` child that polls an external platform
  for public posts. Its ``FeedBackend`` does transport and parsing, and ``sync()``
  uses integrate streams to land each post through messaging and engagement.
- :class:`FeedFollow` — the following / timeline subscription edge.
- :class:`PostMetrics` — rolled-up public engagement counts for a message.
- per-actor post reactions (like / repost / emoji) reuse the single
  ``messaging.Reaction`` table — posts writes ``like``/``repost`` as reaction values
  on the shared ``messaging.Message`` rather than owning a parallel table.
- :class:`Quota` — the per-integration API-unit ledger feed backends spend.
- :class:`ThreadPublic` / :class:`MessagePublic` — the public-thread fields posts
  contributes **onto** ``messaging.Thread`` / ``messaging.Message`` through the
  same-row ``extends`` seam.

The dependency points one way (posts → messaging → parties/integrate/storage);
posts never edits or forks messaging.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from rebac import PermissionDenied, system_context
from rebac.mixins import RebacModelBase

from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel
from angee.integrate.models import IntegrationCreateMode
from angee.posts.backends import FeedBackend
from angee.posts.managers import (
    FeedFollowManager,
    PostMetricsManager,
    QuotaManager,
)

if TYPE_CHECKING:
    from angee.messaging.models import Message


def validate_reply_hold(hours: float | None) -> None:
    """Validate the operator's nullable, finite duration in hours."""

    if hours is not None and (not isfinite(hours) or hours < 0):
        raise ValidationError("Reply hold must be null or finite nonnegative hours.")
    if hours is not None:
        try:
            timezone.now() + timedelta(hours=hours)
        except OverflowError as error:
            raise ValidationError("Reply hold is too large.") from error


class Feed(models.Model, metaclass=RebacModelBase):
    """A connected public-content source that polls an external platform for posts.

    A ``messaging.Channel`` child (identity, bridge state and message access from
    its parents). The scheduler drives its inherited ``Bridge`` through ``sync``.
    ``feed_backend_class`` selects the platform. Downstream ``posts_integrate_*``
    addons contribute ``youtube`` / ``facebook``; ``manual`` is the neutral null-object.

    A *paused* feed carries a NULL ``next_sync_at`` (not scheduled); activating it
    schedules the first poll. ``handle`` is the ``parties.Handle`` the feed monitors
    and posts as (its OAuth token lives on the handle / the integration credential).
    """

    runtime = True
    extends = "messaging.Channel"
    integration_create_mode = IntegrationCreateMode.FORM
    live_impl_field = None

    feed_backend_class = ImplClassField(
        FeedBackend,
        default="manual",
        create_only=True,
    )
    """Registry key for the feed backend bound to this feed."""

    external_id = models.CharField(max_length=512, blank=True, default="")
    """The external channel/page/account id this feed follows on its platform."""
    handle = models.ForeignKey(
        "parties.Handle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="monitored_feeds",
    )
    reply_hold = models.FloatField(null=True, blank=True, default=None, validators=(validate_reply_hold,))
    """Hours until release; null is approval-only with no expiry; zero queues now."""
    live_since = models.DateTimeField(null=True, blank=True)
    """Binding-finish horizon; record timestamps before it are historical."""

    class Meta:
        """Django model options for the feed child model."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "posts/feed"

    @property
    def backend(self) -> FeedBackend:
        """Return this feed's selected backend, bound to this row."""

        backend_class = cast("type[FeedBackend]", self.resolve_impl("feed_backend_class"))
        return backend_class(self)

    @property
    def capability_impl(self) -> FeedBackend:
        """Use the feed selector when Integration dispatches a capability."""

        return self.backend

    def test_connection(self) -> str:
        """Use the inherited credential probe for a periodic feed."""

        return self.probe_credential()

    def binding_finished(self) -> None:
        """Stamp the live horizon once after integrate finishes binding this model."""

        with transaction.atomic():
            row = type(self)._base_manager.select_for_update().get(pk=self.pk)
            if row.live_since is None:
                row.live_since = timezone.now()
                row.save(update_fields=("live_since", "updated_at"))
            self.live_since = row.live_since

    def is_historical(self, record: Any) -> bool:
        """Classify each post independently while history and live streams overlap."""

        return record.message.sent_at is None or self.live_since is None or record.message.sent_at < self.live_since

    def reply_schedule(self) -> datetime | None:
        """Compute an aware release instant from this feed's validated hold policy."""

        hours = self.reply_hold
        validate_reply_hold(hours)
        if hours is None or hours == 0:
            return None
        return timezone.now() + timedelta(hours=hours)


class FeedFollow(AuditMixin, AngeeDataModel):
    """A follow of a :class:`Feed` by a ``parties.Handle`` — the timeline subscription.

    The following edge behind a public timeline: a handle subscribes to a feed's
    posts. ``ended_at`` closes a follow (an open/closed interval), so unfollowing is
    an update, not a delete, and the history is retained. The timeline itself is the
    derived join ``FeedFollow → Feed → Thread → Message`` (a downstream query owner).
    """

    runtime = True
    sqid_prefix = "ffl_"

    feed = models.ForeignKey(
        "posts.Feed",
        on_delete=models.CASCADE,
        related_name="followers",
    )
    handle = models.ForeignKey(
        "parties.Handle",
        on_delete=models.CASCADE,
        related_name="followed_feeds",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = FeedFollowManager()

    class Meta:
        """Django model options for the feed-follow source model."""

        abstract = True
        ordering = ("-started_at", "sqid")
        rebac_resource_type = "posts/feed_follow"
        constraints = (
            models.UniqueConstraint(
                fields=("feed", "handle"),
                name="uq_feed_follow_feed_handle",
            ),
        )

    def __str__(self) -> str:
        """Return a readable follow label for Django displays."""

        return f"{self.handle_id} → {self.feed_id}"


class PostMetrics(AuditMixin, AngeeDataModel):
    """Rolled-up public engagement counts for one message (the platform snapshot).

    Flat one-to-one, not MTI — the counter set overlaps heavily across platforms;
    platform extras go in ``metadata``. Counters are overwritten with the latest
    platform snapshot (no ``F()`` delta), so the feed sync is the single writer.
    """

    runtime = True
    sqid_prefix = "pmx_"

    message = models.OneToOneField(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="post_metrics",
    )
    view_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)
    repost_count = models.PositiveIntegerField(default=0)
    quote_count = models.PositiveIntegerField(default=0)
    reply_count = models.PositiveIntegerField(default=0)
    bookmark_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(blank=True, default=dict)

    objects = PostMetricsManager()

    class Meta:
        """Django model options for the post-metrics source model."""

        abstract = True
        verbose_name_plural = "post metrics"
        rebac_resource_type = "posts/post_metrics"

    def __str__(self) -> str:
        """Return a readable metrics label for Django displays."""

        return f"metrics:{self.message_id}"


class Quota(AuditMixin, AngeeDataModel):
    """A per-integration API-unit ledger for one billing period.

    Feed backends spend platform API units (search, list, insert) against a per-period
    budget; :meth:`~angee.posts.managers.QuotaManager.consume` atomically debits this
    ledger and refuses when the budget is insufficient. Enforcement is cooperative —
    the backend must ask before it spends.
    """

    runtime = True
    sqid_prefix = "qta_"

    integration = models.ForeignKey(
        "integrate.Integration",
        on_delete=models.CASCADE,
        related_name="quotas",
    )
    period_start = models.DateTimeField(db_index=True)
    period_end = models.DateTimeField()
    quota_used = models.PositiveIntegerField(default=0)
    quota_limit = models.PositiveIntegerField(default=10000)
    last_updated = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(blank=True, default=dict)

    objects = QuotaManager()

    class Meta:
        """Django model options for the quota source model."""

        abstract = True
        ordering = ("-period_start", "sqid")
        rebac_resource_type = "posts/quota"
        constraints = (
            models.UniqueConstraint(
                fields=("integration", "period_start"),
                name="uq_quota_integration_period",
            ),
        )

    def __str__(self) -> str:
        """Return a readable quota label for Django displays."""

        return f"{self.integration_id}: {self.quota_used}/{self.quota_limit}"


# --- Same-row extensions onto messaging (the public-post payload) ---------------
#
# These fold the payload-only public-post columns into the SINGLE messaging.Thread /
# messaging.Message tables via Angee's same-row ``extends`` seam (abstract +
# ``extends``, NO ``runtime`` — like ``iam_integrate_oidc.OAuthClientOidc``). Only
# fields with no base producer are extended here: ``modality``/``visibility`` STAY
# owned by ``messaging`` (its ``ThreadManager.resolve`` writes both on every thread,
# and its schema/console bind them), so posts sets ``modality=public_thread`` /
# ``visibility=public`` through that owner rather than re-owning the columns. The base
# ``messaging`` slice carries no field of these names, so the composer folds these onto
# the one table with no collision.


class ThreadPublic(models.Model):
    """Public-post payload posts contributes onto ``messaging.Thread`` (same row).

    ``subject_url`` links a public thread to its post's canonical URL. It has no
    producer in base messaging, so posts owns it; the structural ``modality``/
    ``visibility`` discriminators stay owned by messaging.
    """

    extends = "messaging.Thread"

    subject_url = models.URLField(max_length=1024, blank=True, default="")

    class Meta:
        """Abstract same-row extension composed into ``messaging.Thread``."""

        abstract = True


class CommentAnswered(ValidationError):
    """The comment already has an answer; a new reply is unnecessary."""


class MessagePublic(models.Model):
    """Public-post fields posts contributes onto ``messaging.Message`` (same row).

    ``is_original_post`` marks the root post of a public thread (a post with no parent).
    It has no producer in base messaging, so posts owns it and the composer folds it
    onto the single ``messaging.Message`` table.
    """

    extends = "messaging.Message"

    hasura_filterable_fields = ("is_original_post",)

    is_original_post = models.BooleanField(default=False)

    def reply_state(self) -> bool:
        """Whether this comment is channel-authored or already has an active reply.

        Answer evidence is checked elevated: another author's held reply still
        excludes a second reply even when the caller cannot read that draft.
        """

        message = cast("Message", self)
        with system_context(reason="posts.reply.evidence"):
            sender_id, channel_id = type(message)._base_manager.filter(pk=message.pk).values_list(
                "sender_id", "channel_id",
            ).get()
            handle_id = apps.get_model("posts", "Feed")._base_manager.filter(
                pk=channel_id,
            ).values_list("handle_id", flat=True).first()
            if handle_id is not None and sender_id == handle_id:
                return True
            replies = type(message)._base_manager.filter(
                parent_id=message.pk, channel_id=channel_id, is_trashed=False,
            )
            active = models.Q(direction=message.Direction.OUTBOUND, status__in=message.ACTIVE_OUTBOUND_STATUSES)
            if handle_id is not None:
                active |= models.Q(sender_id=handle_id, status__in=message.PUBLISHED_STATUSES)
            return replies.filter(active).exists()

    def reply_to_comment(
        self, *, body: str, actor: Any, local: Mapping[str, Any] | None = None,
        creation_key: str | None = None,
    ) -> Message:
        """Apply feed reply policy over messaging's idempotent Python-only factory.

        The actor needs readable comment context and channel reply, never feed
        write. None holds for approval indefinitely; positive hours schedule a
        draft; zero queues now. Replays keep the original hold and settlement.
        """

        if actor is None:
            raise PermissionDenied("Comment read access is required.")
        with transaction.atomic():
            parent = type(self).objects.with_actor(actor).lock_if_supported(no_key=True).filter(pk=self.pk).first()
            if parent is None:
                raise PermissionDenied("Comment read access is required.")
            if (parent.is_trashed or parent.direction != parent.Direction.INBOUND
                    or parent.is_original_post or parent.parent_id is None):
                raise ValidationError("Reply requires an inbound comment with an available parent.")
            feed = apps.get_model("posts", "Feed").objects.with_actor(actor).filter(pk=parent.channel_id).first()
            if feed is None or feed.handle_id is None:
                raise ValidationError("The comment's feed has no publishing identity.")
            def admit(comment: Any) -> None:
                if comment.reply_state():
                    raise CommentAnswered("The comment is already answered.")

            return apps.get_model("messaging", "Message").objects.compose_reply(
                parent, body=body, sender=feed.handle, actor=actor, local=local, creation_key=creation_key,
                scheduled_at=feed.reply_schedule(), queued=feed.reply_hold == 0,
                admit=admit,
            )

    def top_level_comment(self) -> Any:
        """Resolve an outbound reply's provider anchor within this feed's parent chain."""

        message = cast("Message", self)
        parent = message.parent
        visited = {self.pk}
        while parent is not None:
            if parent.pk in visited or parent.channel_id != message.channel_id or not parent.external_id:
                raise ValidationError("The comment parent chain is invalid.")
            visited.add(parent.pk)
            ancestor = parent.parent
            if parent.is_original_post or (
                ancestor is not None and ancestor.is_original_post and ancestor.channel_id == message.channel_id
            ):
                return parent
            parent = ancestor
        raise ValidationError("The comment's original post is unavailable.")

    class Meta:
        """Abstract same-row extension composed into ``messaging.Message``."""

        abstract = True
