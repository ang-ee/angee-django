"""Workflow runs on the record timeline: one event per run per record it worked on."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from functools import cached_property
from typing import Any

from django.apps import apps
from django.db import models
from rebac import GenericTarget, generic_target, system_context

from angee.base.pagination import clamp_page_size
from angee.graphql.relations import with_record_reference_access
from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind, actor_label
from angee.workflows.states import RunStatus, StepRunStatus, WaitingKind

KIND = "workflows.run"
RUN_MODEL = "workflows.WorkflowRun"


def run_records(run: Any) -> list[RecordIdentity]:
    """The records a run is about: its subject, the rows its steps retained, and its parent run.

    Each is an identity, deduped; notification delivery resolves merge redirects. The run
    itself is never among them, so a run never lists itself on its own page.
    Timeline invalidation owns the audited system context for these relation reads.
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
    evidence = apps.get_model("workflows", "StepRecord").objects.filter(run_id=run.pk)
    for content_type_id, object_id in evidence.order_by().values_list("content_type_id", "object_id").distinct():
        add(content_type_id, object_id)
    if run.parent_step_id:
        parent_run_id = (
            apps.get_model("workflows", "StepRun").objects
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

    def occurs(self, record: Any) -> bool:
        run = apps.get_model("workflows", "WorkflowRun")
        return run._base_manager.filter(self._record_filter(record)).exists()

    def _record_filter(self, record: Any) -> models.Q:
        run = apps.get_model("workflows", "WorkflowRun")
        predicate = run.objects.about_filter(record)
        if isinstance(record, run):
            predicate = (predicate | models.Q(parent_step__run=record)) & ~models.Q(pk=record.pk)
        return predicate

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        runs = apps.get_model("workflows", "WorkflowRun").objects.with_actor(actor)
        rows = runs.filter(self._record_filter(record)).distinct().scoped()
        waiting = apps.get_model("workflows", "StepRun").system_queryset().filter(
            status=StepRunStatus.WAITING,
        ).defer("input", "output", "state", "notes").order_by("rank", "map_index")
        return rows.select_related("version__workflow", "run_as").prefetch_related(
            models.Prefetch("step_runs", queryset=waiting, to_attr="timeline_waits"),
        )

    def waiting_on(self, record: Any, actor: Any) -> models.QuerySet | None:
        run = apps.get_model("workflows", "WorkflowRun")
        waiting = models.Q(
            status=RunStatus.WAITING,
            step_runs__status=StepRunStatus.WAITING,
            step_runs__waiting_kind=WaitingKind.DECISION,
        )
        if not run._base_manager.filter(self._record_filter(record), waiting).exists():
            return None
        decisions = apps.get_model("decisions", "Decision").objects.waiting_on(actor)
        return run._base_manager.filter(waiting, step_runs__decision__in=decisions.values("pk"))

    def targets(self, row: Any) -> Iterable[models.Model]:
        return (row,) if row._meta.label == RUN_MODEL else apps.get_model(
            "workflows", "WorkflowRun",
        ).objects.filter(pk=row.run_id)

    def records(self, run: Any) -> Iterable[RecordIdentity]:
        return run_records(run)

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        result = {}
        for run in rows:
            detail = run.get_status_display()
            if run.timeline_waits and run.status == RunStatus.WAITING:
                detail = f"{detail} · {run.timeline_waits[0].node_label}"
            result[run.pk] = Presentation(run.version.workflow.name, detail, actor_label(run.run_as))
        return result


RECORD_RUN_LIMIT = 20
"""The newest runs a record's timeline lists, and so the most its Runs group counts."""


class TimelineSelection:
    """Read and group a record selection's runs, open decisions and attention."""

    def __init__(self, records: Sequence[models.Model], actor: Any, *, run_limit: int = RECORD_RUN_LIMIT) -> None:
        self.records = records
        self.actor = actor
        self.run_limit = clamp_page_size(run_limit)
        self.targets = tuple(dict.fromkeys(generic_target(record) for record in records))

    @cached_property
    def runs(self) -> models.QuerySet:
        """The run-owner selection, read only for what :meth:`entries` groups by.

        Ancestry groups by the parent run's id, so no parent step row is loaded; a
        schema reads the grouped runs' own fields through its optimizer.
        """

        runs = apps.get_model("workflows", "WorkflowRun").objects.with_actor(self.actor)
        return runs.about(self.records, limit=self.run_limit).order_by("-created_at", "-pk").only(
            "subject_content_type_id", "subject_object_id",
        ).annotate(_parent_run_id=models.F("parent_step__run_id")).prefetch_related("records")

    @cached_property
    def open_decision_count(self) -> int:
        """Preserve record-level attention independently of question readability."""

        decisions = apps.get_model("decisions", "Decision")
        with system_context(reason="workflows.timeline.selection_attention"):
            return int(decisions.objects.open_for(*self.records).count())

    def entries(self, runs: Sequence[Any]) -> Iterator[tuple[GenericTarget, list[Any], list[Any]]]:
        """Group selected runs with their ancestors and readable open decisions."""

        decisions = apps.get_model("decisions", "Decision").objects.with_actor(self.actor)
        links = apps.get_model("decisions", "DecisionRecord").objects.with_actor(self.actor)
        questions = list(decisions.open_for(*self.records).prefetch_related(models.Prefetch(
            "records", queryset=with_record_reference_access(links),
        )))
        by_run = {run.pk: run for run in runs}
        for target in self.targets:
            identity = (target.content_type.pk, target.object_id)
            selected = {run.pk for run in runs if (
                (run.subject_content_type_id, run.subject_object_id) == identity
                or any((link.content_type_id, link.object_id) == identity for link in run.records.all()))}
            for run_id in tuple(selected):
                run = by_run[run_id]
                while run._parent_run_id in by_run:
                    run = by_run[run._parent_run_id]
                    if run.pk in selected:
                        break
                    selected.add(run.pk)
            yield (
                target,
                list(reversed([run for run in runs if run.pk in selected][:self.run_limit])),
                [decision for decision in questions if any(
                    (link.content_type_id, link.object_id) == identity for link in decision.records.all())],
            )


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (RunKind(),)
