"""Changesets on the record timeline: one event per record a closed changeset touched."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import SubjectRef
from rebac.resources import model_for_subject_type

from angee.identity import public_id_of
from angee.models import record_history
from angee.refs import generic_pointer_target
from angee.timeline.events import target_ref
from angee.timeline.kinds import EventData, TimelineKind

KIND = "provenance.change"
RUN_KIND = "workflows.run"
RUN_MODEL = "workflows.WorkflowRun"


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


class ChangeKind:
    """Every closed changeset, on each record it touched; a run's changesets fold under the run."""

    id = KIND
    sequence = 30
    models = ("provenance.Changeset",)

    def eligible(self, model: type[models.Model]) -> bool:
        return record_history(model) is not None

    def rows(self) -> Iterable[models.Model]:
        changeset_model = apps.get_model("provenance", "Changeset")
        return changeset_model._base_manager.filter(state=changeset_model.State.CLOSED).order_by("pk").iterator()

    def events(self, changeset: models.Model) -> Iterable[EventData]:
        if changeset.state != changeset.State.CLOSED:
            return []
        target_model, target_id = target_ref(changeset)
        title = f"{changeset.get_action_display()} · {changeset.get_activity_display()}"
        actor_label = _actor_label(changeset.actor)
        run_key = _origin_run_key(changeset)
        events: dict[str, EventData] = {}
        for model, history in changeset.history_rows():
            pk_name = model._meta.pk.attname
            ids = set(history.values_list(pk_name if _history_has(history.model, pk_name) else "id", flat=True))
            for row in model._base_manager.filter(pk__in=ids):
                content_type, pk = generic_pointer_target(row)
                occurrence = f"{content_type.model_class()._meta.label}/{pk}"
                key = f"{KIND}:{target_id}:{occurrence}"
                if key in events:
                    continue
                events[key] = EventData(
                    key=key, kind=KIND, at=changeset.occurred_at, record=row,
                    target_model=target_model, target_id=target_id, title=title, detail=changeset.change_reason,
                    actor=changeset.actor, actor_label=actor_label,
                    parent_key=f"{run_key}:{occurrence}" if run_key else None,
                )
        return list(events.values())


def _history_has(history_model: type[models.Model], name: str) -> bool:
    try:
        history_model._meta.get_field(name)
    except Exception:  # noqa: BLE001 - the history copy names the pk differently
        return False
    return True


def _origin_run_key(changeset: Any) -> str | None:
    """The run kind's key prefix when the changeset was made by a workflow run."""

    if changeset.origin_content_type_id is None or not apps.is_installed("angee.workflows"):
        return None
    origin_model = changeset.origin_content_type.model_class()
    if origin_model is None or origin_model._meta.label != RUN_MODEL:
        return None
    run = origin_model._base_manager.filter(pk=changeset.origin_object_id).first()
    return f"{RUN_KIND}:{public_id_of(run)}" if run is not None else None


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (ChangeKind(),)
