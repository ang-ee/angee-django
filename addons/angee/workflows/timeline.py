"""Workflow runs on the record timeline: one event per run per record it worked on."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import to_subject_ref

from angee.timeline.kinds import EventData, RecordIdentity, TimelineKind, actor_label, target_scope
from angee.workflows.states import RunStatus, StepRunStatus, WaitingKind

KIND = "workflows.run"
RUN_MODEL = "workflows.WorkflowRun"


def run_records(run: Any) -> list[RecordIdentity]:
    """The records a run is about: its subject, the rows its steps retained, and its parent run.

    Each is an identity, deduped; the ledger resolves merge redirects. The run
    itself is never among them, so a run never lists itself on its own page.
    """

    seen: set[tuple[int, Any]] = set()
    records: list[RecordIdentity] = []

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
        records.append((model, int(object_id)))

    if run.subject_content_type_id and run.subject_object_id:
        add(run.subject_content_type_id, run.subject_object_id)
    evidence = apps.get_model("workflows", "StepRecord").system_queryset().filter(run_id=run.pk)
    for content_type_id, object_id in evidence.order_by().values_list("content_type_id", "object_id").distinct():
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


class RunKind(TimelineKind):
    """Every run that worked on a record, as one line each; the run drill-in holds its steps."""

    id = KIND
    sequence = 10
    source_models = (RUN_MODEL, "workflows.StepRun", "workflows.StepRecord")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def readable(self, actor: Any) -> models.Q:
        return target_scope(apps.get_model("workflows", "WorkflowRun").objects.with_actor(actor))

    def waiting_on(self, actor: Any) -> models.Q:
        decisions = apps.get_model("decisions", "Decision").objects.with_actor(actor).waiting_on(actor).scoped()
        steps = apps.get_model("workflows", "StepRun").objects.with_actor(actor).scoped().filter(
            status=StepRunStatus.WAITING, waiting_kind=WaitingKind.DECISION,
            decision_id__in=decisions.values("pk"),
        )
        return target_scope(apps.get_model("workflows", "WorkflowRun").objects.with_actor(actor).filter(
            status=RunStatus.WAITING, pk__in=steps.values("run_id"),
        ))

    def targets(self, row: Any) -> Iterable[models.Model]:
        return (row,) if row._meta.label == RUN_MODEL else (apps.get_model("workflows", "WorkflowRun")(pk=row.run_id),)

    def rows(self) -> Iterable[models.Model]:
        return apps.get_model("workflows", "WorkflowRun").system_queryset().order_by("pk").iterator()

    def events(self, row: Any) -> Iterable[EventData]:
        run = apps.get_model("workflows", "WorkflowRun").system_queryset().select_related(
            "version__workflow", "run_as",
        ).get(pk=row.pk)
        waiting = (
            apps.get_model("workflows", "StepRun").system_queryset()
            .filter(run_id=run.pk, status=StepRunStatus.WAITING)
            .defer("input", "output", "state", "notes").order_by("rank", "map_index").first()
        )
        detail = run.get_status_display()
        if waiting is not None and run.status == RunStatus.WAITING:
            detail = f"{detail} · {waiting.node_label}"
        actor = str(to_subject_ref(run.run_as)) if run.run_as_id else ""
        return [
            EventData(
                kind=KIND, target=run, at=run.created_at, record=record,
                title=run.version.workflow.name, detail=detail, actor=actor, actor_label=actor_label(run.run_as),
            )
            for record in run_records(run)
        ]


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (RunKind(),)
