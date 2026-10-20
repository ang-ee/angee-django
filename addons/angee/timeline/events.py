"""Reconcile committed target projections, independently of the writing transaction."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import chain, islice
from typing import Any
from weakref import WeakKeyDictionary

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from rebac import system_context

from angee.graphql.events import ChangePayload
from angee.graphql.publishing import broadcast_related_records, changes_muted
from angee.jobs.enqueue import enqueue_task
from angee.models.merge import MergeableMixin
from angee.refs import generic_pointer_model
from angee.serialization import strip_null_bytes
from angee.timeline.kinds import EventData, RecordIdentity, TimelineKind, timeline_kinds

log = logging.getLogger(__name__)
TARGET_BATCH_SIZE = 100


def target_ref(row: models.Model) -> tuple[str, int]:
    """The persisted target identity, independent of its public representation."""

    return row._meta.concrete_model._meta.label, int(row.pk)


def kinds_for(model: type[models.Model]) -> tuple[TimelineKind, ...]:
    """Kinds observing this source model through the change seam."""

    return tuple(kind for kind in timeline_kinds() if kind.observes(model))


def stored_text(model: Any, name: str, value: str) -> str:
    """Apply the ledger field's storage bound and the shared null-byte rule once."""

    return str(strip_null_bytes(value))[:model._meta.get_field(name).max_length]


def _records(projected: Iterable[EventData], using: str | None) -> dict[RecordIdentity, tuple[int, int]]:
    """Resolve each distinct record once through the generic-pointer and merge owners."""

    grouped: dict[type[models.Model], set[int]] = {}
    identities = {data.record for data in projected}
    for model, pk in identities:
        grouped.setdefault(generic_pointer_model(model), set()).add(pk)
    result: dict[RecordIdentity, tuple[int, int]] = {}
    for model, ids in grouped.items():
        canonical = model.canonical_ids(ids, using=using) if issubclass(model, MergeableMixin) else {
            pk: pk for pk in model._base_manager.using(using).filter(pk__in=ids).values_list("pk", flat=True)
        }
        content_type = ContentType.objects.db_manager(using).get_for_model(generic_pointer_model(model))
        result.update(((model, pk), (content_type.pk, survivor)) for pk, survivor in canonical.items())
    return {identity: result[canonical_identity] for identity in identities
            if (canonical_identity := (generic_pointer_model(identity[0]), identity[1])) in result}


def _event(event_model: Any, data: EventData, record: tuple[int, int]) -> Any:
    content_type_id, object_id = record
    target_model, target_pk = target_ref(data.target)
    fold_model, fold_pk = target_ref(data.fold) if data.fold is not None else ("", None)
    text = {
        "kind": data.kind, "target_model": target_model, "occurrence": data.occurrence,
        "fold_model": fold_model, "actor": data.actor, "actor_label": data.actor_label,
        "title": data.title, "detail": data.detail,
    }
    values = {name: stored_text(event_model, name, value) for name, value in text.items()}
    for name in ("kind", "target_model", "occurrence", "fold_model", "actor"):
        if values[name] != text[name]:
            raise ValueError(f"Timeline {name} cannot be stored losslessly.")
    return event_model(
        **values, at=data.at, record_content_type_id=content_type_id, record_object_id=object_id,
        target_pk=target_pk, fold_pk=fold_pk,
    )


def sync_events(row: models.Model, *, kinds: Iterable[TimelineKind] | None = None) -> int:
    """Reconcile one target per kind; backfill uses the same committed-state path.

    Return the number of projected and deleted entries. Each kind runs in its own
    transaction; a live callback logs failure without undoing the owner's write.
    """

    written = 0
    for kind in kinds if kinds is not None else kinds_for(type(row)):
        with system_context(reason="timeline.sync"), transaction.atomic(using=row._state.db):
            target = (type(row)._base_manager.using(row._state.db).select_related(None)
                      .select_for_update(of=("self",)).filter(pk=row.pk).first())
            event_model = apps.get_model("timeline", "Event")
            events = event_model.objects.using(row._state.db)
            label, pk = target_ref(row)
            previous = set(events.filter(kind=kind.id, target_model=label, target_pk=pk).values_list(
                "record_content_type_id", "record_object_id",
            ))
            projected = [] if target is None else list(kind.events(target))
            records = _records(projected, row._state.db)
            by_identity = {}
            for data in projected:
                if data.kind != kind.id or target_ref(data.target) != (label, pk):
                    raise ValueError(f"{kind.id}: events must belong to the reconciled target.")
                if data.record not in records:
                    continue
                event = _event(event_model, data, records[data.record])
                identity = (event.record_content_type_id, event.record_object_id, event.occurrence)
                by_identity[identity] = event
            rows = events.bulk_create(
                list(by_identity.values()), update_conflicts=True,
                unique_fields=list(event_model.merge_identity[0]),
                update_fields=["at", "actor", "actor_label", "title", "detail", "fold_model", "fold_pk"],
            )
            removed, _ = events.filter(kind=kind.id, target_model=label, target_pk=pk).exclude(
                pk__in=[event.pk for event in rows],
            ).delete()
            written += len(rows) + removed
            record_rows = []
            for content_type_id, record_pk in previous | set(records.values()):
                record_model = ContentType.objects.db_manager(row._state.db).get_for_id(content_type_id).model_class()
                if record_model is not None:
                    record = record_model(pk=record_pk)
                    record._state.db = row._state.db
                    record_rows.append(record)
            broadcast_related_records(record_rows)
    return written


@dataclass(frozen=True)
class _Sync:
    """One target identity in the transaction's pending set."""

    kind: TimelineKind
    model: type[models.Model]
    pk: int
    using: str

    def __call__(self) -> None:
        try:
            row = self.model(pk=self.pk)
            row._state.db = self.using
            sync_events(row, kinds=(self.kind,))
        except Exception:
            log.exception("Timeline projection failed: %s %s:%s", self.kind.id, self.model._meta.label, self.pk)


@dataclass
class _Batch:
    targets: tuple[_Sync, ...]
    background: bool
    pending: set[_Sync]
    executed: bool = False

    def __call__(self) -> None:
        if self.executed:
            return
        self.executed = True
        self.pending.difference_update(self.targets)
        if self.background:
            enqueue_task("timeline.sync_targets", kwargs={"targets": [
                (target.kind.id, target.model._meta.label, target.pk, target.using) for target in self.targets
            ]}, robust=True)
        else:
            for target in self.targets:
                target()


# Django replaces run_on_commit on rollback. Executed callbacks are no longer
# pending even while captureOnCommitCallbacks keeps them in that native list.
_pending: WeakKeyDictionary[transaction.Atomic, tuple[list, set[_Sync]]] = WeakKeyDictionary()


def _enqueue(targets: tuple[_Sync, ...], *, background: bool) -> None:
    using = targets[0].using
    connection = transaction.get_connection(using)
    if not connection.in_atomic_block:
        _Batch(tuple(dict.fromkeys(targets)), background, set())()
        return
    boundary = connection.atomic_blocks[0]
    pending = _pending.get(boundary)
    if pending is None or pending[0] is not connection.run_on_commit:
        keys = pending[1] if pending is not None else set()
        keys.clear()
        keys.update({
            target for _, queued, _ in connection.run_on_commit
            if isinstance(queued, _Batch) and not queued.executed for target in queued.targets
        })
        pending = (connection.run_on_commit, keys)
        _pending[boundary] = pending
    fresh = tuple(target for target in dict.fromkeys(targets) if target not in pending[1])
    if fresh:
        pending[1].update(fresh)
        transaction.on_commit(_Batch(fresh, background, pending[1]), using=using, robust=True)


def queue_change(sender: Any, *, payload: ChangePayload, **kwargs: Any) -> None:
    """Resolve source dependencies now and enqueue each target once after commit.

    The publisher supplies the source row before delete clears its identity.
    Django's callback queue owns savepoint rollback, so a rolled-back entry never suppresses a retry.
    Dependency discovery has its own savepoint; even a database error cannot poison
    the owner's transaction or starve another kind.
    """

    row = payload.instance
    if row is None:
        return
    using = row._state.db or type(row)._base_manager.db_manager(hints={"instance": row}).db
    for kind in kinds_for(sender):
        if not kind.relevant(sender, payload.changed_fields):
            continue
        try:
            with system_context(reason="timeline.targets"), transaction.atomic(using=using):
                targets = kind.targets(row)
                _queue_targets(kind, targets, using)
        except Exception:
            log.exception("Timeline target discovery failed: %s %s:%s", kind.id, sender._meta.label, row.pk)


def queue_updates(sender: Any, *, instances: Iterable[models.Model], update_fields: Iterable[str], using: str,
                  **kwargs: Any) -> None:
    """Observe native batches whose owner coalesces row publications to other models."""

    if changes_muted():
        return
    rows = tuple(instances)
    for kind in kinds_for(sender):
        if not kind.relevant(sender, update_fields):
            continue
        try:
            with system_context(reason="timeline.targets"), transaction.atomic(using=using):
                _queue_targets(kind, chain.from_iterable(kind.targets(row) for row in rows), using)
        except Exception:
            log.exception("Timeline batch discovery failed: %s %s", kind.id, sender._meta.label)


def _queue_targets(kind: TimelineKind, targets: Iterable[models.Model], using: str) -> None:
    if isinstance(targets, models.QuerySet):
        model = targets.model
        targets = (model(pk=pk) for pk in targets.values_list("pk", flat=True)
                   .iterator(chunk_size=TARGET_BATCH_SIZE))

    def distinct() -> Iterable[_Sync]:
        seen: set[_Sync] = set()
        for target in targets:
            callback = _Sync(kind, type(target), int(target.pk), using)
            if callback not in seen:
                seen.add(callback)
                yield callback

    iterator = iter(distinct())
    first = tuple(islice(iterator, TARGET_BATCH_SIZE + 1))
    background = len(first) > TARGET_BATCH_SIZE
    iterator = chain(first, iterator)
    while batch := tuple(islice(iterator, TARGET_BATCH_SIZE)):
        _enqueue(batch, background=background)
