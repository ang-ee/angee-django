"""Model-change event publishing over the channel layer for GraphQL subscriptions."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import Signal

from angee.base.refs import RecordRef, record_view_refs
from angee.base.signals import bulk_updated
from angee.graphql.events import ChangePayload, ChangeRelatedRecord, ReadableFields
from angee.graphql.mixins import ChangeBroadcastMixin
from angee.graphql.sdl import PUBLISHERS_ARTIFACT

_INMEMORY_CHANNEL_LAYER = "channels.layers.InMemoryChannelLayer"
change_published = Signal()
"""Sent robustly when an owner publishes a model change, inside its transaction.

Receivers are called with ``sender`` set to the model class, a ``payload``
keyword containing the already-built :class:`ChangePayload`. Delivery uses
``send_robust``: receiver exceptions are logged by the publisher and are not
propagated to the save/delete caller or allowed to starve later receivers.
Observers enqueue effects with ``transaction.on_commit``. The publisher queues
transport afterward; derived reads can invalidate their related record feeds
through the same native commit queue.
"""

logger = logging.getLogger(__name__)

_publisher_model_labels: frozenset[str] = frozenset()

_mute_state = threading.local()
_publication_ingestion_depth: ContextVar[int] = ContextVar("graphql_publication_ingestion_depth", default=0)


@contextmanager
def publication_ingestion_context() -> Iterator[None]:
    """Mark emitted change payloads as ingestion while preserving broadcasting."""

    token = _publication_ingestion_depth.set(_publication_ingestion_depth.get() + 1)
    try:
        yield
    finally:
        _publication_ingestion_depth.reset(token)


def publication_ingestion_active() -> bool:
    """Return the ingestion annotation for a change captured in this context."""

    return _publication_ingestion_depth.get() > 0


def changes_muted() -> bool:
    """Whether the current owner suppresses local observation and transport."""

    return bool(getattr(_mute_state, "depth", 0))


@contextmanager
def mute_changes() -> Iterator[None]:
    """Suppress per-row change publishing for the duration of the block.

    A bulk teardown that deletes thousands of rows fires one ``post_delete``
    publisher per row; each would lazy-load the row's thread and run an ``exists()``
    probe inside ``broadcasts_changes()`` before buffering an ``on_commit``
    ``group_send``. While a whole scope is being purged those per-row events are
    noise (and their N+1 defeats a count-only preview's scale win), so
    :func:`publish_change` returns immediately when muted — skipping both the probe
    and the broadcast. Thread-local and reentrant, and the prior depth is restored in
    ``finally`` so a nested or failing block never leaks the muted state. This is not
    :func:`disconnect_publishers`: it does not touch the shared signal registry, so it
    is safe under concurrent requests. A coarse-grained change the caller still wants
    observed (the purged root's own row) must run *outside* the block.
    """

    depth = getattr(_mute_state, "depth", 0)
    _mute_state.depth = depth + 1
    try:
        yield
    finally:
        _mute_state.depth = depth


def change_group(model: type[models.Model]) -> str:
    """Return the channel-layer group name for ``model`` changes."""

    return f"angee.changes.{model._meta.app_label}.{model._meta.model_name}"


def connect_change_publishers(metadata: Mapping[str, list[str]] | None = None) -> tuple[type[models.Model], ...]:
    """Wire eager publisher fields from verified output or an explicit build projection.

    Only compose and bare source-test hosts pass a projection. Ordinary startup
    always verifies the native receipt; a missing or altered artifact is fatal.
    """

    global _publisher_model_labels
    if metadata is None:
        from django_compose.serving import artifact_path

        metadata = json.loads(artifact_path(PUBLISHERS_ARTIFACT).read_text(encoding="utf-8"))
    if not isinstance(metadata, Mapping) or any(not isinstance(label, str) for label in metadata):
        raise ImproperlyConfigured("GraphQL publisher metadata must be a model-to-fields mapping; run compose")
    resolved: dict[type[models.Model], frozenset[str]] = {}
    for label, fields in sorted(metadata.items()):
        if not isinstance(label, str) or not isinstance(fields, list | tuple) or any(
            not isinstance(field, str) for field in fields
        ):
            raise ImproperlyConfigured(
                "GraphQL publisher metadata must contain model labels and field lists; run compose"
            )
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError) as error:
            raise ImproperlyConfigured(f"Unknown GraphQL publisher model: {label}; run compose") from error
        if model is None:
            raise ImproperlyConfigured(f"Unknown GraphQL publisher model: {label}; run compose")
        resolved[model] = frozenset(fields)
    for model, fields in resolved.items():
        connect_publishers(model, readable_fields=fields)
    _publisher_model_labels = frozenset(model._meta.label_lower for model in resolved)
    return tuple(resolved)


def connect_publishers(
    model: type[models.Model],
    *,
    readable_fields: ReadableFields = (),
    include_parents: bool = True,
) -> None:
    """Connect a declared transport feed, upgrading any local-only observation."""

    disconnect_publishers(model)
    _connect_changes(model, readable_fields=readable_fields, include_parents=include_parents, broadcast=True)


def connect_observers(model: type[models.Model]) -> None:
    """Observe local writes without enabling a transport feed or replacing one."""

    _connect_changes(model, broadcast=False)


def _connect_changes(model: type[models.Model], *, readable_fields: ReadableFields = (),
                     include_parents: bool = False, broadcast: bool) -> None:
    """Connect native saves/deletes and optionally the model's parent-backed view.

    GraphQL child views observe parent writes too. Source observers that already
    bind every observed class disable that mirror to avoid probing child tables.
    """

    dispatch_uid = f"angee-changes-{model._meta.label}"
    bulk_updated.connect(partial(publish_bulk_update, broadcast=broadcast), sender=model,
                         weak=False, dispatch_uid=f"{dispatch_uid}-bulk")
    save_receiver = partial(
        _on_save,
        published_model=model,
        broadcast=broadcast,
        readable_fields=(readable_fields if callable(readable_fields) else frozenset(readable_fields)),
    )
    senders = (model, *model._meta.get_parent_list()) if include_parents else (model,)
    for sender in senders:
        # Django sends only the saved class's signal, including parent writes.
        post_save.connect(save_receiver, sender=sender, weak=False,
                          dispatch_uid=f"{dispatch_uid}-save" if sender is model
                          else f"{dispatch_uid}-save-{sender._meta.label}")
    post_delete.connect(
        partial(_on_delete, broadcast=broadcast),
        sender=model,
        weak=False,
        dispatch_uid=f"{dispatch_uid}-delete",
    )


def disconnect_publishers(model: type[models.Model]) -> bool:
    """Disconnect ``model``'s save and delete publishers; return whether any were wired.

    The public inverse of :func:`connect_publishers` — a test that wires a lone
    publisher to observe a broadcast restores prior state through this seam instead of
    re-deriving the private dispatch-uid format or probing receiver tuples.
    """

    dispatch_uid = f"angee-changes-{model._meta.label}"
    disconnected = post_save.disconnect(sender=model, dispatch_uid=f"{dispatch_uid}-save")
    for parent in model._meta.get_parent_list():
        disconnected = post_save.disconnect(
            sender=parent, dispatch_uid=f"{dispatch_uid}-save-{parent._meta.label}",
        ) or disconnected
    disconnected = post_delete.disconnect(sender=model, dispatch_uid=f"{dispatch_uid}-delete") or disconnected
    disconnected = bulk_updated.disconnect(sender=model, dispatch_uid=f"{dispatch_uid}-bulk") or disconnected
    return bool(disconnected)


def change_channel_layer() -> Any:
    """Return the configured channel layer after validating deployment safety."""

    _check_channel_layer()
    return get_channel_layer()


def _check_channel_layer() -> None:
    """Fail loudly when the dev-only in-memory layer is used outside dev/test."""

    layer_config = getattr(settings, "CHANNEL_LAYERS", {}).get("default", {})
    backend = layer_config.get("BACKEND", "")
    if backend != _INMEMORY_CHANNEL_LAYER:
        return
    if getattr(settings, "DEBUG", False):
        return
    if getattr(settings, "ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER", False):
        return
    raise ImproperlyConfigured(
        "channels.layers.InMemoryChannelLayer is dev-only for GraphQL changes; configure a shared channel layer."
    )


def _on_save(
    sender: type[models.Model],
    instance: models.Model,
    created: bool = False,
    update_fields: Iterable[str] | None = None,
    raw: bool = False,
    readable_fields: ReadableFields = (),
    published_model: type[models.Model] | None = None,
    broadcast: bool = True,
    **kwargs: Any,
) -> None:
    """Publish a local create or update; observers defer effects until commit."""

    del sender, kwargs
    if raw:
        return
    if published_model is not None and type(instance) is not published_model:
        published_instance = published_model._base_manager.filter(pk=instance.pk).first()
        if published_instance is None:
            return
        instance = published_instance
        created = False
    publish_change(
        instance,
        action="create" if created else "update",
        update_fields=update_fields,
        readable_fields=readable_fields,
        broadcast=broadcast,
    )


def _on_delete(
    sender: type[models.Model],
    instance: models.Model,
    broadcast: bool = True,
    **kwargs: Any,
) -> None:
    """Publish a local delete while its source identity is still available."""

    del sender, kwargs
    publish_change(instance, action="delete", update_fields=None, broadcast=broadcast)


def publish_bulk_update(sender: type[models.Model], *, instances: Iterable[models.Model],
                        update_fields: Iterable[str], broadcast: bool = True, **kwargs: Any) -> None:
    """Adapt the core's bounded batch notification to the row-change protocol."""

    if changes_muted():
        return
    coalesce = getattr(sender, "change_batch_targets", None)
    fields: Iterable[str] | None = update_fields
    if coalesce is not None:
        instances = coalesce(instances, update_fields=update_fields)
        fields = None
    for instance in instances:
        publish_change(instance, action="update", update_fields=fields, broadcast=broadcast)


def publish_change(
    instance: models.Model,
    *,
    action: str,
    update_fields: Iterable[str] | None,
    readable_fields: ReadableFields = (),
    broadcast: bool = True,
) -> None:
    """Publish one local change, then queue transport after local commit effects."""

    # A bulk purge mutes publishing for its whole teardown (see mute_changes): return
    # before the broadcasts_changes() probe so neither the per-row thread lazy-load /
    # exists() nor the group_send runs while thousands of rows are being deleted.
    if changes_muted():
        return
    # Broadcast policy governs transport, not local read invalidation observers.
    # Capture it while the row is live so deletion retains the same boundary.
    broadcasts = broadcast and (
        not isinstance(instance, ChangeBroadcastMixin) or instance.broadcasts_changes()
    )
    model = type(instance)

    payload = ChangePayload.from_instance(
        instance,
        action=action,
        update_fields=update_fields,
        readable_fields=readable_fields if broadcasts else (),
        during_ingestion=publication_ingestion_active(),
        broadcasts=broadcasts,
    )
    _send_change(model, payload)


def _send_change(model: type[models.Model], payload: ChangePayload) -> None:
    """Deliver local observations before queuing transport; isolate receiver failures."""

    responses = change_published.send_robust(sender=model, payload=payload)
    for receiver, response in responses:
        if not isinstance(response, Exception):
            continue
        logger.error(
            "change_published receiver %r failed for %s %s.",
            receiver,
            payload.model,
            payload.id,
            exc_info=(type(response), response, response.__traceback__),
        )
    _queue_transport(model, payload)


def broadcast_related_records(records: Iterable[models.Model]) -> None:
    """Refresh derived record reads after a source change, without observing another write.

    Only declared record feeds transport these identity-only invalidations,
    including inherited views when the canonical model has no feed. Their normal
    subscriber read policy applies; private source models gain no feed.
    """

    feeds = _publisher_model_labels
    by_database: dict[str | None, list[models.Model]] = {}
    for record in records:
        by_database.setdefault(record._state.db, []).append(record)
    for using, rows in by_database.items():
        for reference in record_view_refs(*(RecordRef.from_instance(row) for row in rows), using=using):
            model = apps.get_model(reference.model_label)
            if model._meta.label_lower not in feeds:
                continue
            record = model(pk=reference.object_id)
            record._state.db = using
            payload = ChangePayload.from_instance(record, action="update", update_fields=None, broadcasts=False)
            _queue_transport(model, replace(
                payload, id=reference.public_id, broadcasts=True,
                related_records=(ChangeRelatedRecord(reference.model_label, reference.public_id),),
            ))


def _queue_transport(model: type[models.Model], payload: ChangePayload) -> None:
    if payload.broadcasts:
        using = payload.instance._state.db if payload.instance is not None else None
        message = payload.as_message()

        def broadcast() -> None:
            _broadcast(model, message)

        transaction.on_commit(broadcast, using=using, robust=True)


def _broadcast(model: type[models.Model], payload: dict[str, Any]) -> None:
    """Send ``payload`` to the model's channel-layer change group."""

    layer = change_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        change_group(model),
        {"type": "angee.change", "payload": payload},
    )
