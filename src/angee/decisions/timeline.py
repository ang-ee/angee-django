"""Decisions on records, folding through their owner's requesting references."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.base.refs import generic_pointer_model
from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind, actor_label

KIND = "decisions.decision"


class DecisionKind(TimelineKind):
    id = KIND
    sequence = 20
    source_models = ("decisions.Decision",)

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def _requested_by(self, record: Any) -> models.Q:
        scope = models.Q(pk__in=[])
        for rows in apps.get_model("decisions", "Decision").requester_querysets():
            if generic_pointer_model(rows.model) is generic_pointer_model(type(record)):
                scope |= models.Q(pk__in=rows.filter(pk=record.pk).order_by().values("_decision_id"))
        return scope

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        rows = apps.get_model("decisions", "Decision").objects.with_actor(actor)
        return (rows.for_records(record) | rows.filter(self._requested_by(record))).scoped().select_related(
            "answered_by",
        ).prefetch_related(
            models.Prefetch("assignees", queryset=get_user_model().system_queryset()),
        )

    def waiting_on(self, rows: Any, actor: Any) -> models.QuerySet:
        return rows.waiting_on(actor).scoped()

    def fold(self, record: Any) -> tuple[Any, Any]:
        types, ids = [], []
        requested = self._requested_by(record)
        for rows in apps.get_model("decisions", "Decision").requester_querysets():
            rows = rows.filter(_decision_id=models.OuterRef("pk")).order_by("pk")
            types.append(models.When(models.Exists(rows), then=models.Value(ContentType.objects.get_for_model(
                rows.model,
            ).pk)))
            ids.append(models.When(models.Exists(rows), then=models.Subquery(rows.values("pk")[:1])))
        return (
            models.Case(models.When(requested, then=models.Value(None)), *types, output_field=models.IntegerField()),
            models.Case(models.When(requested, then=models.Value(None)), *ids, output_field=models.BigIntegerField()),
        )

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        result = {}
        for row in rows:
            names = ", ".join(actor_label(user) for user in row.assignees.all())
            detail = (f"Waiting on {names}" if names else "Waiting") if row.is_open else row.verdict_label
            result[row.pk] = Presentation(row.kind_label, detail, actor_label(row.answered_by))
        return result

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        links = row.records.model.system_queryset().filter(decision_id=row.pk).select_related("content_type")
        for link in links:
            if (model := link.content_type.model_class()) is not None:
                yield model, link.object_id
        yield from ((type(record), record.pk) for record in row.requesting_records())


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (DecisionKind(),)
