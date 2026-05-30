"""Signal handlers and app-startup registration for the base addon.

Two startup concerns live here, both wired from
:meth:`angee.base.apps.BaseConfig.ready` once concrete runtime models are
loaded so they track the composed runtime models, not the abstract sources:

- ``register_revision_models`` registers django-reversion tracking.
- the change publishers broadcast model saves/deletes to the channel layer for
  GraphQL ``changes`` subscriptions (see ``angee.base.graphql.subscriptions``).
"""

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


def register_revision_models() -> None:
    """Register every composed model declaring ``revisioned_fields``.

    Run from the base addon's ``ready()`` once concrete models are loaded, so
    django-reversion tracks the runtime models, not the abstract sources.
    """

    for model in apps.get_models():
        fields = getattr(model, "revisioned_fields", ())
        if fields and not reversion.is_registered(model):
            reversion.register(model, fields=list(fields))


# --- change publishing ----------------------------------------------------

_connected: set[type[models.Model]] = set()


def change_group(model: type[models.Model]) -> str:
    """Return the channel-layer group name for a model's changes."""

    return f"angee.changes.{model._meta.app_label}.{model._meta.model_name}"


def connect_publishers(model: type[models.Model]) -> None:
    """Connect change publishers for ``model`` exactly once."""

    if model in _connected:
        return
    _connected.add(model)
    uid = f"angee-changes-{model._meta.label}"
    post_save.connect(_on_save, sender=model, dispatch_uid=f"{uid}-save")
    post_delete.connect(_on_delete, sender=model, dispatch_uid=f"{uid}-delete")


def _on_save(
    sender: type[models.Model],
    instance: models.Model,
    created: bool = False,
    update_fields: Iterable[str] | None = None,
    raw: bool = False,
    **_: Any,
) -> None:
    """Publish a create/update event after the transaction commits."""

    if raw:
        return
    _publish(
        instance,
        action="create" if created else "update",
        update_fields=update_fields,
    )


def _on_delete(
    sender: type[models.Model], instance: models.Model, **_: Any
) -> None:
    """Publish a delete event after the transaction commits."""

    _publish(instance, action="delete", update_fields=None)


def _publish(
    instance: models.Model,
    *,
    action: str,
    update_fields: Iterable[str] | None,
) -> None:
    """Build a change payload and broadcast it on commit."""

    model = type(instance)
    changed = (
        sorted(str(name) for name in update_fields)
        if update_fields is not None
        else None
    )
    values = (
        {name: _json_safe(getattr(instance, name, None)) for name in changed}
        if changed is not None
        else None
    )
    payload = {
        "model": model._meta.label,
        "id": instance.public_id,
        "action": action,
        "changed_fields": changed,
        "changed_values": values,
    }
    transaction.on_commit(lambda: _broadcast(model, payload))


def _broadcast(model: type[models.Model], payload: dict[str, Any]) -> None:
    """Send one payload to the model's channel-layer group."""

    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        change_group(model), {"type": "angee.change", "payload": payload}
    )


def _json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation of a changed value."""

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)
