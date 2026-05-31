"""Runtime signal handlers for revisions and model change publishing."""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping
from typing import Any

import reversion
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.apps import apps
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save

from angee.base.models import public_id_of

_connected: set[type[models.Model]] = set()


def register_revision_models() -> None:
    """Register revision-enabled loaded models with reversion."""

    for model in apps.get_models():
        fields = getattr(model, "revisioned_fields", ())
        if fields and not reversion.is_registered(model):
            reversion.register(model, fields=list(fields))


def change_group(model: type[models.Model]) -> str:
    """Return the channel-layer group name for ``model`` changes."""

    return f"angee.changes.{model._meta.app_label}.{model._meta.model_name}"


def connect_publishers(model: type[models.Model]) -> None:
    """Connect save and delete publishers for ``model`` exactly once."""

    if model in _connected:
        return
    _connected.add(model)
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
    _publish(
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
    _publish(instance, action="delete", update_fields=None)


def _publish(
    instance: models.Model,
    *,
    action: str,
    update_fields: Iterable[str] | None,
) -> None:
    """Build and broadcast one change payload after commit."""

    model = type(instance)
    changed_fields = (
        sorted(str(field) for field in update_fields)
        if update_fields is not None
        else None
    )
    changed_values = (
        {
            field: _json_safe(getattr(instance, field, None))
            for field in changed_fields
        }
        if changed_fields is not None
        else None
    )
    payload = {
        "model": model._meta.label,
        "id": public_id_of(instance),
        "action": action,
        "changed_fields": changed_fields,
        "changed_values": changed_values,
    }
    transaction.on_commit(lambda: _broadcast(model, payload))


def _broadcast(model: type[models.Model], payload: dict[str, Any]) -> None:
    """Send ``payload`` to the model's channel-layer change group."""

    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        change_group(model),
        {"type": "angee.change", "payload": payload},
    )


def _json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation of ``value``."""

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)
