"""Workflow runs on the record timeline: one line per run, its events folded into it."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from django.db.models import Count

from angee.identity import public_id_of
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource, actor_label
from angee.workflows.states import StepRunStatus

KIND = "workflows.run"
RUN_MODEL = "workflows.WorkflowRun"


class RunSource:
    """Every run that worked on the record, newest first; the run drill-in holds its events.

    On a run's own page the record is the run, whose events are its body: here the
    Timeline lists the runs it started instead, so the run never lists itself.
    """

    kind = KIND
    sequence = 10
    models = ("workflows.WorkflowRun", "workflows.StepRun")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def _runs(self, record: models.Model, actor: Any) -> Any:
        runs = apps.get_model("workflows", "WorkflowRun").objects.with_actor(actor)
        if record._meta.label == RUN_MODEL:
            return runs.filter(parent_step__run=record)
        return runs.about(record)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int, needs_me: bool,
    ) -> Iterable[TimelineEntry]:
        step_model = apps.get_model("workflows", "StepRun")
        decision_model = apps.get_model("decisions", "Decision")
        runs = self._runs(record, actor)
        if before is not None:
            runs = runs.filter(before.predicate(self.kind, "created_at"))
        runs = list(runs.order_by("-created_at", "-pk").select_related("version__workflow", "run_as")[:limit])
        if not runs:
            return []
        run_ids = [run.pk for run in runs]
        waiting: dict[int, Any] = {}
        for step in (
            step_model.objects.with_actor(actor)
            .filter(run_id__in=run_ids, status=StepRunStatus.WAITING)
            .order_by("run_id", "rank", "map_index")
        ):
            waiting.setdefault(step.run_id, step)
        mine = set(
            decision_model.objects.with_actor(actor).open()
            .filter(pk__in=[step.decision_id for step in waiting.values() if step.decision_id], assignees=actor)
            .values_list("pk", flat=True)
        )
        decisions = Counter(
            step_model.objects.with_actor(actor).filter(run_id__in=run_ids, decision__isnull=False)
            .values_list("run_id", flat=True)
        )
        content_types = apps.get_model("contenttypes", "ContentType").objects
        run_type = content_types.get_for_model(runs[0], for_concrete_model=False)
        changes = dict(
            apps.get_model("provenance", "Changeset").objects.sudo(reason="timeline.run_changes")
            .filter(origin_content_type=run_type, origin_object_id__in=[str(pk) for pk in run_ids])
            .values("origin_object_id").annotate(n=Count("pk")).values_list("origin_object_id", "n")
        )
        rows: list[TimelineEntry] = []
        for run in runs:
            step = waiting.get(run.pk)
            needs = step is not None and step.decision_id in mine
            if needs_me and not needs:
                continue
            detail = run.get_status_display()
            if step is not None:
                detail = f"{detail} · {step.node_label}"
            folded = " · ".join(
                part for part in (
                    _plural(changes.get(str(run.pk), 0), "change"),
                    _plural(decisions.get(run.pk, 0), "decision"),
                ) if part
            )
            rows.append(TimelineEntry(
                key=f"{self.kind}:{public_id_of(run)}:1", at=run.created_at, kind=self.kind, pk=run.pk,
                title=run.version.workflow.name, actor=actor_label(run.run_as), detail=detail,
                needs_me=needs, target_model=RUN_MODEL, target_id=public_id_of(run), folded=folded,
            ))
        return rows

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._runs(record, actor).count())


def _plural(n: int, noun: str) -> str:
    return "" if not n else f"{n} {noun}{'' if n == 1 else 's'}"


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (RunSource(),)
