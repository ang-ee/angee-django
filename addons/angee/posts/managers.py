"""Posts factories, engagement writes, following scopes, and API quota accounting."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from django.db import models, transaction
from django.utils import timezone
from rebac.managers import RebacManager

from angee.base.models import AngeeManager, AngeeQuerySet


class FeedFollowQuerySet(AngeeQuerySet[Any]):
    """REBAC-scoped read scopes for the following/timeline edge."""

    def active(self) -> FeedFollowQuerySet:
        """Return open follows (never ended)."""

        return cast(FeedFollowQuerySet, self.filter(ended_at__isnull=True))

    def for_handle(self, handle: Any) -> FeedFollowQuerySet:
        """Return the follows a given handle subscribes through."""

        return cast(FeedFollowQuerySet, self.filter(handle=handle))

    def for_feed(self, feed: Any) -> FeedFollowQuerySet:
        """Return the follows subscribed to a given feed."""

        return cast(FeedFollowQuerySet, self.filter(feed=feed))


class FeedFollowManager(RebacManager.from_queryset(FeedFollowQuerySet)):  # type: ignore[misc]
    """Owns the follow/unfollow writes over the ``(feed, handle)`` subscription."""

    def follow(self, *, feed: Any, handle: Any) -> Any:
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


class PostMetricsManager(AngeeManager):
    """Owns the one-to-one engagement-counter upsert for a message."""

    def upsert(self, *, message: Any, metrics: Any, created_by_id: Any = None) -> Any:
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


class QuotaManager(AngeeManager):
    """Owns the API-unit ledger: opening a period and atomic consumption.

    A backend calls :meth:`consume` before spending platform API units; enforcement is
    advisory (the caller must ask). Cost tables / safety margins live with the source
    backend that knows its API, not here.
    """

    def open_period(
        self, *, integration: Any, now: datetime | None = None, scope: str, reset_timezone: str = "UTC",
    ) -> Any:
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
        self, *, period: Any, units: int, limit: int, now: datetime | None = None, reserve: int = 0,
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
