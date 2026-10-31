"""Model-change event publishing over the channel layer for GraphQL subscriptions."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction

from angee.base import signals as model_signals
from angee.base.refs import RecordRef, record_view_refs
from angee.base.signals import ModelChange
from angee.graphql.events import ChangePayload, ChangeRelatedRecord, ReadableFields
from angee.graphql.mixins import ChangeBroadcastMixin
from angee.graphql.sdl import PUBLISHERS_ARTIFACT

# Historical imports retain the one neutral implementation.
change_published = model_signals.change_published
changes_muted = model_signals.changes_muted
connect_observers = model_signals.connect_observers
mute_changes = model_signals.mute_changes
publication_ingestion_active = model_signals.publication_ingestion_active
publication_ingestion_context = model_signals.publication_ingestion_context
publish_bulk_update = model_signals.publish_bulk_update

_INMEMORY_CHANNEL_LAYER = "channels.layers.InMemoryChannelLayer"
_publisher_model_labels: frozenset[str] = frozenset()
_publisher_fields: dict[type[models.Model], ReadableFields] = {}
_explicit_readable_fields: ContextVar[tuple[models.Model, ReadableFields] | None] = ContextVar(
    "graphql_change_readable_fields", default=None,
)


def connect_change_adapter() -> None:
    """Project transport only after every synchronous local observer has run."""
    model_signals.change_observed.connect(_on_change_observed, weak=False, dispatch_uid="angee.graphql.changes")


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
    for model, readable_fields in resolved.items():
        connect_publishers(model, readable_fields=readable_fields)
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
    _publisher_fields[model] = readable_fields if callable(readable_fields) else frozenset(readable_fields)
    connect_change_adapter()
    connect_observers(model, include_parents=include_parents)


def disconnect_publishers(model: type[models.Model]) -> bool:
    """Disconnect a declared transport feed and its native model observers."""
    _publisher_fields.pop(model, None)
    return model_signals.disconnect_observers(model)


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


def publish_change(instance: models.Model, *, action: str, update_fields: Iterable[str] | None,
                   readable_fields: ReadableFields = (), broadcast: bool = True) -> None:
    """Compatibility adapter for explicit GraphQL publication of an owner write."""
    connect_change_adapter()
    token = _explicit_readable_fields.set((instance, readable_fields))
    try:
        model_signals.publish_change(instance, action=action, update_fields=update_fields, broadcast=broadcast)
    finally:
        _explicit_readable_fields.reset(token)


def _on_change_observed(sender: type[models.Model], *, payload: ModelChange, **kwargs: Any) -> None:
    """Capture GraphQL-only values/read gates after local write observation."""
    requested = _explicit_readable_fields.get()
    explicit = requested[1] if requested is not None and requested[0] is payload.instance else None
    if explicit is None and sender not in _publisher_fields:
        return
    instance = payload.instance
    broadcasts = payload.broadcasts and (
        not isinstance(instance, ChangeBroadcastMixin) or instance.broadcasts_changes()
    )
    fields = explicit if explicit is not None else _publisher_fields[sender]
    projected = ChangePayload.from_instance(
        instance, action=payload.action, update_fields=payload.changed_fields,
        readable_fields=fields if broadcasts else (), during_ingestion=payload.during_ingestion,
        broadcasts=broadcasts, field_values=payload.changed_values,
    )
    _queue_transport(sender, replace(
        projected, id=payload.id, occurrence_id=payload.occurrence_id, using=payload.using,
    ))


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
        using = payload.using
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
