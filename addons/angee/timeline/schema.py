"""The ``timeline`` read: one ordered page of a record's events across every source."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import strawberry
from django.apps import apps
from django.core.exceptions import ValidationError

from angee.graphql.ids import PublicID, require_instance_for_id
from angee.iam.permissions import request_from_info
from angee.timeline.sources import Cursor, TimelineEntry, timeline_sources

PAGE_LIMIT_DEFAULT = 25
PAGE_LIMIT_MAX = 100


@strawberry.type
class TimelineEntryType:
    """One line: what happened, who, when, and where its body lives."""

    id: str
    at: datetime
    kind: str
    title: str
    actor: str
    detail: str
    needs_me: bool
    target_model: str | None
    target_id: PublicID | None
    group_id: str | None


@strawberry.type
class TimelineKindCountType:
    kind: str
    count: int


@strawberry.type
class TimelinePageType:
    entries: list[TimelineEntryType]
    """Newest first."""
    kinds: list[str]
    """Every kind this record can carry, in source order; the scope chips."""
    counts: list[TimelineKindCountType]
    has_more: bool
    next_before: str | None


def _entry_type(entry: TimelineEntry) -> TimelineEntryType:
    return TimelineEntryType(
        id=entry.id, at=entry.at, kind=entry.kind, title=entry.title, actor=entry.actor, detail=entry.detail,
        needs_me=entry.needs_me, target_model=entry.target_model,
        target_id=PublicID(entry.target_id) if entry.target_id else None, group_id=entry.group_id,
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
        rows: list[TimelineEntry] = []
        for source in selected:
            rows.extend(source.entries(record, actor, before=cursor, limit=limit + 1))
        if cursor is not None:
            rows = [row for row in rows if cursor.admits(row)]
        rows.sort(key=lambda row: row.order_key, reverse=True)
        page = rows[:limit]
        last = page[-1] if page else None
        return TimelinePageType(
            entries=[_entry_type(row) for row in page],
            kinds=[source.kind for source in eligible],
            counts=[TimelineKindCountType(kind=source.kind, count=source.count(record, actor)) for source in eligible],
            has_more=len(rows) > limit,
            next_before=Cursor(at=last.at, id=last.id).encode() if last is not None and len(rows) > limit else None,
        )


schemas: dict[str, dict[str, list[Any]]] = {"console": {"query": [TimelineQuery]}}
