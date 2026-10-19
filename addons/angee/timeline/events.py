"""Keep the ledger current: project a source row's events and upsert them by key."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models, transaction
from rebac import system_context

from angee.identity import public_id_of
from angee.refs import generic_pointer_target
from angee.timeline.kinds import EventData, TimelineKind, timeline_kinds

log = logging.getLogger(__name__)



def kinds_for(model: type[models.Model]) -> tuple[TimelineKind, ...]:
    """The kinds a save of ``model`` re-projects."""

    label = model._meta.concrete_model._meta.label
    return tuple(kind for kind in timeline_kinds() if label in kind.models)


def sync_events(row: models.Model, *, kinds: Iterable[TimelineKind] | None = None) -> int:
    """Project ``row`` through every kind it feeds and reconcile the ledger.

    Returns how many events were created, changed or removed. Runs inside the
    caller's transaction, so the ledger and the owner's row commit together.
    """

    event_model = apps.get_model("timeline", "Event")
    changed = 0
    with system_context(reason="timeline.sync"), transaction.atomic():
        for kind in kinds if kinds is not None else kinds_for(type(row)):
            projected = list(kind.events(row))
            # The row itself is always a reconciled target, so a row that now
            # projects nothing (trashed, withdrawn) loses its events.
            changed += _reconcile(event_model, kind, projected, extra_targets={target_ref(row)})
    return changed


def _reconcile(
    event_model: Any,
    kind: TimelineKind,
    projected: list[EventData],
    *,
    extra_targets: set[tuple[str, str]] | frozenset[tuple[str, str]] = frozenset(),
) -> int:
    """Upsert the projected events of one kind and drop the stale ones for the same targets."""

    changed = 0
    by_key = {data.key: data for data in projected}
    targets = {(data.target_model, data.target_id) for data in projected} | set(extra_targets)
    candidates = event_model.objects.filter(kind=kind.id).filter(_targets_q(targets) | models.Q(key__in=list(by_key)))
    existing = {event.key: event for event in candidates}
    # A parent may belong to another kind (a decision folds under its run): read it from the ledger.
    parent_keys = {data.parent_key for data in projected if data.parent_key and data.parent_key not in existing}
    parents = {event.key: event for event in event_model.objects.filter(key__in=parent_keys)} if parent_keys else {}
    # Parents first, so a child's parent row exists when the child is written.
    ordered = sorted(projected, key=lambda data: (data.parent_key is not None, data.at, data.key))
    for data in ordered:
        values = _values(data, existing, parents)
        event = existing.get(data.key)
        if event is None:
            event = event_model.objects.create(key=data.key, kind=kind.id, **values)
            existing[data.key] = event
            changed += 1
        elif any(getattr(event, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(event, name, value)
            event.save(update_fields=list(values))
            changed += 1
        wanted = {pk for pk in data.needs if pk is not None}
        current = set(event.needs.values_list("pk", flat=True))
        if wanted != current:
            event.needs.set(wanted)
            changed += 1
    stale = [
        event.pk for key, event in existing.items()
        if key not in by_key and (event.target_model, event.target_id) in targets
    ]
    if stale:
        changed += event_model.objects.filter(pk__in=stale).delete()[0]
    return changed


def _targets_q(targets: set[tuple[str, str]]) -> models.Q:
    q = models.Q(pk__in=())
    for target_model, target_id in targets:
        q |= models.Q(target_model=target_model, target_id=target_id)
    return q


def _values(data: EventData, existing: dict[str, Any], parents: dict[str, Any]) -> dict[str, Any]:
    content_type, object_id = generic_pointer_target(data.record)
    parent = (existing.get(data.parent_key) or parents.get(data.parent_key)) if data.parent_key else None
    if data.parent_key and parent is None:
        log.warning("timeline: %s folds under %s, which is not in the ledger", data.key, data.parent_key)
    return {
        "at": data.at,
        "record_content_type_id": content_type.pk,
        "record_object_id": object_id,
        "target_model": data.target_model,
        "target_id": data.target_id,
        "actor": data.actor,
        "actor_label": data.actor_label,
        "title": data.title[:500],
        "detail": data.detail[:500],
        "parent_id": parent.pk if parent is not None else None,
    }


def target_ref(row: models.Model) -> tuple[str, str]:
    """A source row as an event target: its concrete model label and public id."""

    return row._meta.concrete_model._meta.label, public_id_of(row)
