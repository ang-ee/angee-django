"""Model-change event publishing over the channel layer for GraphQL subscriptions."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import Signal

from angee.base.sync import sync_ingestion_active
from angee.graphql.events import ChangePayload

_INMEMORY_CHANNEL_LAYER = "channels.layers.InMemoryChannelLayer"
_CHANGE_BROADCAST_DISPATCH_UID = "angee.graphql.change_broadcast"
change_published = Signal()
"""Sent robustly after commit when a model change should be observed.

Receivers are called with ``sender`` set to the model class and a ``payload``
keyword containing the already-built :class:`ChangePayload`. Delivery uses
``send_robust``: receiver exceptions are logged by the publisher and are not
propagated to the save/delete caller or allowed to starve later receivers.
"""

logger = logging.getLogger(__name__)


def change_group(model: type[models.Model]) -> str:
    """Return the channel-layer group name for ``model`` changes."""

    return f"angee.changes.{model._meta.app_label}.{model._meta.model_name}"


def connect_publishers(model: type[models.Model]) -> None:
    """Connect save and delete publishers for ``model`` exactly once."""

    dispatch_uid = f"angee-changes-{model._meta.label}"
    post_save.connect(
        _on_save,
        sender=model,
        dispatch_uid=f"{dispatch_uid}-save",
    )
    post_delete.connect(
        _on_delete,
        sender=model,
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
    disconnected = post_delete.disconnect(sender=model, dispatch_uid=f"{dispatch_uid}-delete") or disconnected
    return disconnected


def connect_change_broadcast_receiver() -> None:
    """Connect the GraphQL channel-layer broadcast receiver exactly once."""

    change_published.connect(
        _broadcast_change,
        dispatch_uid=_CHANGE_BROADCAST_DISPATCH_UID,
    )


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
    **kwargs: Any,
) -> None:
    """Publish a create or update event after the transaction commits."""

    del sender, kwargs
    if raw:
        return
    publish_change(
        instance,
        action="create" if created else "update",
        update_fields=update_fields,
    )


def _on_delete(
    sender: type[models.Model],
    instance: models.Model,
    **kwargs: Any,
) -> None:
    """Publish a delete event after the transaction commits."""

    del sender, kwargs
    publish_change(instance, action="delete", update_fields=None)


def publish_change(
    instance: models.Model,
    *,
    action: str,
    update_fields: Iterable[str] | None,
) -> None:
    """Build and send one observable change payload after commit."""

    # The row owns whether its changes reach the generic subscription surface; a
    # record-chatter row that is isolated to ``record_thread`` drops out here, so
    # its create/update/delete never broadcasts to a non-record-reader — the
    # emission mirror of the ``.inbox()`` read scope. Checked while the instance is
    # live, so the answer holds for the delete event too.
    broadcasts = getattr(instance, "broadcasts_changes", None)
    if callable(broadcasts) and not broadcasts():
        return
    model = type(instance)
    payload = ChangePayload.from_instance(
        instance,
        action=action,
        update_fields=update_fields,
        during_ingestion=sync_ingestion_active(),
    )
    transaction.on_commit(lambda: _send_change(model, payload))


def _send_change(model: type[models.Model], payload: ChangePayload) -> None:
    """Send ``payload`` to every robust change receiver and log failures."""

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


def _broadcast_change(
    sender: type[models.Model],
    payload: ChangePayload,
    **kwargs: Any,
) -> None:
    """Broadcast a published change payload over the GraphQL channel layer."""

    del kwargs
    _broadcast(sender, payload.as_message())


def _broadcast(model: type[models.Model], payload: dict[str, Any]) -> None:
    """Send ``payload`` to the model's channel-layer change group."""

    layer = change_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        change_group(model),
        {"type": "angee.change", "payload": payload},
    )
