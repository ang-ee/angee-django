"""Source models for the posts addon — public feeds, engagement, and following.

Posts is the public-post surface layered on ``messaging``. It reuses the one
idempotent ``Message.objects.ingest`` write path (a public post *is* a
``messaging.Message`` in a ``messaging.Thread``) and adds the posts overlay:

- :class:`Feed` — an ``integrate.Integration`` child + ``Bridge`` (exactly like
  ``messaging.Channel``) that polls an external platform for public posts; its
  ``FeedBackend`` does the transport+parse, and ``sync()`` maps each post onto the
  messaging ingest, then overlays engagement.
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

from typing import cast

from django.db import models
from rebac.managers import RebacManager

from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.integrate.models import Bridge
from angee.posts.backends import FeedBackend
from angee.posts.ingest import land_posts
from angee.posts.managers import (
    FeedFollowManager,
    PostMetricsManager,
    QuotaManager,
)


class Feed(Bridge):
    """A connected public-content source that polls an external platform for posts.

    An ``integrate.Integration`` child (identity / credential / status / owner from
    the connection substrate) and a ``Bridge`` (the scheduler + ``run_sync`` drive it
    through ``sync``; ``integrate.scheduler.run_due_bridges`` auto-discovers any
    concrete ``Bridge`` subclass, so no registration is needed). ``backend_class``
    selects the platform — ``youtube`` / ``facebook`` are contributed by downstream
    ``posts_integrate_*`` addons; ``manual`` is the neutral null-object.

    A *paused* feed carries a NULL ``next_sync_at`` (not scheduled); activating it
    schedules the first poll. ``handle`` is the ``parties.Handle`` the feed monitors
    and posts as (its OAuth token lives on the handle / the integration credential).
    """

    runtime = True
    extends = "integrate.Integration"
    integration_kind_label = "Feed"

    backend_class = ImplClassField(
        base_class=FeedBackend,
        registry_setting="ANGEE_POSTS_FEED_BACKEND_CLASSES",
        default="manual",
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

    objects = RebacManager()

    class Meta:
        """Django model options for the feed child model."""

        abstract = True
        rebac_resource_type = "posts/feed"
        rebac_id_attr = "sqid"

    @property
    def backend(self) -> FeedBackend:
        """Return this feed's selected backend, bound to this row."""

        backend_class = cast("type[FeedBackend]", self.resolve_impl("backend_class"))
        return backend_class(self)

    def sync(self) -> int:
        """Fetch new posts, ingest their message core, and overlay engagement.

        The message core (thread/message/parts) is the messaging owner's job, so a
        public post shares email's one idempotent write path; posts only writes the
        overlay it owns (public payload / metrics / reactions / post edges). The
        ingest is told the structural facts a public feed differs on: every thread
        is born a ``PUBLIC_THREAD`` with ``PUBLIC`` visibility — the ingest owner
        derives the ``COMMENT`` kind from that shape — and the RFC-5322 quotation
        builder is skipped (``quote_edges=False``) so a post's short shared text
        does not mint spurious email ``quote`` edges.
        """

        posts = self.backend.fetch_posts()
        # last_sync_items reports messages ingested, consistent with Channel.sync.
        return len(land_posts(self, posts, owner_id=self.owner_id))


class FeedFollow(SqidMixin, AuditMixin, AngeeModel):
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
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("feed", "handle"),
                name="uq_feed_follow_feed_handle",
            ),
        )

    def __str__(self) -> str:
        """Return a readable follow label for Django displays."""

        return f"{self.handle_id} → {self.feed_id}"


class PostMetrics(SqidMixin, AuditMixin, AngeeModel):
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
        rebac_resource_type = "posts/post_metrics"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a readable metrics label for Django displays."""

        return f"metrics:{self.message_id}"


class Quota(SqidMixin, AuditMixin, AngeeModel):
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
        rebac_id_attr = "sqid"
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


class ThreadPublic(AngeeModel):
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


class MessagePublic(AngeeModel):
    """Public-post fields posts contributes onto ``messaging.Message`` (same row).

    ``is_original_post`` marks the root post of a public thread (a post with no parent).
    It has no producer in base messaging, so posts owns it and the composer folds it
    onto the single ``messaging.Message`` table.
    """

    extends = "messaging.Message"

    is_original_post = models.BooleanField(default=False)

    class Meta:
        """Abstract same-row extension composed into ``messaging.Message``."""

        abstract = True
