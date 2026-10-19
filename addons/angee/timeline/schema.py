"""The ``timeline`` read: one ordered page of a record's events across every source."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import strawberry
from django.apps import apps
from django.core.exceptions import ValidationError

from angee.graphql.ids import PublicID, require_instance_for_id
from angee.iam.permissions import request_from_info
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource, timeline_sources

PAGE_LIMIT_DEFAULT = 25
PAGE_LIMIT_MAX = 100

log = logging.getLogger(__name__)


@strawberry.type
class TimelineEntryType:
    """One line: what happened, who, when, and where its body lives."""

    key: str
    at: datetime
    kind: str
    title: str
    actor: str
    detail: str
    needs_me: bool
    target_model: str | None
    target_id: PublicID | None
    folded: str


@strawberry.type
class TimelineKindType:
    """A kind this record can carry, in sequence order; its presentation is the kind's ``timeline#kinds`` child."""

    id: str
    count: int | None
    """None when the kind's source failed on this read."""
    models: list[str]


@strawberry.type
class TimelinePageType:
    entries: list[TimelineEntryType]
    """Newest first."""
    kinds: list[TimelineKindType]
    failed: list[str]
    """Kinds whose source raised; their lines are missing from this page."""
    has_more: bool
    next_before: str | None


def _entry_type(entry: TimelineEntry) -> TimelineEntryType:
    return TimelineEntryType(
        key=entry.key, at=entry.at, kind=entry.kind, title=entry.title, actor=entry.actor, detail=entry.detail,
        needs_me=entry.needs_me, target_model=entry.target_model,
        target_id=PublicID(entry.target_id) if entry.target_id else None, folded=entry.folded,
    )


@strawberry.type
class TimelineQuery:
    @strawberry.field
    def timeline(
        self,
        info: strawberry.Info,
        model: str,
        id: PublicID,
        kinds: list[str] | None = None,
        needs_me: bool = False,
        before: str | None = None,
        limit: int = PAGE_LIMIT_DEFAULT,
    ) -> TimelinePageType:
        """One page of the record's events, newest first, across the selected kinds."""

        actor = request_from_info(info).user
        try:
            record_model = apps.get_model(model)
        except (LookupError, ValueError) as error:
            raise ValidationError(f"Unknown model {model!r}.", code="invalid") from error
        record = require_instance_for_id(record_model, str(id), queryset=record_model.objects.with_actor(actor))
        limit = max(1, min(int(limit), PAGE_LIMIT_MAX))
        try:
            cursor = Cursor.decode(before) if before else None
        except ValueError as error:
            raise ValidationError(str(error), code="invalid") from error

        eligible = [source for source in timeline_sources() if source.eligible(record_model)]
        selected = [source for source in eligible if not kinds or source.kind in kinds]
        failed: list[str] = []
        rows: list[TimelineEntry] = []
        for source in selected:
            try:
                rows.extend(source.entries(record, actor, before=cursor, limit=limit + 1, needs_me=needs_me))
            except Exception:  # noqa: BLE001 - one failing source must not take the page down
                log.exception("timeline source %s failed on %s", source.kind, record)
                failed.append(source.kind)
        if cursor is not None:
            rows = [row for row in rows if cursor.admits(row)]
        rows.sort(key=lambda row: row.order_key)
        page = rows[:limit]
        return TimelinePageType(
            entries=[_entry_type(row) for row in page],
            kinds=[_kind_type(source, record, actor, failed) for source in eligible],
            failed=failed,
            has_more=len(rows) > limit,
            next_before=Cursor.after(page[-1]).encode() if page and len(rows) > limit else None,
        )


def _kind_type(source: TimelineSource, record: Any, actor: Any, failed: list[str]) -> TimelineKindType:
    count: int | None
    if source.kind in failed:
        count = None
    else:
        try:
            count = int(source.count(record, actor))
        except Exception:  # noqa: BLE001
            log.exception("timeline source %s count failed on %s", source.kind, record)
            failed.append(source.kind)
            count = None
    return TimelineKindType(id=source.kind, count=count, models=list(source.models))


schemas: dict[str, dict[str, list[Any]]] = {"console": {"query": [TimelineQuery]}}
