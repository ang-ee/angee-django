"""Changesets on the record timeline: one line per changeset, pointing at history."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import SubjectRef
from rebac.resources import model_for_subject_type

from angee.identity import public_id_for, public_id_of
from angee.models import record_history
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource

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


def _run_of(changeset: Any) -> str | None:
    """The workflow run a run changeset originated from, when its origin is one."""

    origin = changeset.origin_content_type
    if origin is None or changeset.origin_object_id in (None, ""):
        return None
    model = origin.model_class()
    if model is None or model._meta.label_lower != "workflows.workflowrun":
        return None
    return public_id_for(model, changeset.origin_object_id)


class ChangeSource:
    """Every changeset touching the record, newest first."""

    kind = "change"

    def eligible(self, model: type[models.Model]) -> bool:
        return record_history(model) is not None

    def _changesets(self, record: models.Model) -> Any:
        changesets = apps.get_model("provenance", "Changeset").objects.sudo(reason="timeline.changes")
        return changesets.touching(record)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int,
    ) -> Iterable[TimelineEntry]:
        changesets = self._changesets(record)
        if before is not None:
            changesets = changesets.filter(**before.before("occurred_at"))
        actors: dict[str, str] = {}
        for changeset in changesets.order_by("-occurred_at", "-pk").select_related("origin_content_type")[:limit]:
            if changeset.actor not in actors:
                actors[changeset.actor] = _actor_label(changeset.actor)
            yield TimelineEntry(
                id=public_id_of(changeset), at=changeset.occurred_at, kind=self.kind,
                title=f"{changeset.get_action_display()} · {changeset.get_activity_display()}",
                actor=actors[changeset.actor], detail=changeset.change_reason,
                target_model=CHANGESET_MODEL, target_id=public_id_of(changeset), group_id=_run_of(changeset),
            )

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._changesets(record).count())


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (ChangeSource(),)
