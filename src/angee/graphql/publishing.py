"""GraphQL change feeds: the transport adapter over base write observation.

``angee.base.signals`` observes owner writes. This adapter projects each
observation of a declared feed (a model with a ``changes`` subscription) into a
:class:`ChangePayload` and queues its channel-layer delivery on commit, after
local observers queued theirs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction

from angee.base.refs import RecordRef, record_view_refs
from angee.base.signals import ModelChange, change_observed, connect_observers, disconnect_observers
from angee.graphql.events import ChangePayload, ChangeRelatedRecord, ReadableFields
from angee.graphql.mixins import ChangeBroadcastMixin
from angee.graphql.sdl import PUBLISHERS_ARTIFACT

_INMEMORY_CHANNEL_LAYER = "channels.layers.InMemoryChannelLayer"

_publisher_fields: dict[type[models.Model], ReadableFields] = {}
"""Declared feeds and the fields their partial-update payloads may carry."""


def change_group(model: type[models.Model]) -> str:
    """Return the channel-layer group name for ``model`` changes."""

    return f"angee.changes.{model._meta.app_label}.{model._meta.model_name}"


def connect_change_publishers(metadata: Mapping[str, list[str]] | None = None) -> tuple[type[models.Model], ...]:
    """Wire eager publisher fields from verified output or an explicit build projection.

    Only compose and bare source-test hosts pass a projection. Ordinary startup
    always verifies the native receipt; a missing or altered artifact is fatal.
    """

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
    return tuple(resolved)


def connect_publishers(
    model: type[models.Model],
    *,
    readable_fields: ReadableFields = (),
    include_parents: bool = True,
) -> None:
    """Declare ``model`` a transport feed, upgrading any local-only observation.

    Writes through an MTI parent class are observed as writes of this feed's rows.
    The adapter projects feeds after every local observer of a write has run.
    """

    disconnect_publishers(model)
    _publisher_fields[model] = readable_fields if callable(readable_fields) else frozenset(readable_fields)
    change_observed.connect(_on_change_observed, weak=False, dispatch_uid="angee.graphql.changes")
    connect_observers(model, include_parents=include_parents)


def disconnect_publishers(model: type[models.Model]) -> bool:
    """Retire ``model``'s feed and its observation; return whether any was wired.

    The public inverse of :func:`connect_publishers`: a test that wires a lone
    feed restores prior state through this seam.
    """

    _publisher_fields.pop(model, None)
    return disconnect_observers(model)


def change_channel_layer() -> Any:
    """Return the configured channel layer after validating deployment safety."""

    validate_channel_layer()
    return get_channel_layer()


def validate_channel_layer() -> None:
    """Refuse process-local delivery when a broker lets writers run in another process.

    ``enqueue_task`` sends through ``send_task``, which Celery never runs eagerly,
    so a configured ``CELERY_BROKER_URL`` means a worker process broadcasts.
    """

    layer_config = settings.CHANNEL_LAYERS.get("default", {})
    backend = layer_config.get("BACKEND", "")
    if backend != _INMEMORY_CHANNEL_LAYER:
        return
    if settings.ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER is True:
        return
    # Celery broker settings are optional when the jobs addon is absent.
    if settings.DEBUG is True and not getattr(settings, "CELERY_BROKER_URL", None):
        return
    raise ImproperlyConfigured(
        "channels.layers.InMemoryChannelLayer cannot deliver GraphQL changes across processes. "
        "Configure a shared CHANNEL_LAYERS backend such as channels_redis.core.RedisChannelLayer "
        "when a Celery broker is configured. In-memory delivery requires DEBUG=True and no "
        "CELERY_BROKER_URL; use ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER only for isolated tests."
    )


def _on_change_observed(sender: type[models.Model], *, payload: ModelChange, **kwargs: Any) -> None:
    """Project one observed write of a declared feed and queue its delivery on commit.

    The row's broadcast policy (:class:`ChangeBroadcastMixin`) is read while the
    row is live, so a delete keeps the same boundary.
    """

    fields = _publisher_fields.get(sender)
    if fields is None or not payload.broadcasts:
        return
    instance = payload.instance
    if isinstance(instance, ChangeBroadcastMixin) and not instance.broadcasts_changes():
        return
    projected = ChangePayload.from_instance(
        instance,
        action=payload.action,
        update_fields=payload.changed_fields,
        readable_fields=fields,
        field_values=payload.changed_values,
    )
    _queue_transport(sender, replace(projected, id=payload.id, occurrence_id=payload.occurrence_id))


def broadcast_related_records(records: Iterable[models.Model]) -> None:
    """Refresh derived record reads after a source change, without observing another write.

    Only declared record feeds transport these identity-only invalidations,
    including inherited views when the canonical model has no feed. Their normal
    subscriber read policy applies; private source models gain no feed.
    """

    for reference in record_view_refs(*(RecordRef.from_instance(record) for record in records)):
        model = apps.get_model(reference.model_label)
        if model not in _publisher_fields:
            continue
        payload = ChangePayload.from_instance(
            model(pk=reference.object_id), action="update", update_fields=None, broadcasts=False,
        )
        _queue_transport(model, replace(
            payload, id=reference.public_id, broadcasts=True,
            related_records=(ChangeRelatedRecord(reference.model_label, reference.public_id),),
        ))


def _queue_transport(model: type[models.Model], payload: ChangePayload) -> None:
    if payload.broadcasts:
        message = payload.as_message()

        def broadcast() -> None:
            _broadcast(model, message)

        transaction.on_commit(broadcast, robust=True)


def _broadcast(model: type[models.Model], payload: dict[str, Any]) -> None:
    """Send ``payload`` to the model's channel-layer change group."""

    layer = change_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        change_group(model),
        {"type": "angee.change", "payload": payload},
    )
