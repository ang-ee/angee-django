"""Decisions on the record timeline: verdicts answered outside a run, and on a run page every decision the run asked."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.identity import public_id_of
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource, actor_label

KIND = "decisions.decision"
DECISION_MODEL = "decisions.Decision"


def is_run(record: models.Model) -> bool:
    return record._meta.label == "workflows.WorkflowRun"


class AnsweredDecisionSource:
    """Decisions answered on the record, newest verdict first; a run's decisions fold into its run.

    On a run's own page the run is the record, so its decisions are listed here as asked:
    open ones wait on their assignees, answered ones carry the verdict.
    """

    kind = KIND
    sequence = 20
    models = ("decisions.Decision", "decisions.DecisionRecord")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def _answered(self, record: models.Model, actor: Any) -> Any:
        decisions = apps.get_model("decisions", "Decision").objects.with_actor(actor)
        return decisions.for_records(record).exclude(answered_at=None).filter(requesting_steps__isnull=True)

    def _asked_by_run(self, run: models.Model, actor: Any) -> Any:
        decisions = apps.get_model("decisions", "Decision").objects.with_actor(actor)
        return decisions.filter(requesting_steps__run=run).distinct()

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int, needs_me: bool,
    ) -> Iterable[TimelineEntry]:
        if is_run(record):
            return self._run_entries(record, actor, before=before, limit=limit, needs_me=needs_me)
        if needs_me:
            return []
        decisions = self._answered(record, actor)
        if before is not None:
            decisions = decisions.filter(before.predicate(self.kind, "answered_at"))
        decisions = decisions.order_by("-answered_at", "-pk").select_related("answered_by")[:limit]
        return [
            TimelineEntry(
                key=f"{self.kind}:{public_id_of(decision)}:1", at=decision.answered_at, kind=self.kind, pk=decision.pk,
                title=decision.kind_label, actor=actor_label(decision.answered_by), detail=decision.verdict_label,
                target_model=DECISION_MODEL, target_id=public_id_of(decision),
            )
            for decision in decisions
        ]

    def _run_entries(
        self, run: models.Model, actor: Any, *, before: Cursor | None, limit: int, needs_me: bool,
    ) -> list[TimelineEntry]:
        decisions = self._asked_by_run(run, actor)
        if before is not None:
            decisions = decisions.filter(before.predicate(self.kind, "created_at"))
        decisions = decisions.order_by("-created_at", "-pk").select_related("answered_by").prefetch_related("assignees")
        rows: list[TimelineEntry] = []
        for decision in decisions[: limit if not needs_me else None]:
            waiting = decision.is_open
            needs = waiting and any(user.pk == actor.pk for user in decision.assignees.all())
            if needs_me and not needs:
                continue
            rows.append(TimelineEntry(
                key=f"{self.kind}:{public_id_of(decision)}:1", at=decision.created_at, kind=self.kind, pk=decision.pk,
                title=decision.kind_label,
                actor=actor_label(decision.answered_by) if not waiting else "",
                detail=decision.verdict_label if not waiting else _waiting_on(decision),
                needs_me=needs, target_model=DECISION_MODEL, target_id=public_id_of(decision),
            ))
            if len(rows) >= limit:
                break
        return rows

    def count(self, record: models.Model, actor: Any) -> int:
        if is_run(record):
            return int(self._asked_by_run(record, actor).count())
        return int(self._answered(record, actor).count())


def _waiting_on(decision: Any) -> str:
    names = [actor_label(user) for user in decision.assignees.all()]
    return f"Waiting on {', '.join(names)}" if names else "Waiting"


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (AnsweredDecisionSource(),)
