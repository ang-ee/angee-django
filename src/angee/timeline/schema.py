"""The ``timeline`` read: one ordered page of a record's events through a SQL union."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

import strawberry
from channels.db import database_sync_to_async
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.http import HttpRequest
from rebac import current_actor
from strawberry_django.auth.utils import get_current_user
from strawberry_django.utils.requests import get_request

from angee.base.pagination import InvalidKeysetCursor, clamp_page_size
from angee.base.refs import RecordRef
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.subscriptions import subscribe_group
from angee.timeline.events import timeline_group
from angee.timeline.reading import Entry, KindCount, KindSummary, Page, Timeline

PAGE_LIMIT_DEFAULT = 25


TimelineKindCount = strawberry.type(KindCount, name="TimelineKindCount")
TimelineKindType = strawberry.type(KindSummary, name="TimelineKindType")
TimelineEntryType = strawberry.type(Entry, name="TimelineEntryType")
TimelinePageType = strawberry.type(Page, name="TimelinePageType")


def _timeline_record(model: str, id: PublicID, actor: Any) -> models.Model:
    """The read and subscription share exactly the same record visibility."""

    try:
        record_model = apps.get_model(model)
    except (LookupError, ValueError) as error:
        raise ValidationError(f"Unknown model {model!r}.", code="invalid") from error
    return require_instance_for_id(
        record_model, str(id), queryset=record_model.objects.with_actor(actor),
    )


def _timeline(info: strawberry.Info, model: str, id: PublicID) -> Timeline:
    request = get_request(info)
    actor = get_current_user(info)
    cache = request.__dict__.setdefault("_timeline_reads", {}) if isinstance(request, HttpRequest) else None
    key = (model, str(id))
    if cache is not None and key in cache:
        return cache[key]
    timeline = Timeline(_timeline_record(model, id, actor), actor)
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

        limit = clamp_page_size(limit)
        try:
            return _timeline(info, model, id).page(kinds=kinds, needs_me=needs_me, before=before, limit=limit)
        except InvalidKeysetCursor as error:
            raise ValidationError(str(error), code="invalid") from error

    @strawberry.field
    def timeline_attention(self, info: strawberry.Info, model: str, id: PublicID) -> int:
        """How many lines on the record wait on the viewer."""

        return _timeline(info, model, id).attention()


@strawberry.type
class TimelineSubscription:
    @strawberry.subscription(name="timelineChanged")
    async def timeline_changed(self, model: str, id: PublicID) -> AsyncGenerator[bool, None]:
        """Invalidate this timeline only while its record remains readable.

        Admission and every delivery use the read's actor-scoped lookup. An
        inaccessible, missing or revoked record reveals no timeline activity.
        """

        actor = current_actor()
        if actor is None:
            return
        read = database_sync_to_async(_timeline_record, thread_sensitive=False)
        try:
            record = await read(model, id, actor)
        except ValidationError:
            return
        async with aclosing(subscribe_group(timeline_group(RecordRef.from_instance(record)))) as stream:
            async for _payload in stream:
                try:
                    await read(model, id, actor)
                except ValidationError:
                    return
                yield True


schemas: dict[str, dict[str, list[Any]]] = {
    "console": {"query": [TimelineQuery], "subscription": [TimelineSubscription]},
}
