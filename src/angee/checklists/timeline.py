"""Recorded checklist results on their record's chronology."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac.field_backing import canonical_model

from angee.checklists.contracts import ChecklistStatus
from angee.checklists.models import ChecklistMixin
from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind, actor_label

KIND = "checklists.result"


def _labels(target: type[models.Model] | None) -> dict[str, str]:
    """Item labels the checklist types keyed on ``target`` declare."""

    labels: dict[str, str] = {}
    for model in sorted(apps.get_models(), key=lambda model: model._meta.label):
        if issubclass(model, ChecklistMixin) and canonical_model(model) is target:
            labels.update({item.key: item.label for item in model.checklist().items})
    return labels


class ChecklistResultKind(TimelineKind):
    """Each recorded result, read as its result reads: through its record and its evidence."""

    id = KIND
    sequence = 30
    source_models = ("checklists.ChecklistResult",)

    def eligible(self, model: type[models.Model]) -> bool:
        return issubclass(model, ChecklistMixin)

    def _rows(self) -> Any:
        return apps.get_model("checklists", "ChecklistResult")

    def occurs(self, record: Any) -> bool:
        results = self._rows()
        return bool(results._base_manager.filter(results.record_filter(record)).exists())

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        results = self._rows()
        return (
            results.objects.with_actor(actor)
            .filter(results.record_filter(record))
            .scoped()
            .select_related(
                "created_by",
                "content_type",
            )
        )

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        rows = list(rows)
        labels: dict[Any, dict[str, str]] = {}
        result = {}
        for row in rows:
            target = row.content_type.model_class()
            names = labels.setdefault(row.content_type_id, _labels(target))
            title = f"{names.get(row.key, row.key)}: {ChecklistStatus(row.status).label}"
            result[row.pk] = Presentation(title, row.detail, actor_label(row.created_by))
        return result

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        if (model := row.content_type.model_class()) is not None:
            yield model, row.object_id


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (ChecklistResultKind(),)
