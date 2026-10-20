"""Closed changesets projected through the history owner's document membership."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.models import record_history
from angee.timeline.kinds import EventData, TimelineKind, actor_label


class ChangeKind(TimelineKind):
    """A document's history has no independent row policy beyond its record."""

    id = "provenance.change"
    sequence = 30
    source_models = ("provenance.Changeset",)
    explicit_publication = True

    def eligible(self, model: type[models.Model]) -> bool:
        return record_history(model) is not None

    def rows(self) -> Iterable[models.Model]:
        model = apps.get_model("provenance", "Changeset")
        return model._base_manager.filter(state=model.State.CLOSED).order_by("pk").iterator()

    def events(self, changeset: Any) -> Iterable[EventData]:
        if changeset.state != changeset.State.CLOSED:
            return
        fold = None
        if changeset.origin_content_type_id:
            origin_model = changeset.origin_content_type.model_class()
            if origin_model is not None:
                fold = origin_model(pk=changeset.origin_object_id)
        label = actor_label(changeset.actor)
        records = type(changeset).objects.filter(pk=changeset.pk).records_touched()
        for model, pk in records:
            yield EventData(
                kind=self.id, target=changeset, record=(model, pk), at=changeset.occurred_at,
                title=f"{changeset.get_action_display()} · {changeset.get_activity_display()}",
                detail=changeset.reason_display, actor=changeset.actor, actor_label=label, fold=fold,
            )


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (ChangeKind(),)
