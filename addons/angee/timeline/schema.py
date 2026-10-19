"""The ``timeline`` read: one ordered page of a record's events from the ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import strawberry
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models import Count

from angee.graphql.ids import PublicID, require_instance_for_id
from angee.iam.permissions import request_from_info
from angee.timeline.kinds import Cursor, timeline_kinds

PAGE_LIMIT_DEFAULT = 25
PAGE_LIMIT_MAX = 100


@strawberry.type
class TimelineKindCount:
    kind: str
    count: int


@strawberry.type
class TimelineEntryType:
    """One line: what happened, who, when, where its body lives, and what folds under it."""

    key: str
    at: datetime
    kind: str
    title: str
    actor: str
    detail: str
    needs_me: bool
    target_model: str
    target_id: PublicID
    children: list[TimelineKindCount]


@strawberry.type
class TimelineKindType:
    """A kind this record can carry, in sequence order; its presentation is the kind's ``timeline#kinds`` child."""

    id: str
    count: int
    models: list[str]


@strawberry.type
class TimelinePageType:
    entries: list[TimelineEntryType]
    """Newest first."""
    kinds: list[TimelineKindType]
    needs_me: int
    """How many lines on the record wait on the viewer."""
    has_more: bool
    next_before: str | None


def _record(info: strawberry.Info, model: str, id: PublicID) -> tuple[Any, Any, Any]:
    actor = request_from_info(info).user
    try:
        record_model = apps.get_model(model)
    except (LookupError, ValueError) as error:
        raise ValidationError(f"Unknown model {model!r}.", code="invalid") from error
    record = require_instance_for_id(record_model, str(id), queryset=record_model.objects.with_actor(actor))
    return actor, record_model, record


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

        actor, record_model, record = _record(info, model, id)
        limit = max(1, min(int(limit), PAGE_LIMIT_MAX))
        try:
            cursor = Cursor.decode(before) if before else None
        except ValueError as error:
            raise ValidationError(str(error), code="invalid") from error
        event_model = apps.get_model("timeline", "Event")
        # The record was read under the actor; the ledger is its index.
        lines = event_model.objects.for_record(record).top_level()
        eligible = [kind for kind in timeline_kinds() if kind.eligible(record_model)]
        counts = dict(lines.values_list("kind").annotate(n=Count("pk")).values_list("kind", "n"))
        waiting = lines.needing(actor).count() if actor is not None and actor.pk else 0
        if kinds:
            lines = lines.filter(kind__in=kinds)
        if needs_me:
            lines = lines.needing(actor)
        if cursor is not None:
            lines = lines.filter(cursor.predicate())
        page = list(lines.order_by("-at", "kind", "-pk")[: limit + 1])
        more = len(page) > limit
        page = page[:limit]
        children: dict[int, list[TimelineKindCount]] = {}
        for parent_id, kind, n in (
            event_model.objects.filter(parent__in=[event.pk for event in page])
            .values_list("parent_id", "kind").annotate(n=Count("pk")).values_list("parent_id", "kind", "n")
            .order_by("parent_id", "kind")
        ):
            children.setdefault(parent_id, []).append(TimelineKindCount(kind=kind, count=n))
        mine = set(
            event_model.needs.through.objects.filter(event_id__in=[event.pk for event in page], user_id=actor.pk)
            .values_list("event_id", flat=True)
        ) if actor is not None and actor.pk else set()
        return TimelinePageType(
            entries=[
                TimelineEntryType(
                    key=event.key, at=event.at, kind=event.kind, title=event.title, actor=event.actor_label,
                    detail=event.detail, needs_me=event.pk in mine, target_model=event.target_model,
                    target_id=PublicID(event.target_id), children=children.get(event.pk, []),
                )
                for event in page
            ],
            kinds=[
                TimelineKindType(id=kind.id, count=counts.get(kind.id, 0), models=list(kind.models))
                for kind in eligible
            ],
            needs_me=waiting,
            has_more=more,
            next_before=Cursor(at=page[-1].at, kind=page[-1].kind, pk=page[-1].pk).encode() if page and more else None,
        )

    @strawberry.field
    def timeline_attention(self, info: strawberry.Info, model: str, id: PublicID) -> int:
        """How many lines on the record wait on the viewer."""

        actor, _record_model, record = _record(info, model, id)
        if actor is None or not actor.pk:
            return 0
        return int(apps.get_model("timeline", "Event").objects.for_record(record).top_level().needing(actor).count())


schemas: dict[str, dict[str, list[Any]]] = {"console": {"query": [TimelineQuery]}}
