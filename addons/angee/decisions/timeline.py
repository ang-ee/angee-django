"""Answered decisions on the record timeline: one line per verdict."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.identity import public_id_of
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource

DECISION_MODEL = "decisions.Decision"


def _actor_label(user: Any) -> str:
    return (user.get_full_name() or user.get_username()) if user is not None else ""


class AnsweredDecisionSource:
    """Decisions answered on the record, newest verdict first."""

    kind = "decision"

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def _answered(self, record: models.Model, actor: Any) -> Any:
        decisions = apps.get_model("decisions", "Decision").objects.with_actor(actor)
        return decisions.for_records(record).exclude(answered_at=None)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int,
    ) -> Iterable[TimelineEntry]:
        decisions = self._answered(record, actor)
        if before is not None:
            decisions = decisions.filter(**before.before("answered_at"))
        decisions = decisions.order_by("-answered_at", "-pk").select_related("answered_by")[:limit]
        for decision in decisions:
            step = decision.requesting_steps.select_related("run").first()
            yield TimelineEntry(
                id=public_id_of(decision), at=decision.answered_at, kind=self.kind,
                title=decision.kind_label, actor=_actor_label(decision.answered_by), detail=decision.verdict_label,
                target_model=DECISION_MODEL, target_id=public_id_of(decision),
                group_id=public_id_of(step.run) if step is not None else None,
            )

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._answered(record, actor).count())


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (AnsweredDecisionSource(),)
