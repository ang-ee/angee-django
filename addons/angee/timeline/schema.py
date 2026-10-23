"""The ``timeline`` read: one ordered page of a record's events through a SQL union."""

from __future__ import annotations

from typing import Any

import strawberry
from django.apps import apps
from django.core.exceptions import ValidationError

from angee.graphql.ids import PublicID, require_instance_for_id
from angee.iam.permissions import request_from_info
from angee.timeline.kinds import Cursor
from angee.timeline.reading import Entry, KindCount, KindSummary, Page, Timeline

PAGE_LIMIT_DEFAULT = 25
PAGE_LIMIT_MAX = 100


TimelineKindCount = strawberry.type(KindCount, name="TimelineKindCount")
TimelineKindType = strawberry.type(KindSummary, name="TimelineKindType")
TimelineEntryType = strawberry.type(Entry, name="TimelineEntryType")
TimelinePageType = strawberry.type(Page, name="TimelinePageType")


def _timeline(info: strawberry.Info, model: str, id: PublicID) -> Timeline:
    request = request_from_info(info)
    cache = request.__dict__.setdefault("_timeline_reads", {})
    key = (model, str(id))
    if key not in cache:
        try:
            record_model = apps.get_model(model)
        except (LookupError, ValueError) as error:
            raise ValidationError(f"Unknown model {model!r}.", code="invalid") from error
        record = require_instance_for_id(
            record_model, str(id), queryset=record_model.objects.with_actor(request.user),
        )
        cache[key] = Timeline(record, request.user)
    return cache[key]


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
    ) -> Page:
        """One page of the record's events, newest first, across the selected kinds."""

        limit = max(1, min(int(limit), PAGE_LIMIT_MAX))
        try:
            cursor = Cursor.decode(before) if before else None
        except ValueError as error:
            raise ValidationError(str(error), code="invalid") from error
        return _timeline(info, model, id).page(kinds=kinds, needs_me=needs_me, cursor=cursor, limit=limit)

    @strawberry.field
    def timeline_attention(self, info: strawberry.Info, model: str, id: PublicID) -> int:
        """How many lines on the record wait on the viewer."""

        return _timeline(info, model, id).attention()


schemas: dict[str, dict[str, list[Any]]] = {"console": {"query": [TimelineQuery]}}
