"""Decisions answered outside a run on the record timeline: one line per verdict."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.identity import public_id_of
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource, actor_label

KIND = "decisions.decision"
DECISION_MODEL = "decisions.Decision"


class AnsweredDecisionSource:
    """Decisions answered on the record, newest verdict first; a run's decisions fold into its run."""

    kind = KIND
    sequence = 20
    models = ("decisions.Decision", "decisions.DecisionRecord")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def _answered(self, record: models.Model, actor: Any) -> Any:
        decisions = apps.get_model("decisions", "Decision").objects.with_actor(actor)
        return decisions.for_records(record).exclude(answered_at=None).filter(requesting_steps__isnull=True)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int, needs_me: bool,
    ) -> Iterable[TimelineEntry]:
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

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._answered(record, actor).count())


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (AnsweredDecisionSource(),)
