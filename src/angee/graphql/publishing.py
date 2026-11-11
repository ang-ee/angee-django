"""GraphQL change feeds: the transport adapter over base write observation.

``angee.base.signals`` observes owner writes. This adapter projects each
observation of a declared feed (a model with a ``changes`` subscription) into a
:class:`ChangePayload` and queues its channel-layer delivery on commit, after
local observers queued theirs. A model's feed is its own or, for an MTI child
without one, its nearest ancestor's (:func:`change_feed`): the child's rows share
that ancestor's primary key and public id, so its writes publish there as rows
of the ancestor, and resource metadata points the child's reads at that feed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction

from angee.base.lineage import concrete_lineage, nearest_lineage_model
from angee.base.signals import ModelChange, change_observed, connect_observers, disconnect_observers
from angee.graphql.events import ChangePayload, ReadableFields
from angee.graphql.mixins import ChangeBroadcastMixin
from angee.graphql.sdl import PUBLISHERS_ARTIFACT

_INMEMORY_CHANNEL_LAYER = "channels.layers.InMemoryChannelLayer"

_publisher_fields: dict[type[models.Model], ReadableFields] = {}
"""Declared feeds and the fields their partial-update payloads may carry."""


def change_group(model: type[models.Model]) -> str:
    """Return the channel-layer group name for ``model`` changes."""

    return f"angee.changes.{model._meta.app_label}.{model._meta.model_name}"


def change_feed(model: type[models.Model]) -> type[models.Model] | None:
    """Return the declared feed carrying ``model``'s rows: its own, else its nearest MTI ancestor's."""

    return nearest_lineage_model(model, _publisher_fields)


def _inheriting_children(model: type[models.Model]) -> tuple[type[models.Model], ...]:
    """Return the MTI descendants of ``model`` whose writes publish on its feed."""

    return tuple(
        child for child in concrete_lineage(model)
        if child is not model and issubclass(child, model) and change_feed(child) is model
    )


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

    Writes through an MTI parent class are observed as writes of this feed's rows,
    and so are writes through an MTI child without a feed of its own.
    The adapter projects feeds after every local observer of a write has run.
    """

    disconnect_publishers(model)
    _publisher_fields[model] = readable_fields if callable(readable_fields) else frozenset(readable_fields)
    change_observed.connect(_on_change_observed, weak=False, dispatch_uid="angee.graphql.changes")
    connect_observers(model, include_parents=include_parents)
    for child in _inheriting_children(model):
        connect_observers(child)


def disconnect_publishers(model: type[models.Model]) -> bool:
    """Retire ``model``'s feed and its observation; return whether any was wired.

    The public inverse of :func:`connect_publishers`: a test that wires a lone
    feed restores prior state through this seam.
    """

    children = _inheriting_children(model) if model in _publisher_fields else ()
    _publisher_fields.pop(model, None)
    for child in children:
        disconnect_observers(child)
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
    """Project one observed write onto its feed and queue its delivery on commit.

    A write through an MTI child without a feed of its own publishes as a row of
    the ancestor's feed. Its delete is left to the ancestor row, which Django
    deletes with it. The row's broadcast policy (:class:`ChangeBroadcastMixin`) is
    read while the row is live, so a delete keeps the same boundary.
    """

    feed = change_feed(sender)
    if feed is None or not payload.broadcasts or (feed is not sender and payload.action == "delete"):
        return
    instance = payload.instance
    if isinstance(instance, ChangeBroadcastMixin) and not instance.broadcasts_changes():
        return
    projected = ChangePayload.from_instance(
        instance,
        action=payload.action,
        update_fields=payload.changed_fields,
        readable_fields=_publisher_fields[feed],
        field_values=payload.changed_values,
    )
    _queue_transport(
        feed,
        replace(projected, model=feed._meta.label, id=payload.id, occurrence_id=payload.occurrence_id),
    )


def _queue_transport(model: type[models.Model], payload: ChangePayload) -> None:
    if payload.broadcasts:
        message = payload.as_message()

        def broadcast() -> None:
            _broadcast(model, message)

        transaction.on_commit(broadcast, robust=True)


def _broadcast(model: type[models.Model], payload: dict[str, Any]) -> None:
    """Send ``payload`` to the model's channel-layer change group."""

    broadcast_group(change_group(model), payload)


def broadcast_group(group: str, payload: dict[str, Any]) -> None:
    """Send one change notification through the shared channel-layer transport."""

    layer = change_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        group,
        {"type": "angee.change", "payload": payload},
    )
