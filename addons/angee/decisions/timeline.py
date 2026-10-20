"""Decisions on records, folding through their owner's requesting references."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import to_subject_ref

from angee.timeline.kinds import EventData, TimelineKind, actor_label, target_scope

KIND = "decisions.decision"


class DecisionKind(TimelineKind):
    """One question per concerned record, waiting on its current assignees."""

    id = KIND
    sequence = 20
    source_models = ("decisions.Decision",)

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def readable(self, actor: Any) -> models.Q:
        return target_scope(apps.get_model("decisions", "Decision").objects.with_actor(actor))

    def waiting_on(self, actor: Any) -> models.Q:
        return target_scope(apps.get_model("decisions", "Decision").objects.with_actor(actor).waiting_on(actor))

    def rows(self) -> Iterable[models.Model]:
        return apps.get_model("decisions", "Decision").system_queryset().order_by("pk").iterator()

    def events(self, row: Any) -> Iterable[EventData]:
        decision = (
            apps.get_model("decisions", "Decision").system_queryset().select_related("answered_by").get(pk=row.pk)
        )
        assignees = list(decision.assignees.all())
        names = ", ".join(actor_label(user) for user in assignees)
        detail = (f"Waiting on {names}" if names else "Waiting") if decision.is_open else decision.verdict_label
        common = dict(
            kind=self.id, target=decision, at=decision.created_at, title=decision.kind_label, detail=detail,
            actor=str(to_subject_ref(decision.answered_by)) if decision.answered_by_id else "",
            actor_label=actor_label(decision.answered_by),
        )
        requesters = list(decision.requesting_records())
        links = decision.records.model.system_queryset().filter(decision_id=decision.pk).select_related("content_type")
        for link in links.order_by("pk"):
            model = link.content_type.model_class()
            if model is not None:
                yield EventData(record=(model, link.object_id), fold=requesters[0] if requesters else None, **common)
        for requester in requesters:
            yield EventData(record=(type(requester), requester.pk), **common)


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (DecisionKind(),)
