"""Posts factories, public-post landing, engagement writes, following scopes, and API quota accounting."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, time, timedelta
from itertools import batched
from typing import TYPE_CHECKING, Any, Self
from zoneinfo import ZoneInfo

from django.apps import apps
from django.db import models, transaction
from django.utils import timezone

from angee.base.managers import AngeeManager
from angee.base.querysets import AngeeQuerySet
from angee.messaging.managers import MessageManager

if TYPE_CHECKING:
    from angee.messaging.models import Message
    from angee.posts.backends import ParsedPost
    from angee.posts.models import FeedFollow, PostMetrics, Quota


class MessagePostsManager[MessageT: Message](MessageManager[MessageT]):
    """Posts' message manager, composed onto ``messaging.Message`` as ``Message.posts``.

    It lands parsed public posts through messaging's one ingest owner and applies the
    overlay posts owns: payload, metrics, reactions and cross-post relations.
    """

    def land(
        self,
        channel: Any,
        posts: list[ParsedPost],
        *,
        owner_id: Any,
        historical: bool = False,
        relations: bool = True,
    ) -> list[Any]:
        """Land public posts through messaging, then apply the posts-owned overlay.

        Message/thread/part persistence stays on ``Message.objects.ingest``. This
        owner supplies the public structural facts that make ingest derive COMMENT,
        disables email quotation edges, then writes payload, metrics, reactions, and
        cross-post relations exactly once through their existing owners. Pages land
        in transactional chunks; ``relations=False`` leaves cross-post relations to a
        later :meth:`land_relations` once the whole page is visible.
        """

        thread_model = self.model.thread.field.related_model
        messages: list[Any] = []
        # Overlay and moderation must exist before the one messaging event owner runs.
        for chunk in batched(posts, 200):
            by_id = {post.message.external_id: post for post in chunk}

            def overlay(message: Any) -> None:
                post = by_id[message.external_id]
                self._overlay_engagement([post], [message], owner_id=owner_id)
                message.apply_provider_visibility(hidden=post.hidden)

            with transaction.atomic():
                messages.extend(
                    self.ingest(
                        [
                            replace(post.message, metadata={**post.message.metadata, "tags": list(post.tags)})
                            for post in chunk
                        ],
                        channel=channel,
                        created_by_id=owner_id,
                        modality=thread_model.Modality.PUBLIC_THREAD,
                        visibility=thread_model.Visibility.PUBLIC,
                        quote_edges=False,
                        historical=historical,
                        after_landing=overlay,
                    )
                )
        if relations:
            self.land_relations(channel, posts, messages, owner_id=owner_id)
        return messages

    def land_relations(self, channel: Any, posts: Sequence[ParsedPost], messages: list[Any], *, owner_id: Any) -> None:
        """Finish successfully landed cross-post relations once a page is visible."""

        landed, by_key = self._pair_landed(posts, messages)
        targets = self._relation_targets(landed, by_key, channel_id=channel.pk)
        edge_model = apps.get_model("messaging", "MessageEdge")
        for message, post in landed:
            for relation in post.relations:
                target = targets.get((post.message.platform, relation.dst_external_id))
                if target is not None:
                    edge_model.objects.relate(message, target, kind=relation.kind, created_by_id=owner_id)

    @classmethod
    def _overlay_engagement(cls, posts: list[ParsedPost], messages: list[Any], *, owner_id: Any) -> None:
        """Attach public payload and engagement to the rows messaging returned."""

        if not posts:
            return
        landed, _ = cls._pair_landed(posts, messages)
        for message, post in landed:
            message.apply_public_post(post)
            if post.metrics is not None:
                apps.get_model("posts", "PostMetrics").objects.upsert(
                    message=message,
                    metrics=post.metrics,
                    created_by_id=owner_id,
                )
        handles = cls._reaction_handles(landed, owner_id)
        apps.get_model("messaging", "Reaction").objects.attribute(
            (
                (message, handles[(reaction.handle.platform, reaction.handle.value)], reaction.reaction)
                for message, post in landed
                for reaction in post.reactions
            ),
            created_by_id=owner_id,
        )

    @staticmethod
    def _pair_landed(posts: Sequence[ParsedPost], messages: list[Any]) -> tuple[list[Any], dict[Any, Any]]:
        """Pair each post with the message messaging landed for it, keyed by external identity."""

        by_key = {(message.platform, message.external_id): message for message in messages}
        landed = [
            (message, post)
            for post in posts
            if (message := by_key.get((post.message.platform, post.message.external_id))) is not None
        ]
        return landed, by_key

    @staticmethod
    def _reaction_handles(landed: list[Any], owner_id: Any) -> dict[tuple[str, str], Any]:
        """Upsert each distinct reactor handle once."""

        specs: dict[tuple[str, str], Any] = {}
        for _message, post in landed:
            for reaction in post.reactions:
                specs.setdefault((reaction.handle.platform, reaction.handle.value), reaction.handle)
        handle_model = apps.get_model("parties", "Handle")
        return {
            key: handle_model.objects.upsert(
                platform=parsed.platform,
                value=parsed.value,
                created_by_id=owner_id,
                display_name=parsed.display_name,
                external_id=parsed.external_id,
                metadata=parsed.metadata,
            )
            for key, parsed in specs.items()
        }

    def _relation_targets(self, landed: list[Any], by_key: dict[Any, Any], *, channel_id: Any) -> dict[Any, Any]:
        """Key every cross-post target by platform and external id; a copy on ``channel_id`` wins."""

        targets = dict(by_key)
        missing: dict[str, set[str]] = {}
        for _message, post in landed:
            for relation in post.relations:
                key = (post.message.platform, relation.dst_external_id)
                if key not in targets:
                    missing.setdefault(post.message.platform, set()).add(relation.dst_external_id)
        for platform, external_ids in missing.items():
            rows = list(self.with_external_ids(sorted(external_ids)).filter(platform=platform))
            for row in sorted(rows, key=lambda row: row.channel_id == channel_id):
                targets[(platform, row.external_id)] = row
        return targets


class FeedFollowQuerySet[FollowT: FeedFollow](AngeeQuerySet[FollowT]):
    """REBAC-scoped read scopes for the following/timeline edge."""

    def active(self) -> Self:
        """Return open follows (never ended)."""

        return self.filter(ended_at__isnull=True)

    def for_feed(self, feed: Any) -> Self:
        """Return the follows subscribed to a given feed."""

        return self.filter(feed=feed)


_FeedFollowManagerBase = AngeeManager.from_queryset(FeedFollowQuerySet)


class FeedFollowManager[FollowT: FeedFollow](_FeedFollowManagerBase[FollowT]):
    """Owns the follow/unfollow writes over the ``(feed, handle)`` subscription."""

    def follow(self, *, feed: Any, handle: Any) -> FollowT:
        """Open (or re-open) the follow of ``feed`` by ``handle``; idempotent."""

        follow, created = self.get_or_create(
            feed_id=feed.pk,
            handle_id=handle.pk,
            defaults={"started_at": timezone.now()},
        )
        if not created and follow.ended_at is not None:
            follow.ended_at = None
            follow.started_at = timezone.now()
            follow.save(update_fields=["ended_at", "started_at", "updated_at"])
        return follow

    def unfollow(self, *, feed: Any, handle: Any) -> int:
        """Close the open follow of ``feed`` by ``handle``; returns rows closed."""

        return self.filter(feed=feed, handle=handle, ended_at__isnull=True).update(
            ended_at=timezone.now(),
        )


class PostMetricsManager[MetricsT: PostMetrics](AngeeManager[MetricsT]):
    """Owns the one-to-one engagement-counter upsert for a message."""

    def reply_counts(self, *, channel: Any, platform: str, external_ids: Sequence[str]) -> dict[str, int]:
        """Return the known reply count of each selected source message on ``channel``, in one query.

        A message without metrics stays absent, so a provider can tell an unseen
        post from a measured zero.
        """

        return dict(
            self.filter(
                message__channel_id=channel.pk,
                message__platform=platform,
                message__external_id__in=external_ids,
            ).values_list("message__external_id", "reply_count")
        )

    def upsert(self, *, message: Any, metrics: Any, created_by_id: Any = None) -> MetricsT:
        """Write the rolled-up engagement counters for ``message`` (idempotent).

        ``metrics`` is a :class:`~angee.posts.backends.ParsedMetrics`. Counters are a
        platform-reported snapshot, so the latest fetch overwrites them.
        ``created_by_id`` attributes the insert only: the row's ``creator`` keeps it.
        """

        counters = {
            "view_count": metrics.view_count,
            "like_count": metrics.like_count,
            "repost_count": metrics.repost_count,
            "quote_count": metrics.quote_count,
            "reply_count": metrics.reply_count,
            "bookmark_count": metrics.bookmark_count,
            "metadata": metrics.metadata,
        }
        row, _created = self.update_or_create(
            message_id=message.pk,
            defaults=counters,
            create_defaults={**counters, "created_by_id": created_by_id},
        )
        return row


class QuotaManager[QuotaT: Quota](AngeeManager[QuotaT]):
    """Owns the API-unit ledger: opening a period and atomic consumption.

    A backend calls :meth:`consume` before spending platform API units; enforcement is
    advisory (the caller must ask). Cost tables / safety margins live with the source
    backend that knows its API, not here.
    """

    def open_period(
        self,
        *,
        integration: Any,
        now: datetime | None = None,
        scope: str,
        reset_timezone: str = "UTC",
    ) -> QuotaT:
        """Open the backend's daily scope at midnight in its declared reset timezone.

        A scope must include the provider namespace and its true budget identity
        (for a project-wide allowance, the OAuth client rather than the feed).
        The integration FK retains an authorization anchor, not the budget key.
        The row stores usage only; each ``consume`` check brings its own limit.
        """

        if not scope.strip():
            raise ValueError("Quota needs a nonblank scope.")
        moment = (now or timezone.now()).astimezone(ZoneInfo(reset_timezone))
        period_start = datetime.combine(moment.date(), time.min, tzinfo=moment.tzinfo)
        row, _created = self.get_or_create(
            scope=scope,
            period_start=period_start,
            defaults={"integration_id": integration.pk, "period_end": period_start + timedelta(days=1)},
        )
        return row

    def consume(
        self,
        *,
        period: Any,
        units: int,
        limit: int,
        now: datetime | None = None,
        reserve: int = 0,
    ) -> bool:
        """Atomically debit an already opened period; ``False`` if it would exceed.

        Bumps ``quota_used`` with an ``F()`` delta under a row lock so concurrent
        spenders never lose an increment. Refusal leaves usage untouched while
        retaining usage only. Limits belong to each check, never to the shared row.
        """

        if units < 0 or not 0 <= reserve < limit:
            raise ValueError("Quota units must be nonnegative and reserve smaller than limit.")
        moment = now or timezone.now()
        with transaction.atomic():
            locked = self.locked_get(pk=period.pk)
            ceiling = limit - reserve
            if locked.quota_used + units > ceiling:
                return False
            consumed = self.filter(pk=locked.pk, quota_used__lte=ceiling - units).update(
                quota_used=models.F("quota_used") + units,
                last_updated=moment,
            )
        return consumed == 1
