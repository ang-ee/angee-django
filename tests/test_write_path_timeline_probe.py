"""Reviewer probe: TaskKind invalidations for mapped and unmapped Task saves."""
from __future__ import annotations

from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from rebac import actor_context, system_context

from angee.projects.testing.models import Task
from angee.timeline import events
from tests.chatterdemo.models import ChatterDoc
from tests.messaging_campaign import comment_record as comment_record

pytestmark = [
    pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("activity_catalog", "composed_permissions"),
]


@pytest.fixture(autouse=True)
def kinds(settings):
    settings.ANGEE_TIMELINE_KINDS = ["angee.projects.timeline.timeline_kinds"]


def test_probe_task_kind_invalidations(comment_record, monkeypatch) -> None:
    record, writer = comment_record.record, comment_record.writer
    seen: list[Any] = []
    monkeypatch.setattr(events, "broadcast_related_records",
                        lambda rows: seen.append(sorted((type(r).__name__, r.pk) for r in rows)))
    with actor_context(writer):
        task = Task.objects.schedule(record.with_actor(writer), title="Probe", assignee=None)
    with system_context(reason="tests.write_path.second"):
        other = ChatterDoc.objects.create(title="Second record")
    out = {}
    with system_context(reason="tests.write_path.tl"):
        for label, mutate, fields in (
            ("title_partial", lambda t: setattr(t, "title", "Probe 2"), {"title"}),
            ("note_partial", lambda t: setattr(t, "note", "x"), {"note"}),
            ("full_unchanged", lambda t: None, None),
            ("subject_move_partial", lambda t: (setattr(t, "subject_content_type",
                                                ContentType.objects.get_for_model(ChatterDoc)),
                                                setattr(t, "subject_object_id", other.pk)),
             {"subject_content_type", "subject_object_id"}),
            ("subject_back_full", lambda t: setattr(t, "subject_object_id", record.pk), None),
        ):
            seen.clear()
            row = Task._base_manager.get(pk=task.pk)
            mutate(row)
            row.save(update_fields=fields)
            out[label] = [item for batch in seen for item in batch]
    print(f"RV TaskKind invalidations (record={record.pk}, other={other.pk}): {out}")
