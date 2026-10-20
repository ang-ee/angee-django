"""Bounded, identity-only fan-out through the same committed reconciliation."""

from celery import shared_task
from django.apps import apps

from angee.timeline.events import _Sync
from angee.timeline.kinds import timeline_kinds


@shared_task(name="timeline.sync_targets")
def sync_targets(targets: list[tuple[str, str, int, str]]) -> None:
    kinds = {kind.id: kind for kind in timeline_kinds()}
    for kind_id, label, pk, using in targets:
        if kind_id in kinds:
            _Sync(kinds[kind_id], apps.get_model(label), pk, using)()
