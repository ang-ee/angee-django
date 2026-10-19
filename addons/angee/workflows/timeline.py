"""Workflow runs on the record timeline: one line per run, pointing at the run."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.identity import public_id_of
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource
from angee.workflows.states import StepRunStatus

RUN_MODEL = "workflows.WorkflowRun"


def _actor_label(user: Any) -> str:
    return (user.get_full_name() or user.get_username()) if user is not None else ""


class RunSource:
    """Every run that worked on the record, newest first."""

    kind = "run"

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def _runs(self, record: models.Model, actor: Any) -> Any:
        return apps.get_model("workflows", "WorkflowRun").objects.with_actor(actor).about(record)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int,
    ) -> Iterable[TimelineEntry]:
        runs = self._runs(record, actor)
        if before is not None:
            runs = runs.filter(**before.before("created_at"))
        runs = runs.order_by("-created_at", "-pk").select_related("version__workflow", "run_as")[:limit]
        for run in runs:
            waiting = (
                apps.get_model("workflows", "StepRun").objects.with_actor(actor)
                .filter(run=run, status=StepRunStatus.WAITING)
                .order_by("rank", "map_index")
                .first()
            )
            detail = run.get_status_display()
            needs_me = False
            if waiting is not None:
                detail = f"{detail} · {waiting.node_label}"
                if waiting.decision_id is not None:
                    needs_me = apps.get_model("decisions", "Decision").objects.with_actor(actor).open().filter(
                        pk=waiting.decision_id, assignees=actor,
                    ).exists()
            yield TimelineEntry(
                id=public_id_of(run), at=run.created_at, kind=self.kind,
                title=run.version.workflow.name, actor=_actor_label(run.run_as), detail=detail,
                needs_me=needs_me, target_model=RUN_MODEL, target_id=public_id_of(run),
            )

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._runs(record, actor).count())


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (RunSource(),)
