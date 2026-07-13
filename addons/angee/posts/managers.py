"""Managers that own the posts write path and its chainable read scopes.

Following the repo's Manager/QuerySet canon (``storage.FileQuerySet``/``FileManager``
via ``from_queryset``; the very split the messaging ORM review flagged as **H3**):
read predicates are chainable scopes on a ``*QuerySet``; the managers own the
writes. The feed-ingest overlay reuses ``messaging.Message.objects.ingest`` for the
message core, and per-actor reactions reuse the single ``messaging.Reaction`` table,
so these managers own only the remaining posts layer — engagement counts, following,
and the per-integration API quota ledger.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

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

    def follow(self, *, feed: Any, handle: Any, owner_id: Any = None) -> Any:
        """Open (or re-open) the follow of ``feed`` by ``handle``; idempotent."""

        follow, created = self.get_or_create(
            feed=feed,
            handle=handle,
            defaults={"started_at": timezone.now(), "created_by_id": owner_id},
        )
        if not created and follow.ended_at is not None:
            follow.ended_at = None
            follow.started_at = timezone.now()
            follow.save(update_fields=["ended_at", "started_at", "updated_at"])
        return follow

    def unfollow(self, *, feed: Any, handle: Any) -> int:
        """Close the open follow of ``feed`` by ``handle``; returns rows closed."""

        return self.filter(feed=feed, handle=handle, ended_at__isnull=True).update(ended_at=timezone.now())


class PostMetricsManager(AngeeManager):
    """Owns the one-to-one engagement-counter upsert for a message."""

    def upsert(self, *, message: Any, metrics: Any, owner_id: Any = None) -> Any:
        """Write the rolled-up engagement counters for ``message`` (idempotent).

        ``metrics`` is a :class:`~angee.posts.backends.ParsedMetrics`. Counters are a
        platform-reported snapshot, so the latest fetch overwrites the row — no
        ``F()`` delta (unlike thread counters, which the ingest owner increments).
        """

        row, _created = self.update_or_create(
            message=message,
            defaults={
                "view_count": metrics.view_count,
                "like_count": metrics.like_count,
                "repost_count": metrics.repost_count,
                "quote_count": metrics.quote_count,
                "reply_count": metrics.reply_count,
                "bookmark_count": metrics.bookmark_count,
                "metadata": metrics.metadata,
                "created_by_id": owner_id,
            },
        )
        return row


class QuotaQuerySet(AngeeQuerySet[Any]):
    """REBAC-scoped read scopes for the per-integration API budget."""

    def for_integration(self, integration: Any) -> QuotaQuerySet:
        """Return the quota ledgers of one credentialed integration."""

        return cast(QuotaQuerySet, self.filter(integration=integration))

    def current(self, *, now: datetime) -> QuotaQuerySet:
        """Return the ledger rows whose period contains ``now``."""

        return cast(
            QuotaQuerySet,
            self.filter(period_start__lte=now, period_end__gt=now),
        )


class QuotaManager(RebacManager.from_queryset(QuotaQuerySet)):  # type: ignore[misc]
    """Owns the API-unit ledger: opening a period and atomic consumption.

    A backend calls :meth:`consume` before spending platform API units; enforcement is
    advisory (the caller must ask). Cost tables / safety margins live with the source
    backend that knows its API, not here.
    """

    _DEFAULT_WINDOW = timedelta(days=1)

    def open_period(
        self,
        *,
        integration: Any,
        limit: int,
        now: datetime | None = None,
        window: timedelta | None = None,
    ) -> Any:
        """Return the current ledger row for ``integration``, opening one if due."""

        moment = now or timezone.now()
        span = window or self._DEFAULT_WINDOW
        epoch = datetime(1970, 1, 1, tzinfo=moment.tzinfo)
        period_start = epoch + ((moment - epoch) // span) * span
        row, _created = self.get_or_create(
            integration=integration,
            period_start=period_start,
            defaults={"period_end": period_start + span, "quota_limit": limit},
        )
        return row

    def consume(
        self,
        *,
        integration: Any,
        units: int,
        limit: int,
        now: datetime | None = None,
    ) -> bool:
        """Atomically consume ``units`` from the current period; ``False`` if it would exceed.

        Bumps ``quota_used`` with an ``F()`` delta under a row lock so concurrent
        spenders never lose an increment, and refuses (leaving the ledger untouched)
        when the budget is insufficient.
        """

        moment = now or timezone.now()
        with transaction.atomic():
            period = self.open_period(integration=integration, limit=limit, now=moment)
            locked = self.locked_get(pk=period.pk)
            if locked.quota_used + units > locked.quota_limit:
                return False
            self.filter(pk=locked.pk).update(
                quota_used=models.F("quota_used") + units,
                last_updated=moment,
            )
        return True
