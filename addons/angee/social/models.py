"""Source models for the social addon — public feeds, engagement, and following.

Social is the public-social surface layered on ``messaging``. It reuses the one
idempotent ``Message.objects.ingest`` write path (a public post *is* a
``messaging.Message`` in a ``messaging.Thread``) and adds the social overlay:

- :class:`Feed` — an ``integrate.Integration`` child + ``Bridge`` (exactly like
  ``messaging.Channel``) that polls an external platform for public posts; its
  ``FeedBackend`` does the transport+parse, and ``sync()`` maps each post onto the
  messaging ingest, then overlays engagement.
- :class:`FeedFollow` — the following / timeline subscription edge.
- :class:`PostMetrics` — rolled-up public engagement counts for a message.
- per-actor social reactions (like / repost / emoji) reuse the single
  ``messaging.Reaction`` table — social writes ``like``/``repost`` as reaction values
  on the shared ``messaging.Message`` rather than owning a parallel table.
- :class:`Quota` — the per-handle, per-platform API-unit ledger feed backends spend.
- :class:`ThreadPublic` / :class:`MessagePublic` — the public-thread fields social
  contributes **onto** ``messaging.Thread`` / ``messaging.Message`` through the
  same-row ``extends`` seam.

The dependency points one way (social → messaging → parties/integrate/storage);
social never edits or forks messaging.
"""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.db import models
from rebac.managers import RebacManager

from angee.base.fields import StateField
from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.integrate.models import Bridge
from angee.parties.models import Handle
from angee.social.backends import FeedBackend, ParsedPost
from angee.social.managers import (
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
    ``social_integrate_*`` addons; ``manual`` is the neutral null-object.

    A *paused* feed carries a NULL ``next_sync_at`` (not scheduled); activating it
    schedules the first poll. ``handle`` is the ``parties.Handle`` the feed monitors
    and posts as (its OAuth token lives on the handle / the integration credential).
    """

    runtime = True
    extends = "integrate.Integration"
    integration_kind_label = "Feed"

    backend_class = ImplClassField(
        base_class=FeedBackend,
        registry_setting="ANGEE_SOCIAL_FEED_BACKEND_CLASSES",
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
        rebac_resource_type = "social/feed"
        rebac_id_attr = "sqid"

    @property
    def backend(self) -> FeedBackend:
        """Return this feed's selected backend, bound to this row."""

        backend_class = cast("type[FeedBackend]", self.resolve_impl("backend_class"))
        return backend_class(self)

    def sync(self) -> int:
        """Fetch new posts, ingest their message core, and overlay engagement.

        The message core (thread/message/parts) is the messaging owner's job, so a
        public post shares email's one idempotent write path; social only writes the
        overlay it owns (public payload / metrics / reactions / social edges). The
        ingest is told the facts a public feed differs on, each set through the
        messaging owner rather than bulk-patched afterward: every thread is born a
        ``PUBLIC_THREAD`` with ``PUBLIC`` visibility, each message lands under the
        ``COMMENT`` kind (a public post, not email), and the RFC-5322 quotation builder
        is skipped (``quote_edges=False``) so a post's short shared text does not mint
        spurious email ``quote`` edges.
        """

        posts = self.backend.fetch_posts()
        message_model = apps.get_model("messaging", "Message")
        thread_model = apps.get_model("messaging", "Thread")
        messages = message_model.objects.ingest(
            [post.message for post in posts],
            channel=self,
            modality=thread_model.Modality.PUBLIC_THREAD,
            visibility=thread_model.Visibility.PUBLIC,
            message_kind=message_model.MessageKind.COMMENT,
            quote_edges=False,
        )
        self._overlay_engagement(posts, messages)
        # last_sync_items reports messages ingested, consistent with Channel.sync.
        return len(messages)

    def _overlay_engagement(self, posts: list[ParsedPost], messages: list) -> None:
        """Attach the social overlay to the message rows the ingest just landed.

        Keys the ``messages`` the owner returned by ``(platform, external_id)`` — no
        re-query, since ``Message.objects.ingest`` hands back the rows it resolved — and
        writes what social owns: the public-post payload it folds onto the shared rows
        (``is_original_post``/``subject_url``/``tags`` via
        :class:`MessagePublic`/:class:`ThreadPublic`), rolled-up :class:`PostMetrics`,
        per-actor reactions on the reused ``messaging.Reaction`` table, and cross-post
        edges through the ``messaging.MessageEdge`` owner (``relate``). The engagement is
        landed in batch: every distinct reactor handle is resolved once and all reactions
        insert through the ``Reaction`` owner in one pass, and cross-post targets are
        pre-keyed from this fetch with a single query for the rest — no per-reaction or
        per-relation round-trip. Runs under the scheduler's ``system_context``, so the
        reads/writes carry system scope through REBAC — not by dropping to the non-REBAC
        ``_base_manager``. The thread shape (born ``PUBLIC_THREAD``/``PUBLIC``) is set at
        ingest time, so no post-hoc bulk update is needed.
        """

        if not posts:
            return
        metrics_model = apps.get_model("social", "PostMetrics")
        # Reactions reuse the single messaging.Reaction table (one per-actor reaction
        # store, not a parallel social one): social writes like/repost as reaction values.
        reaction_model = apps.get_model("messaging", "Reaction")
        edge_model = apps.get_model("messaging", "MessageEdge")
        handle_model = apps.get_model("parties", "Handle")
        owner_id = self.owner_id

        by_key = {(message.platform, message.external_id): message for message in messages}
        landed = [
            (message, post)
            for post in posts
            if (message := by_key.get((post.message.platform, post.message.external_id))) is not None
        ]

        # Payload + rolled-up metrics per landed post (each write is idempotent).
        for message, post in landed:
            self._write_public_payload(message, post)
            if post.metrics is not None:
                metrics_model.objects.upsert(message=message, metrics=post.metrics, owner_id=owner_id)

        # Reactions: resolve every distinct reactor handle once, then land the whole
        # batch through the messaging Reaction owner (one insert, idempotent on the
        # partial unique constraint) rather than a get_or_create per reaction.
        handles = self._resolve_reaction_handles(landed, handle_model, owner_id)
        reaction_model.objects.attribute(
            (
                (message, handles[(reaction.handle.platform, reaction.handle.value)], reaction.reaction)
                for message, post in landed
                for reaction in post.reactions
            ),
            owner_id=owner_id,
        )

        # Cross-post edges: pre-key every target from this fetch, then one query resolves
        # any referenced post not in the batch; the MessageEdge owner writes the edge
        # shape once (idempotent on the (src, dst, kind) key) — social only supplies the kind.
        targets = self._resolve_relation_targets(landed, by_key, channel_id=self.pk)
        for message, post in landed:
            for relation in post.relations:
                target = targets.get((post.message.platform, relation.dst_external_id))
                if target is not None:
                    edge_model.objects.relate(message, target, kind=relation.kind, owner_id=owner_id)

    @staticmethod
    def _resolve_reaction_handles(landed: list, handle_model: Any, owner_id: Any) -> dict:
        """Upsert each distinct reactor handle once, keyed by ``(platform, value)``.

        A post's reactors repeat across the batch, so the handle upsert is deduped to
        one write per distinct reactor through the ``parties.Handle`` owner instead of
        one per reaction.
        """

        specs: dict[tuple[str, str], Any] = {}
        for _message, post in landed:
            for reaction in post.reactions:
                specs.setdefault((reaction.handle.platform, reaction.handle.value), reaction.handle)
        return {
            key: handle_model.objects.upsert(
                platform=parsed.platform,
                value=parsed.value,
                owner_id=owner_id,
                display_name=parsed.display_name,
            )
            for key, parsed in specs.items()
        }

    @staticmethod
    def _resolve_relation_targets(landed: list, by_key: dict, *, channel_id: Any) -> dict:
        """Key every cross-post target by ``(platform, external_id)``.

        Targets already landed in this fetch come from ``by_key``; any post referenced
        but not in the batch is resolved in one indexed ``with_external_ids`` query
        per platform. Message identity is channel-scoped, so the same external id may
        exist once per channel — rows from this feed's own channel win the key, and a
        cross-channel copy is only the fallback.
        """

        message_model = apps.get_model("messaging", "Message")
        targets = dict(by_key)
        missing: dict[str, set[str]] = {}
        for _message, post in landed:
            for relation in post.relations:
                key = (post.message.platform, relation.dst_external_id)
                if key not in targets:
                    missing.setdefault(post.message.platform, set()).add(relation.dst_external_id)
        for platform, external_ids in missing.items():
            rows = list(
                message_model.objects.with_external_ids(sorted(external_ids)).filter(platform=platform)
            )
            # Same-channel rows overwrite cross-channel fallbacks (they sort last).
            for row in sorted(rows, key=lambda row: row.channel_id == channel_id):
                targets[(platform, row.external_id)] = row
        return targets

    @staticmethod
    def _write_public_payload(message: Any, post: ParsedPost) -> None:
        """Fold the parsed public-post payload onto its message/thread rows.

        ``is_original_post`` rides the message and ``subject_url``/``tags`` ride its
        thread — the fields :class:`MessagePublic`/:class:`ThreadPublic` contribute onto
        the single messaging tables. Each row is written only when a value actually
        changes, so an idempotent re-sync stays a no-op.
        """

        if message.is_original_post != post.is_original_post:
            message.is_original_post = post.is_original_post
            message.save(update_fields=("is_original_post", "updated_at"))
        thread = message.thread
        if thread is None:
            return
        subject_url = post.subject_url or ""
        tags = list(post.tags)
        if thread.subject_url != subject_url or thread.tags != tags:
            thread.subject_url = subject_url
            thread.tags = tags
            thread.save(update_fields=("subject_url", "tags", "updated_at"))


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
        "social.Feed",
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
        rebac_resource_type = "social/feed_follow"
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
        rebac_resource_type = "social/post_metrics"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a readable metrics label for Django displays."""

        return f"metrics:{self.message_id}"


class Quota(SqidMixin, AuditMixin, AngeeModel):
    """A per-handle, per-platform API-unit ledger for one billing period.

    Feed backends spend platform API units (search, list, insert) against a per-period
    budget; :meth:`~angee.social.managers.QuotaManager.consume` atomically debits this
    ledger and refuses when the budget is insufficient. Enforcement is cooperative —
    the backend must ask before it spends.
    """

    runtime = True
    sqid_prefix = "qta_"

    handle = models.ForeignKey(
        "parties.Handle",
        on_delete=models.CASCADE,
        related_name="quotas",
    )
    # No default: platform is part of the (handle, platform, period_start) identity, so
    # a ledger is always opened for an explicit platform, never a defaulted one.
    platform = StateField(choices_enum=Handle.Platform)
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
        rebac_resource_type = "social/quota"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("handle", "platform", "period_start"),
                name="uq_quota_handle_platform_period",
            ),
        )

    def __str__(self) -> str:
        """Return a readable quota label for Django displays."""

        return f"{self.handle_id}/{self.platform}: {self.quota_used}/{self.quota_limit}"


# --- Same-row extensions onto messaging (the public-post payload) ---------------
#
# These fold the payload-only public-post columns into the SINGLE messaging.Thread /
# messaging.Message tables via Angee's same-row ``extends`` seam (abstract +
# ``extends``, NO ``runtime`` — like ``iam_integrate_oidc.OAuthClientOidc``). Only
# fields with no base producer are extended here: ``modality``/``visibility`` STAY
# owned by ``messaging`` (its ``ThreadManager.resolve`` writes both on every thread,
# and its schema/console bind them), so social sets ``modality=public_thread`` /
# ``visibility=public`` through that owner rather than re-owning the columns. The base
# ``messaging`` slice carries no field of these names, so the composer folds these onto
# the one table with no collision.


class ThreadPublic(AngeeModel):
    """Public-post payload fields social contributes onto ``messaging.Thread`` (same row).

    A public thread's row *is* its subject post: ``subject_url``/``body``/``tags`` carry
    the post payload and ``parent`` nests a thread under another (a reply/quote thread).
    These have no producer in base messaging, so social owns them; the structural
    ``modality``/``visibility`` discriminators stay owned by messaging.
    """

    extends = "messaging.Thread"

    parent = models.ForeignKey(
        "messaging.Thread",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    body = models.TextField(blank=True, default="")
    subject_url = models.URLField(max_length=1024, blank=True, default="")
    tags = models.JSONField(blank=True, default=list)

    class Meta:
        """Abstract same-row extension composed into ``messaging.Thread``."""

        abstract = True


class MessagePublic(AngeeModel):
    """Public-post fields social contributes onto ``messaging.Message`` (same row).

    ``is_original_post`` marks the root post of a public thread (a post with no parent).
    It has no producer in base messaging, so social owns it and the composer folds it
    onto the single ``messaging.Message`` table.
    """

    extends = "messaging.Message"

    is_original_post = models.BooleanField(default=False)

    class Meta:
        """Abstract same-row extension composed into ``messaging.Message``."""

        abstract = True
