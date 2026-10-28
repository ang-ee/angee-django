"""The ``timeline`` read: one ordered page of a record's events through a SQL union."""

from __future__ import annotations

from typing import Any

import strawberry
from strawberry_django.auth.utils import get_current_user
from strawberry_django.utils.requests import get_request
from django.apps import apps
from django.core.exceptions import ValidationError
from django.http import HttpRequest

from angee.graphql.ids import PublicID, require_instance_for_id

from angee.base.pagination import InvalidKeysetCursor
from angee.timeline.reading import Entry, KindCount, KindSummary, Page, Timeline

PAGE_LIMIT_DEFAULT = 25
PAGE_LIMIT_MAX = 100


TimelineKindCount = strawberry.type(KindCount, name="TimelineKindCount")
TimelineKindType = strawberry.type(KindSummary, name="TimelineKindType")
TimelineEntryType = strawberry.type(Entry, name="TimelineEntryType")
TimelinePageType = strawberry.type(Page, name="TimelinePageType")


def _timeline(info: strawberry.Info, model: str, id: PublicID) -> Timeline:
    request = get_request(info)
    actor = get_current_user(info)
    cache = request.__dict__.setdefault("_timeline_reads", {}) if isinstance(request, HttpRequest) else None
    key = (model, str(id))
    if cache is not None and key in cache:
        return cache[key]
    try:
        record_model = apps.get_model(model)
    except (LookupError, ValueError) as error:
        raise ValidationError(f"Unknown model {model!r}.", code="invalid") from error
    record = require_instance_for_id(
        record_model, str(id), queryset=record_model.objects.with_actor(actor),
    )
    timeline = Timeline(record, actor)
    if cache is not None:
        cache[key] = timeline
    return timeline


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
            return _timeline(info, model, id).page(kinds=kinds, needs_me=needs_me, before=before, limit=limit)
        except InvalidKeysetCursor as error:
            raise ValidationError(str(error), code="invalid") from error

    @strawberry.field
    def timeline_attention(self, info: strawberry.Info, model: str, id: PublicID) -> int:
        """How many lines on the record wait on the viewer."""

        return _timeline(info, model, id).attention()


schemas: dict[str, dict[str, list[Any]]] = {"console": {"query": [TimelineQuery]}}
