"""Workflow runs on the record timeline: one event per run per record it worked on."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import to_subject_ref

from angee.identity import public_id_of
from angee.refs import generic_pointer_target
from angee.timeline.events import target_ref
from angee.timeline.kinds import EventData, TimelineKind, actor_label
from angee.workflows.states import RunStatus, StepRunStatus, WaitingKind

KIND = "workflows.run"
RUN_MODEL = "workflows.WorkflowRun"


def record_key_part(record: models.Model) -> str:
    """The record half of an event key: its canonical model label and primary key."""

    content_type, object_id = generic_pointer_target(record)
    return f"{content_type.model_class()._meta.label}/{object_id}"


def run_event_key(run: models.Model, record: models.Model) -> str:
    """The key of ``run``'s line on ``record``; decisions asked by the run fold under it."""

    return f"{KIND}:{public_id_of(run)}:{record_key_part(record)}"


def run_records(run: models.Model) -> list[models.Model]:
    """The records a run is about: its subject, the rows its steps retained, and its parent run.

    Each is a canonical shell instance (model and primary key), deduped; the run
    itself is never among them, so a run never lists itself on its own page.
    """

    seen: set[tuple[int, Any]] = set()
    records: list[models.Model] = []

    def add(content_type_id: int, object_id: Any) -> None:
        if (content_type_id, object_id) in seen:
            return
        content_type = apps.get_model("contenttypes", "ContentType").objects.get_for_id(content_type_id)
        model = content_type.model_class()
        if model is None:
            return
        if model._meta.label == RUN_MODEL and object_id == run.pk:
            return
        seen.add((content_type_id, object_id))
        records.append(model(pk=object_id))

    if run.subject_content_type_id and run.subject_object_id:
        add(run.subject_content_type_id, run.subject_object_id)
    evidence = apps.get_model("workflows", "StepRecord").system_queryset().filter(run_id=run.pk)
    for content_type_id, object_id in evidence.order_by("pk").values_list("content_type_id", "object_id").distinct():
        add(content_type_id, object_id)
    if run.parent_step_id:
        parent_run_id = (
            apps.get_model("workflows", "StepRun").system_queryset()
            .filter(pk=run.parent_step_id).values_list("run_id", flat=True).first()
        )
        if parent_run_id:
            run_type = apps.get_model("contenttypes", "ContentType").objects.get_for_model(
                apps.get_model("workflows", "WorkflowRun"), for_concrete_model=False,
            )
            add(run_type.pk, parent_run_id)
    return records


class RunKind:
    """Every run that worked on a record, as one line each; the run drill-in holds its steps."""

    id = KIND
    sequence = 10
    models = (RUN_MODEL, "workflows.StepRun")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def rows(self) -> Iterable[models.Model]:
        return apps.get_model("workflows", "WorkflowRun").system_queryset().order_by("pk").iterator()

    def events(self, row: models.Model) -> Iterable[EventData]:
        run = row.run if row._meta.label == "workflows.StepRun" else row
        run = apps.get_model("workflows", "WorkflowRun").system_queryset().select_related(
            "version__workflow", "run_as",
        ).get(pk=run.pk)
        waiting = (
            apps.get_model("workflows", "StepRun").system_queryset()
            .filter(run_id=run.pk, status=StepRunStatus.WAITING)
            .order_by("rank", "map_index").first()
        )
        detail = run.get_status_display()
        needs: tuple[Any, ...] = ()
        if waiting is not None and run.status == RunStatus.WAITING:
            detail = f"{detail} · {waiting.node_label}"
            if waiting.waiting_kind == WaitingKind.DECISION and waiting.decision_id:
                decision = apps.get_model("decisions", "Decision").system_queryset().filter(
                    pk=waiting.decision_id, verdict__isnull=True,
                ).first()
                if decision is not None:
                    needs = tuple(decision.assignees.values_list("pk", flat=True))
        target_model, target_id = target_ref(run)
        actor = str(to_subject_ref(run.run_as)) if run.run_as_id else ""
        return [
            EventData(
                key=run_event_key(run, record), kind=KIND, at=run.created_at, record=record,
                target_model=target_model, target_id=target_id, title=run.version.workflow.name, detail=detail,
                actor=actor, actor_label=actor_label(run.run_as), needs=needs,
            )
            for record in run_records(run)
        ]


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (RunKind(),)
