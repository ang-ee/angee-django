"""Model-lifecycle signals, per-model receiver binding and write observation.

Write observation is transport-neutral: an owner's write becomes one
:class:`ModelChange`, delivered robustly to local observers
(:data:`change_published`) and then to adapters (:data:`change_observed`),
such as GraphQL's change feeds, inside the writing transaction. An adapter's
failure fails the write; its delivery is queued robustly on commit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from django.apps import AppConfig, apps
from django.db import connections, router
from django.db.models import Model
from django.db.models.signals import class_prepared, post_delete, post_save
from django.dispatch import Signal

logger = logging.getLogger(__name__)

pre_merge = Signal()
"""Enter addon-owned contexts around one record fold (survivor, join, contexts).

``contexts`` is the fold's ExitStack. Receivers may enter a context there;
its cleanup runs after the fold, including when validation or a write fails.
"""

bulk_updated = Signal()
"""An owning bulk verb changed a batch of rows (instances, update_fields).

Observers receive persisted rows without pretending they were individually saved:
the queryset already owns history and authorization for the write.
"""


def send_bulk_update(
    *, sender: type[Model], instances: Iterable[Model], update_fields: Iterable[str],
) -> None:
    """Deliver one persisted batch to every observer, logging receiver failures."""

    for receiver, response in bulk_updated.send_robust(
        sender=sender, instances=tuple(instances), update_fields=frozenset(update_fields),
    ):
        if isinstance(response, Exception):
            logger.error(
                "bulk_updated receiver %r failed for %s.", receiver, sender._meta.label,
                exc_info=(type(response), response, response.__traceback__),
            )


record_access_revoked = Signal()
"""A direct record relation was revoked (instance, relation, subject).

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


def migrated_model(*, app_config: AppConfig, using: str, app_label: str, model_name: str) -> type[Model] | None:
    """Return the model a ``post_migrate`` receiver may work on now, or ``None``.

    Django sends ``post_migrate`` for every app on every migrate, test databases
    included, so a receiver acts once: on its own app's signal, for the connection
    the router writes the model to, and only while migrations have created the
    model's table (not after migrating back past it).
    """

    if app_config.label != app_label:
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
    """One local write observation, independent of any transport.

    The live instance stays available, including for deletes. Wire values,
    read gates and related-record projection belong to each adapter.
    """

    model: str
    """Django model label of the written row."""

    id: str
    """Public identifier of the written row."""

    action: str
    """``create``, ``update`` or ``delete``."""

    occurrence_id: str
    """Identity of this observable change, shared by every adapter projection."""

    changed_fields: tuple[str, ...] | None
    """Saved fields of a partial update, sorted; ``None`` for a full write."""

    instance: Model
    """The written row; never serialized."""

    changed_values: Mapping[str, Any] | None = None
    """Concrete field values of ``changed_fields``, captured before any receiver runs."""

    broadcasts: bool = True
    """Whether the owner requests transport; each adapter still applies its own policy."""


change_published = Signal()
"""Sent robustly when an owner writes a row, inside its transaction.

Receivers get ``sender`` (the model class) and ``payload`` (:class:`ModelChange`).
Receiver exceptions are logged and never reach the writer or starve later
receivers. Observers queue external effects with ``transaction.on_commit``.
"""

change_observed = Signal()
"""Sent after every :data:`change_published` receiver ran, with the same arguments.

Transport adapters project and queue delivery here, so local observers' commit
effects run before transport. It is a later lifecycle point, not a second write.
A receiver's exception propagates to the writer: a projection that breaks its
contract, or a failing query it makes, aborts the write. Adapters queue their
delivery with ``transaction.on_commit(..., robust=True)``.
"""

_mute_depth: ContextVar[int] = ContextVar("angee_change_mute_depth", default=0)

_IMMUTABLE_VALUE_TYPES = frozenset({date, datetime, time, timedelta, Decimal, UUID})
"""Immutable field value types a deep copy would rebuild; builtin scalars it already returns as they are."""


def changes_muted() -> bool:
    """Whether the current owner suppresses local observation and transport."""

    return _mute_depth.get() > 0


@contextmanager
def mute_changes() -> Iterator[None]:
    """Suppress per-row change observation for the duration of the block.

    A bulk teardown deleting thousands of rows would otherwise observe each one,
    and every adapter would probe and queue transport per row. Reentrant and
    context-local: it never touches the shared signal registry, so it is safe
    under concurrent requests. A coarse change the caller still wants observed
    (the purged root's own row) runs outside the block.
    """

    token = _mute_depth.set(_mute_depth.get() + 1)
    try:
        yield
    finally:
        _mute_depth.reset(token)


def publish_change(
    instance: Model, *, action: str, update_fields: Iterable[str] | None, broadcast: bool = True,
) -> None:
    """Observe one owner write: local observers first, then transport adapters."""

    if changes_muted():
        return
    # models.identity declares models, which need a ready app registry; this
    # signals module stays importable before apps load.
    from angee.base.models.identity import public_id_of

    fields = tuple(sorted(str(name) for name in update_fields)) if update_fields is not None else None
    values = None
    if fields is not None:
        columns = {name: field for field in instance._meta.concrete_fields for name in (field.name, field.attname)}
        values = MappingProxyType({
            name: _captured_value(getattr(instance, columns[name].attname)) for name in fields if name in columns
        })
    payload = ModelChange(
        model=instance._meta.label, id=public_id_of(instance), action=action, occurrence_id=uuid4().hex,
        changed_fields=fields, instance=instance, changed_values=values, broadcasts=broadcast,
    )
    for receiver, response in change_published.send_robust(sender=type(instance), payload=payload):
        if isinstance(response, Exception):
            logger.error(
                "change_published receiver %r failed for %s %s.", receiver, payload.model, payload.id,
                exc_info=(type(response), response, response.__traceback__),
            )
    change_observed.send(sender=type(instance), payload=payload)


def _captured_value(value: Any) -> Any:
    """Return ``value`` as an observation captures it: a copy unless no receiver can mutate it."""

    return value if type(value) in _IMMUTABLE_VALUE_TYPES else deepcopy(value)


def publish_bulk_update(
    sender: type[Model], *, instances: Iterable[Model], update_fields: Iterable[str],
    broadcast: bool = True, **kwargs: Any,
) -> None:
    """Observe an owner's bounded batch write (:data:`bulk_updated`) row by row.

    An Angee model may coalesce the batch onto the rows its readers observe
    through its declared ``AngeeModel.change_batch_targets``; those rows are
    observed as full updates.
    """

    if changes_muted():
        return
    # models.base declares models, which need a ready app registry; this
    # signals module stays importable before apps load.
    from angee.base.models.base import AngeeModel

    coalesce = sender.change_batch_targets if issubclass(sender, AngeeModel) else None
    fields: Iterable[str] | None = update_fields
    if coalesce is not None:
        instances = coalesce(instances, update_fields=update_fields)
        fields = None
    for instance in instances:
        publish_change(instance, action="update", update_fields=fields, broadcast=broadcast)


def connect_observers(model: type[Model], *, include_parents: bool = False) -> None:
    """Observe ``model``'s native saves, deletes and bulk updates.

    With ``include_parents``, a write through an MTI parent class is observed as
    a write of ``model``'s row too. Connecting an already observed model is a
    no-op: one dispatch uid per model.
    """

    uid = f"angee-changes-{model._meta.label}"
    bulk_updated.connect(publish_bulk_update, sender=model, weak=False, dispatch_uid=f"{uid}-bulk")
    receiver = partial(_on_save, observed_model=model)
    for sender in (model, *model._meta.get_parent_list()) if include_parents else (model,):
        # Django sends only the saved class's signal, including parent writes.
        post_save.connect(
            receiver, sender=sender, weak=False,
            dispatch_uid=f"{uid}-save" if sender is model else f"{uid}-save-{sender._meta.label}",
        )
    post_delete.connect(_on_delete, sender=model, weak=False, dispatch_uid=f"{uid}-delete")


def disconnect_observers(model: type[Model]) -> bool:
    """Disconnect ``model``'s observation, including parent mirrors; return whether any was wired."""

    uid = f"angee-changes-{model._meta.label}"
    disconnected = post_save.disconnect(sender=model, dispatch_uid=f"{uid}-save")
    for parent in model._meta.get_parent_list():
        disconnected = post_save.disconnect(
            sender=parent, dispatch_uid=f"{uid}-save-{parent._meta.label}",
        ) or disconnected
    disconnected = post_delete.disconnect(sender=model, dispatch_uid=f"{uid}-delete") or disconnected
    disconnected = bulk_updated.disconnect(sender=model, dispatch_uid=f"{uid}-bulk") or disconnected
    return bool(disconnected)


def _on_save(
    sender: type[Model], instance: Model, *, observed_model: type[Model], created: bool = False,
    update_fields: Iterable[str] | None = None, raw: bool = False, using: str | None = None,
    created_batch: tuple[Model, ...] | None = None, **kwargs: Any,
) -> None:
    """Observe each row; an inherited view reads a creation batch once, on its final send.

    The view is read on the database Django saved to.
    """

    if raw:
        return
    instances: Iterable[Model] = (instance,)
    if type(instance) is not observed_model:
        sources = created_batch if created_batch is not None else (instance,)
        if not sources or instance is not sources[-1]:
            return
        rows = observed_model._base_manager.db_manager(using).in_bulk(row.pk for row in sources)
        instances = (rows[row.pk] for row in sources if row.pk in rows)
        created = False
    for row in instances:
        publish_change(row, action="create" if created else "update", update_fields=update_fields)


def _on_delete(sender: type[Model], instance: Model, **kwargs: Any) -> None:
    """Observe a delete while the row's identity is still available."""

    publish_change(instance, action="delete", update_fields=None)
