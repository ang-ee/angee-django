"""Invalidate only timeline reads on source changes, after commit."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from functools import partial
from typing import Any

from django.db import models, transaction
from rebac import system_context

from angee.base.models.merge import MergeableMixin
from angee.base.refs import RecordRef, generic_pointer_model, record_view_refs
from angee.base.signals import ModelChange, changes_muted
from angee.graphql.publishing import broadcast_group
from angee.timeline.kinds import TimelineKind, timeline_kinds

log = logging.getLogger(__name__)


def timeline_group(record: RecordRef) -> str:
    """One timeline stream per record view, independent of model change feeds."""

    return f"angee.timeline.{record.model_label.lower()}.{record.public_id}"


def broadcast_timeline_changes(records: Iterable[models.Model]) -> None:
    """Invalidate each affected timeline, including inherited views, on commit.

    No source details or synthetic record updates enter a model change feed.
    Subscribers recheck the timeline read's record authorization on delivery.
    """

    for record in record_view_refs(*(RecordRef.from_instance(row) for row in records)):
        transaction.on_commit(partial(
            broadcast_group, timeline_group(record), {"model": record.model_label, "id": record.public_id},
        ), robust=True)


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
    records: list[models.Model] = []
    for kind in kinds_for(type(row)):
        if not kind.relevant(type(row), update_fields):
            continue
        try:
            with transaction.atomic(), system_context(reason="timeline.invalidate"):
                grouped: dict[type[models.Model], set[int]] = {}
                for target in kind.targets(row):
                    for model, pk in kind.records(target):
                        grouped.setdefault(generic_pointer_model(model), set()).add(pk)
                for model, ids in grouped.items():
                    canonical = set(model.canonical_ids(ids).values()) if issubclass(model, MergeableMixin) else ids
                    records.extend(model(pk=pk) for pk in canonical)
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
    broadcast_timeline_changes(_records(instance))


def queue_change(sender: Any, *, payload: ModelChange, **kwargs: Any) -> None:
    if payload.action != "delete":
        previous = payload.instance.__dict__.pop("_timeline_previous_records", ())
        broadcast_timeline_changes((*previous, *_records(payload.instance, update_fields=payload.changed_fields)))


def queue_updates(sender: Any, *, instances: Iterable[models.Model], update_fields: Iterable[str],
                  **kwargs: Any) -> None:
    for instance in instances:
        broadcast_timeline_changes(_records(instance, update_fields=update_fields))
