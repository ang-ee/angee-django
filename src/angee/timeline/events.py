"""Invalidate record reads on source changes; no projection or background work."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from django.db import models, transaction
from rebac import database_context, system_context

from angee.base.refs import generic_pointer_model
from angee.base.signals import ModelChange, changes_muted
from angee.graphql.publishing import broadcast_related_records
from angee.iam.models.merge import ProtectedMergeableMixin
from angee.timeline.kinds import TimelineKind, timeline_kinds

log = logging.getLogger(__name__)
_suppress_invalidation: ContextVar[bool] = ContextVar("timeline_suppress_invalidation", default=False)


@contextmanager
def suppress_invalidation() -> Iterator[None]:
    """Skip timeline invalidation while preserving each model's change publication."""
    token = _suppress_invalidation.set(True)
    try:
        yield
    finally:
        _suppress_invalidation.reset(token)


def kinds_for(model: type[models.Model]) -> tuple[TimelineKind, ...]:
    return tuple(kind for kind in timeline_kinds() if kind.observes(model))


def _records(
    row: Any, *, previous: Any = None, update_fields: Iterable[str] | None = None,
) -> list[models.Model]:
    """Capture affected identities now; Django sends invalidations only after commit.

    Native pre-save captures the old row. Publication collects old and new
    membership together after the write; Django discards callbacks on rollback.
    """
    if changes_muted() or _suppress_invalidation.get():
        return []
    using = row._state.db
    records = []
    for kind in kinds_for(type(row)):
        current_relevant = kind.relevant(type(row), update_fields)
        previous_relevant = previous is not None and kind.previous_relevant(type(row), update_fields)
        if not current_relevant and not previous_relevant:
            continue
        try:
            with transaction.atomic(using=using), database_context(using), system_context(reason="timeline.invalidate"):
                grouped: dict[type[models.Model], set[int]] = {}
                for source in (previous, row):
                    if source is None or (source is previous and not previous_relevant) or (
                        source is row and not current_relevant
                    ):
                        continue
                    for target in kind.targets(source):
                        for model, pk in kind.records(target):
                            grouped.setdefault(generic_pointer_model(model), set()).add(pk)
                for model, ids in grouped.items():
                    canonical = (
                        set(model.canonical_ids(ids, using=using).values())
                        if issubclass(model, ProtectedMergeableMixin)
                        else ids
                    )
                    for pk in canonical:
                        record = model(pk=pk)
                        record._state.db = using
                        records.append(record)
        except Exception:
            log.exception("Timeline invalidation failed: %s %s:%s", kind.id, row._meta.label, row.pk)
    return records


def before_save(
    sender: Any,
    *,
    instance: Any,
    raw: bool = False,
    update_fields: Iterable[str] | None = None,
    using: str,
    **kwargs: Any,
) -> None:
    instance.__dict__.pop("_timeline_previous_source", None)
    if raw or changes_muted() or instance._state.adding:
        return
    if not any(kind.previous_relevant(sender, update_fields) for kind in kinds_for(sender)):
        return
    kinds = kinds_for(sender)
    columns = {column for kind in kinds for column in kind.observed_fields(sender)}
    previous_rows = sender._base_manager.using(using).filter(pk=instance.pk)
    if columns and all(isinstance(kind.source_models, dict) for kind in kinds):
        previous_rows = previous_rows.only(*columns)
    previous = previous_rows.first()
    if previous is not None:
        instance._timeline_previous_source = previous


def before_delete(sender: Any, *, instance: Any, **kwargs: Any) -> None:
    broadcast_related_records(_records(instance))


def queue_change(sender: Any, *, payload: ModelChange, **kwargs: Any) -> None:
    if payload.instance is not None and payload.action != "delete":
        previous = payload.instance.__dict__.pop("_timeline_previous_source", None)
        broadcast_related_records(_records(payload.instance, previous=previous, update_fields=payload.changed_fields))


def queue_updates(
    sender: Any, *, instances: Iterable[models.Model], update_fields: Iterable[str], **kwargs: Any
) -> None:
    for instance in instances:
        broadcast_related_records(_records(instance, update_fields=update_fields))
