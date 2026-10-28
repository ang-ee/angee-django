"""Invalidate record reads on source changes; no projection or background work."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from django.db import models, transaction
from rebac import system_context

from angee.graphql.events import ChangePayload
from angee.graphql.publishing import broadcast_related_records, changes_muted
from angee.base.models.merge import MergeableMixin
from angee.base.refs import generic_pointer_model
from angee.timeline.kinds import TimelineKind, timeline_kinds

log = logging.getLogger(__name__)


def kinds_for(model: type[models.Model]) -> tuple[TimelineKind, ...]:
    return tuple(kind for kind in timeline_kinds() if kind.observes(model))


def _records(row: Any, *, update_fields: Iterable[str] | None = None) -> list[models.Model]:
    """Capture affected identities now; Django sends invalidations only after commit.

    Native pre-save captures old membership on the instance. Its publication
    adds new membership after the write; Django's callback queue discards
    invalidations on rollback.
    """
    if changes_muted():
        return []
    using = row._state.db
    records = []
    for kind in kinds_for(type(row)):
        if not kind.relevant(type(row), update_fields):
            continue
        try:
            with transaction.atomic(using=using), system_context(reason="timeline.invalidate"):
                grouped: dict[type[models.Model], set[int]] = {}
                for target in kind.targets(row):
                    for model, pk in kind.records(target):
                        grouped.setdefault(generic_pointer_model(model), set()).add(pk)
                for model, ids in grouped.items():
                    canonical = set(model.canonical_ids(ids, using=using).values()) \
                        if issubclass(model, MergeableMixin) else ids
                    for pk in canonical:
                        record = model(pk=pk)
                        record._state.db = using
                        records.append(record)
        except Exception:
            log.exception("Timeline invalidation failed: %s %s:%s", kind.id, row._meta.label, row.pk)
    return records


def before_save(sender: Any, *, instance: Any, raw: bool = False, update_fields: Iterable[str] | None = None,
                using: str, **kwargs: Any) -> None:
    if raw or changes_muted() or instance._state.adding:
        return
    if not any(kind.relevant(sender, update_fields) for kind in kinds_for(sender)):
        return
    previous = sender._base_manager.using(using).filter(pk=instance.pk).first()
    if previous is not None:
        instance._timeline_previous_records = _records(previous, update_fields=update_fields)


def before_delete(sender: Any, *, instance: Any, **kwargs: Any) -> None:
    broadcast_related_records(_records(instance))


def queue_change(sender: Any, *, payload: ChangePayload, **kwargs: Any) -> None:
    if payload.instance is not None and payload.action != "delete":
        previous = payload.instance.__dict__.pop("_timeline_previous_records", ())
        broadcast_related_records((*previous, *_records(payload.instance, update_fields=payload.changed_fields)))


def queue_updates(sender: Any, *, instances: Iterable[models.Model], update_fields: Iterable[str],
                  **kwargs: Any) -> None:
    for instance in instances:
        broadcast_related_records(_records(instance, update_fields=update_fields))
