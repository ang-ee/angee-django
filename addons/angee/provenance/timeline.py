"""Changesets on the record timeline: one line per changeset a person or sync made."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import SubjectRef
from rebac.resources import model_for_subject_type

from angee.identity import public_id_of
from angee.models import record_history
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource

KIND = "provenance.change"
CHANGESET_MODEL = "provenance.Changeset"


def _actor_label(stored: str) -> str:
    """The stored subject's display label; the raw subject when it does not resolve."""

    if not stored:
        return ""
    subject = SubjectRef.parse(stored)
    resolved = model_for_subject_type(subject.subject_type)
    if resolved is None or subject.subject_id in ("", "*"):
        return str(subject)
    instance = resolved[0]._base_manager.filter(pk=subject.subject_id).first()
    return str(instance) if instance is not None else str(subject)


class ChangeSource:
    """Every changeset touching the record, newest first; a run's changesets fold into its run."""

    kind = KIND
    sequence = 30
    models = ("provenance.Changeset",)

    def eligible(self, model: type[models.Model]) -> bool:
        return record_history(model) is not None

    def _changesets(self, record: models.Model) -> Any:
        changesets = apps.get_model("provenance", "Changeset").objects.sudo(reason="timeline.changes").touching(record)
        if not apps.is_installed("angee.workflows"):
            return changesets
        run_type = apps.get_model("contenttypes", "ContentType").objects.get_for_model(
            apps.get_model("workflows", "WorkflowRun"), for_concrete_model=False,
        )
        return changesets.exclude(origin_content_type=run_type)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int, needs_me: bool,
    ) -> Iterable[TimelineEntry]:
        if needs_me:
            return []
        changesets = self._changesets(record)
        if before is not None:
            changesets = changesets.filter(before.predicate(self.kind, "occurred_at"))
        actors: dict[str, str] = {}
        rows: list[TimelineEntry] = []
        for changeset in changesets.order_by("-occurred_at", "-pk")[:limit]:
            if changeset.actor not in actors:
                actors[changeset.actor] = _actor_label(changeset.actor)
            rows.append(TimelineEntry(
                key=f"{self.kind}:{public_id_of(changeset)}:1", at=changeset.occurred_at, kind=self.kind,
                pk=changeset.pk,
                title=f"{changeset.get_action_display()} · {changeset.get_activity_display()}",
                actor=actors[changeset.actor], detail=changeset.change_reason,
                target_model=CHANGESET_MODEL, target_id=public_id_of(changeset),
            ))
        return rows

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._changesets(record).count())


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (ChangeSource(),)
