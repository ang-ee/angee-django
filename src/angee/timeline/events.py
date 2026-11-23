"""Invalidate only timeline reads on source changes, after commit."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from functools import partial
from typing import Any

from django.db import models, transaction
from rebac import system_context

from angee.base.models.merge import MergeableMixin
from angee.base.permissions import links_narrowed
from angee.base.refs import RecordRef, generic_pointer_model, record_view_refs
from angee.base.signals import ModelChange, changes_muted
from angee.graphql.publishing import broadcast_group
from angee.timeline.kinds import TimelineKind, timeline_kinds

log = logging.getLogger(__name__)


def timeline_group(record: RecordRef) -> str:
    """One timeline stream per record view, independent of model change feeds."""

    return f"angee.timeline.{record.model_label.lower()}.{record.public_id}"


def broadcast_timeline_changes(records: Iterable[models.Model], *, rows: Iterable[tuple[str, Any]] = ()) -> None:
    """Invalidate each affected timeline, including inherited views, on commit.

    No source details or synthetic record updates enter a model change feed.
    Subscribers recheck the timeline read's record authorization on delivery.
    ``rows`` names the changed source rows (model label and key) on the server's
    channel only; a subscriber is then told only when REBAC lets it read one of
    them. Naming none tells every reader of the record.
    """

    changed = sorted({(label, str(pk)) for label, pk in rows})
    for record in record_view_refs(*(RecordRef.from_instance(row) for row in records)):
        payload: dict[str, Any] = {"model": record.model_label, "id": record.public_id}
        if changed:
            payload["rows"] = [list(row) for row in changed]
        transaction.on_commit(partial(broadcast_group, timeline_group(record), payload), robust=True)


def kinds_for(model: type[models.Model]) -> tuple[TimelineKind, ...]:
    return tuple(kind for kind in timeline_kinds() if kind.observes(model))


def _records(
    row: Any, *, update_fields: Iterable[str] | None = None,
) -> list[tuple[models.Model, frozenset[tuple[str, Any]]]]:
    """Capture affected identities now, each with the changed source rows that hold its events.

    Django sends invalidations only after commit. Native pre-save captures old
    membership on the instance. Its publication adds new membership after the
    write; Django's callback queue discards invalidations on rollback. The source
    rows are captured only under the narrowed policy (:func:`_broadcast`).
    """
    if changes_muted():
        return []
    sourced = links_narrowed()
    records: list[tuple[models.Model, frozenset[tuple[str, Any]]]] = []
    for kind in kinds_for(type(row)):
        if not kind.relevant(type(row), update_fields):
            continue
        try:
            with transaction.atomic(), system_context(reason="timeline.invalidate"):
                grouped: dict[type[models.Model], set[int]] = {}
                sources: dict[tuple[type[models.Model], Any], set[tuple[str, Any]]] = {}
                for target in kind.targets(row):
                    for model, pk in kind.records(target):
                        grouped.setdefault(generic_pointer_model(model), set()).add(pk)
                        if sourced:
                            sources.setdefault((generic_pointer_model(model), pk), set()).add(
                                (target._meta.label, target.pk),
                            )
                for model, ids in grouped.items():
                    merged = model.canonical_ids(ids) if issubclass(model, MergeableMixin) else None
                    canonical = set(merged.values()) if merged is not None else ids
                    held: dict[Any, set[tuple[str, Any]]] = {}
                    for pk in ids if sourced else ():
                        survivor = merged.get(pk, pk) if merged is not None else pk
                        held.setdefault(survivor, set()).update(sources[(model, pk)])
                    records.extend((model(pk=pk), frozenset(held.get(pk, ()))) for pk in canonical)
        except Exception:
            log.exception("Timeline invalidation failed: %s %s:%s", kind.id, row._meta.label, row.pk)
    return records


def _broadcast(changes: Iterable[tuple[models.Model, frozenset[tuple[str, Any]]]]) -> None:
    """Broadcast captured changes; under the narrowed policy each timeline is told which rows changed.

    With the published policy a record's readers read the rows of its timeline,
    so a change tells the timeline only that it changed. Under
    ``REBAC_INHERIT_THROUGH_LINKS = False`` a reader of the record may not read a
    row reached from it through a link (a comment, a task about it), so the
    payload names the changed rows and REBAC decides, per subscriber, whether it
    reads one.
    """

    changes = list(changes)
    if not links_narrowed():
        broadcast_timeline_changes([record for record, _sources in changes])
        return
    by_record: dict[tuple[str, Any], tuple[models.Model, set[tuple[str, Any]]]] = {}
    for record, sources in changes:
        by_record.setdefault((record._meta.label, record.pk), (record, set()))[1].update(sources)
    for record, rows in by_record.values():
        broadcast_timeline_changes([record], rows=rows)


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
    _broadcast(_records(instance))


def queue_change(sender: Any, *, payload: ModelChange, **kwargs: Any) -> None:
    if payload.action != "delete":
        previous = payload.instance.__dict__.pop("_timeline_previous_records", ())
        _broadcast((*previous, *_records(payload.instance, update_fields=payload.changed_fields)))


def queue_updates(sender: Any, *, instances: Iterable[models.Model], update_fields: Iterable[str],
                  **kwargs: Any) -> None:
    for instance in instances:
        _broadcast(_records(instance, update_fields=update_fields))
