"""Decisions on the record timeline: one event per decision per record it concerns.

A decision a run step asked folds under that run's line on the same record and is
also a line of its own on the run's page, where the run is the record.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import to_subject_ref

from angee.identity import public_id_of
from angee.timeline.events import target_ref
from angee.timeline.kinds import EventData, TimelineKind, actor_label

KIND = "decisions.decision"
DECISION_MODEL = "decisions.Decision"


def _record_shell(content_type_id: int, object_id: Any) -> models.Model | None:
    model = apps.get_model("contenttypes", "ContentType").objects.get_for_id(content_type_id).model_class()
    return None if model is None else model(pk=object_id)


class DecisionKind:
    """Every decision a record carries, open ones waiting on their assignees."""

    id = KIND
    sequence = 20
    models = (DECISION_MODEL,)

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def rows(self) -> Iterable[models.Model]:
        return apps.get_model("decisions", "Decision").system_queryset().order_by("pk").iterator()

    def events(self, row: models.Model) -> Iterable[EventData]:
        decisions = apps.get_model("decisions", "Decision").system_queryset().select_related("answered_by")
        decision = decisions.get(pk=row.pk)
        assignees = list(decision.assignees.all())
        waiting = decision.is_open
        detail = _waiting_on(assignees) if waiting else decision.verdict_label
        actor = str(to_subject_ref(decision.answered_by)) if decision.answered_by_id else ""
        needs = tuple(user.pk for user in assignees) if waiting else ()
        target_model, target_id = target_ref(decision)
        common = dict(
            kind=KIND, at=decision.created_at, target_model=target_model, target_id=target_id,
            title=decision.kind_label, detail=detail, actor=actor, actor_label=actor_label(decision.answered_by),
            needs=needs,
        )
        requesting_runs = [
            step.run for step in apps.get_model("workflows", "StepRun").system_queryset()
            .filter(decision_id=decision.pk).select_related("run")
        ] if apps.is_installed("angee.workflows") else []
        events: list[EventData] = []
        links = decision.records.model.system_queryset().filter(decision_id=decision.pk).order_by("pk")
        for content_type_id, object_id in links.values_list("content_type_id", "object_id"):
            record = _record_shell(content_type_id, object_id)
            if record is None:
                continue
            part = f"{record._meta.label}/{object_id}"
            parent_key = None
            if requesting_runs:
                from angee.workflows.timeline import run_event_key

                parent_key = run_event_key(requesting_runs[0], record)
            events.append(EventData(
                key=f"{KIND}:{public_id_of(decision)}:{part}", record=record, parent_key=parent_key, **common,
            ))
        for run in requesting_runs:
            part = f"{run._meta.concrete_model._meta.label}/{run.pk}"
            events.append(EventData(key=f"{KIND}:{public_id_of(decision)}:{part}", record=run, **common))
        return events


def _waiting_on(assignees: list[Any]) -> str:
    names = [actor_label(user) for user in assignees]
    return f"Waiting on {', '.join(names)}" if names else "Waiting"


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (DecisionKind(),)
