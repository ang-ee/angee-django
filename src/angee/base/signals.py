"""Per-model receiver binding and the post-migrate receiver guard."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from django.apps import apps
from django.db import connections, router
from django.db.models import Model
from django.db.models.signals import class_prepared, post_delete, post_save
from django.dispatch import Signal

logger = logging.getLogger(__name__)

pre_merge = Signal()
"""Enter addon-owned contexts around one record fold (survivor, merged, join, contexts).

``merged`` is the deterministic tuple of locked source rows being folded;
``survivor`` is their locked destination. ``contexts`` is the fold's ExitStack.
Receivers may enter a context there;
its cleanup runs after the fold, including when validation or a write fails.
"""

bulk_updated = Signal()
"""An owning bulk verb changed a batch of rows (instances, update_fields, using).

Observers receive persisted rows without pretending they were individually saved:
the queryset already owns history and authorization for the write.
"""

record_access_revoked = Signal()
"""A direct record relation was revoked (instance, relation, subject, using).

The subject is the normalized native SubjectRef. Receivers run synchronously
inside the relationship write transaction, including for an absent tuple.
"""


def send_created_batch(
    *, sender: type[Model], instances: Iterable[Model], using: str, **kwargs: Any,
) -> None:
    """Send every native creation event, carrying the immutable persisted batch.

    Batch-aware observers act on its final send; all ordinary receivers still
    receive each instance. Callers finish initialization and actor binding first.
    """

    rows = tuple(instances)
    for instance in rows:
        post_save.send(
            sender=sender, instance=instance, created=True, raw=False, using=using,
            update_fields=None, created_batch=rows, **kwargs,
        )


def migrated_model(*, app_config: object, using: str, app_label: str, model_name: str) -> type[Model] | None:
    """Return the model a ``post_migrate`` receiver may work on now, or ``None``.

    Django sends ``post_migrate`` for every app on every migrate, test databases
    included, so a receiver acts once: on its own app's signal, for the connection
    the router writes the model to, and only while migrations have created the
    model's table (not after migrating back past it).
    """

    if getattr(app_config, "label", "") != app_label:
        return None
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None
    if using != router.db_for_write(model) or not router.allow_migrate_model(using, model):
        return None
    return model if has_table(using, model) else None


def has_table(using: str, model: type[Model]) -> bool:
    """Return whether migrations have created ``model``'s table on the ``using`` connection."""

    connection = connections[using]
    with connection.cursor() as cursor:
        return model._meta.db_table in connection.introspection.table_names(cursor)


def connect_for_models(
    signal: Signal,
    receiver: Callable[..., Any],
    *,
    applies: Callable[[type[Model]], bool],
    dispatch_uid: str,
    prepare: Callable[[type[Model]], None] | None = None,
) -> None:
    """Connect ``receiver`` to each concrete model ``applies`` admits, now and as models prepare.

    A model signal receiver connected without a sender listens to every model, and a
    delete receiver of that kind stops Django from deleting any table's rows in bulk:
    the collector loads each row to send it. Owners bind to the models they concern
    instead. Proxies bind too, since a delete through a proxy queryset sends the proxy.
    Historical migration models never bind.
    """

    def bind(model: type[Model]) -> None:
        if model._meta.abstract or model._meta.apps is not apps or not applies(model):
            return
        if prepare is not None:
            prepare(model)
        signal.connect(receiver, sender=model, dispatch_uid=f"{dispatch_uid}.{model._meta.label_lower}")

    for model in apps.get_models():
        bind(model)
    # Models prepared after app population (test-defined records, for one) bind as they finalize.
    class_prepared.connect(
        lambda sender, **_kwargs: bind(sender), dispatch_uid=f"{dispatch_uid}.class_prepared", weak=False,
    )


@dataclass(frozen=True, slots=True)
class ModelChange:
    """One synchronous local write observation, independent of any transport.

    The live instance remains available for deletes. Occurrence identity and
    the database alias survive projection; wire values and access decisions belong
    to each enabled consumer, rather than to this observation.
    """

    model: str
    id: str
    action: str
    occurrence_id: str
    changed_fields: tuple[str, ...] | None
    instance: Model
    using: str | None = None
    changed_values: Mapping[str, Any] | None = None
    """Native concrete field values captured before any local receiver runs."""
    during_ingestion: bool = False
    broadcasts: bool = True
    """Whether the owner requests transport; each adapter applies its own policy."""


change_published = Signal()
"""Robust synchronous local observation, inside the owning write transaction.

Receivers get ``sender`` and ``payload: ModelChange``. Queue external effects
with native ``transaction.on_commit``; receiver errors never starve later observers.
"""

change_observed = Signal()
"""Local observation completed, after every ``change_published`` receiver.

Adapters project and enqueue transport here, after local observers have queued
commit effects. This is a distinct lifecycle boundary, not another write event.
"""

_change_mute_depth: ContextVar[int] = ContextVar("model_change_mute_depth", default=0)
_change_ingestion_depth: ContextVar[int] = ContextVar("model_change_ingestion_depth", default=0)


def changes_muted() -> bool:
    """Whether the current owner suppresses local observation and transport."""
    return _change_mute_depth.get() > 0


@contextmanager
def mute_changes() -> Iterator[None]:
    """Suppress row observations in a bounded, reentrant teardown context."""
    token = _change_mute_depth.set(_change_mute_depth.get() + 1)
    try:
        yield
    finally:
        _change_mute_depth.reset(token)


@contextmanager
def publication_ingestion_context() -> Iterator[None]:
    """Annotate local writes as ingestion without suppressing observation."""
    token = _change_ingestion_depth.set(_change_ingestion_depth.get() + 1)
    try:
        yield
    finally:
        _change_ingestion_depth.reset(token)


def publication_ingestion_active() -> bool:
    """Return the ingestion annotation captured by local write observers."""
    return _change_ingestion_depth.get() > 0


def publish_change(instance: Model, *, action: str, update_fields: Iterable[str] | None,
                   broadcast: bool = True) -> None:
    """Observe one owner write, then notify enabled adapters after local delivery."""
    if changes_muted():
        return
    # Signal owners are imported by model bases during class construction.
    from angee.base.models.identity import public_id_of

    fields = tuple(sorted(str(name) for name in update_fields)) if update_fields is not None else None
    values = None
    if fields is not None:
        columns = {name: field for field in instance._meta.concrete_fields for name in (field.name, field.attname)}
        values = MappingProxyType({
            name: deepcopy(getattr(instance, columns[name].attname)) for name in fields if name in columns
        })
    payload = ModelChange(
        model=instance._meta.label, id=public_id_of(instance), action=action, occurrence_id=uuid4().hex,
        changed_fields=fields, instance=instance, using=instance._state.db, changed_values=values,
        during_ingestion=publication_ingestion_active(), broadcasts=broadcast,
    )
    for signal, name in ((change_published, "change_published"), (change_observed, "change_observed")):
        for receiver, response in signal.send_robust(sender=type(instance), payload=payload):
            if isinstance(response, Exception):
                logger.error(
                    "%s receiver %r failed for %s %s.", name, receiver, payload.model, payload.id,
                    exc_info=(type(response), response, response.__traceback__),
                )


def connect_observers(model: type[Model], *, include_parents: bool = False) -> None:
    """Bind native save/delete/bulk observations, optionally including inherited views."""
    uid = f"angee-changes-{model._meta.label}"
    bulk_updated.connect(publish_bulk_update, sender=model, weak=False, dispatch_uid=f"{uid}-bulk")
    receiver = partial(_on_model_save, published_model=model)
    for sender in (model, *model._meta.get_parent_list()) if include_parents else (model,):
        post_save.connect(
            receiver, sender=sender, weak=False,
            dispatch_uid=f"{uid}-save" if sender is model else f"{uid}-save-{sender._meta.label}",
        )
    post_delete.connect(_on_model_delete, sender=model, weak=False, dispatch_uid=f"{uid}-delete")


def disconnect_observers(model: type[Model]) -> bool:
    """Disconnect a model's native write observations and inherited view mirrors."""
    uid = f"angee-changes-{model._meta.label}"
    removed = post_save.disconnect(sender=model, dispatch_uid=f"{uid}-save")
    for parent in model._meta.get_parent_list():
        removed = post_save.disconnect(sender=parent, dispatch_uid=f"{uid}-save-{parent._meta.label}") or removed
    removed = post_delete.disconnect(sender=model, dispatch_uid=f"{uid}-delete") or removed
    removed = bulk_updated.disconnect(sender=model, dispatch_uid=f"{uid}-bulk") or removed
    return bool(removed)


def _on_model_save(sender: type[Model], instance: Model, *, created: bool = False,
                   update_fields: Iterable[str] | None = None, raw: bool = False,
                   published_model: type[Model], created_batch: tuple[Model, ...] | None = None,
                   **kwargs: Any) -> None:
    """Reload an inherited view once on the batch's final native creation signal."""
    if raw or changes_muted():
        return
    instances: Iterable[Model] = (instance,)
    if type(instance) is not published_model:
        sources = created_batch if created_batch is not None else (instance,)
        if not sources or instance is not sources[-1]:
            return
        rows = published_model._base_manager.db_manager(instance._state.db).in_bulk(row.pk for row in sources)
        instances = (rows[row.pk] for row in sources if row.pk in rows)
        created = False
    for row in instances:
        publish_change(row, action="create" if created else "update", update_fields=update_fields)


def _on_model_delete(sender: type[Model], instance: Model, **kwargs: Any) -> None:
    """Observe deletion while the row's source identity is still available."""
    publish_change(instance, action="delete", update_fields=None)


def publish_bulk_update(sender: type[Model], *, instances: Iterable[Model], update_fields: Iterable[str],
                    broadcast: bool = True, **kwargs: Any) -> None:
    """Project an owner's bounded batch notification without faking save signals."""
    if changes_muted():
        return
    coalesce = getattr(sender, "change_batch_targets", None)
    fields: Iterable[str] | None = update_fields
    if coalesce is not None:
        instances = coalesce(instances, update_fields=update_fields)
        fields = None
    for instance in instances:
        publish_change(instance, action="update", update_fields=fields, broadcast=broadcast)
