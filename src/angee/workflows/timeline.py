"""Workflow runs on the record timeline: one event per run per record it worked on."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import cached_property
from typing import Any, cast

from django.apps import apps
from django.db import models
from django.db.models.query import EmptyQuerySet
from rebac import GenericTarget, generic_target

from angee.decisions.querysets import DecisionQuerySet
from angee.iam.graphql.relations import with_record_reference_access
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

    using = run._state.db
    content_types = apps.get_model("contenttypes", "ContentType").objects.db_manager(using)
    seen: set[tuple[int, Any]] = set()
    records: list[RecordIdentity] = []

    def add(content_type_id: int, object_id: Any) -> None:
        if (content_type_id, object_id) in seen:
            return
        content_type = content_types.get_for_id(content_type_id)
        model = content_type.model_class()
        if model is None:
            return
        if model._meta.label == RUN_MODEL and object_id == run.pk:
            return
        seen.add((content_type_id, object_id))
        records.append((model, int(object_id)))

    if run.subject_content_type_id and run.subject_object_id:
        add(run.subject_content_type_id, run.subject_object_id)
    evidence = (
        apps.get_model("workflows", "StepRecord")
        ._default_manager.db_manager(using)
        .filter(run_id=run.pk)
    )
    for content_type_id, object_id in evidence.order_by().values_list("content_type_id", "object_id").distinct():
        add(content_type_id, object_id)
    if run.parent_step_id:
        parent_run_id = (
            apps.get_model("workflows", "StepRun")
            .objects.db_manager(using)
            .filter(pk=run.parent_step_id)
            .values_list("run_id", flat=True)
            .first()
        )
        if parent_run_id:
            run_type = content_types.get_for_model(
                apps.get_model("workflows", "WorkflowRun"),
                for_concrete_model=False,
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

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        runs = apps.get_model("workflows", "WorkflowRun").objects.with_actor(actor)
        rows = runs.about(record, ancestors=False)
        if isinstance(record, rows.model):
            rows = (rows | runs.filter(parent_step__run=record).distinct()).exclude(pk=record.pk)
        rows = rows.scoped()
        waiting = apps.get_model("workflows", "StepRun")._base_manager.filter(
            status=StepRunStatus.WAITING,
        ).defer("input", "output", "state", "notes").order_by("rank", "map_index")
        return rows.select_related("version__workflow", "run_as").prefetch_related(
            models.Prefetch("step_runs", queryset=waiting, to_attr="timeline_waits"),
        )

    def waiting_on_expression(self, rows: Any, actor: Any) -> models.Expression:
        """Pre-read this record's admitted waiting runs before projecting attention."""
        decisions = apps.get_model("decisions", "Decision").objects.using(rows.db).waiting_on(actor)
        if isinstance(decisions, EmptyQuerySet):
            return models.Value(False, output_field=models.BooleanField())
        run_ids = tuple(rows.filter(status=RunStatus.WAITING).order_by().values_list("pk", flat=True))
        if not run_ids:
            return models.Value(False, output_field=models.BooleanField())
        steps = apps.get_model("workflows", "StepRun").objects.using(rows.db).with_actor(actor)
        pairs = tuple(steps.scoped_for_aggregate().filter(
            run_id__in=run_ids, status=StepRunStatus.WAITING, waiting_kind=WaitingKind.DECISION,
            decision_id__isnull=False,
        ).order_by().values_list("run_id", "decision_id"))
        if not pairs:
            return models.Value(False, output_field=models.BooleanField())
        decision_ids = {decision_id for _, decision_id in pairs}
        actionable = set(decisions.with_actor(actor).scoped_for_aggregate().filter(
            pk__in=decision_ids,
        ).order_by().values_list("pk", flat=True))
        waiting_runs = {run_id for run_id, decision_id in pairs if decision_id in actionable}
        return models.Case(
            models.When(status=RunStatus.WAITING, pk__in=waiting_runs, then=models.Value(True)),
            default=models.Value(False), output_field=models.BooleanField(),
        )

    def targets(self, row: Any) -> Iterable[models.Model]:
        return (
            (row,)
            if row._meta.label == RUN_MODEL
            else apps.get_model(
                "workflows",
                "WorkflowRun",
            )
            .objects.db_manager(row._state.db, hints={"instance": row})
            .filter(pk=row.run_id)
        )

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


class TimelineSelection:
    """Read and group a record selection's runs, open decisions and attention."""

    def __init__(self, records: Sequence[models.Model], actor: Any, *, run_limit: int = 20) -> None:
        self.records = records
        self.actor = actor
        self.run_limit = max(1, min(run_limit, 100))
        self.targets = tuple(dict.fromkeys(generic_target(record) for record in records))

    @cached_property
    def runs(self) -> models.QuerySet:
        """The shared run-owner selection, ready for a schema-specific optimizer."""

        runs = apps.get_model("workflows", "WorkflowRun").objects.with_actor(self.actor)
        return runs.about(self.records, limit=self.run_limit).order_by("-created_at", "-pk").select_related(
            "parent_step",
        ).prefetch_related("records")

    @cached_property
    def open_decision_count(self) -> int:
        """Preserve record-level attention independently of question readability."""

        if not self.records:
            return 0
        decisions = apps.get_model("decisions", "Decision")
        rows = decisions._base_manager.using(self.records[0]._state.db)
        return int(DecisionQuerySet.for_records(cast(Any, rows.all()), *self.records).filter(
            verdict__isnull=True,
        ).count())

    @cached_property
    def runs_by_record(self) -> dict[GenericTarget, tuple[int, ...]]:
        """Run identities grouped once, independently of a schema's projection."""
        runs = list(self.runs)
        by_run = {run.pk: run for run in runs}
        grouped = {}
        for target in self.targets:
            identity = (target.content_type.pk, target.object_id)
            selected = {run.pk for run in runs if (
                (run.subject_content_type_id, run.subject_object_id) == identity
                or any((link.content_type_id, link.object_id) == identity for link in run.records.all()))}
            for run_id in tuple(selected):
                run = by_run[run_id]
                while run.parent_step_id and run.parent_step.run_id in by_run:
                    run = by_run[run.parent_step.run_id]
                    if run.pk in selected:
                        break
                    selected.add(run.pk)
            grouped[target] = tuple(reversed([run.pk for run in runs if run.pk in selected][:self.run_limit]))
        return grouped

    @cached_property
    def decisions_by_record(self) -> dict[GenericTarget, list[Any]]:
        """Readable open questions, loaded only by a question projection."""
        decisions = apps.get_model("decisions", "Decision").objects.with_actor(self.actor)
        links = apps.get_model("decisions", "DecisionRecord").objects.with_actor(self.actor)
        questions = list(decisions.open_for(*self.records).prefetch_related(models.Prefetch(
            "records", queryset=with_record_reference_access(links),
        )))
        return {
            target: [decision for decision in questions if any(
                (link.content_type_id, link.object_id) == (target.content_type.pk, target.object_id)
                for link in decision.records.all())]
            for target in self.targets
        }


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (RunKind(),)
