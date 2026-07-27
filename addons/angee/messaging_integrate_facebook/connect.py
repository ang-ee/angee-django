"""Idempotent Facebook import-channel creation and confirmation."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import system_context

from angee.messaging_integrate_facebook.backend import FacebookChannelBackend


def get_or_create_facebook_channel(user: Any, *, name: str = "Facebook") -> Any:
    """Return the user's named Facebook channel, creating it disconnected once."""

    display_name = str(name or "").strip()
    if not display_name:
        raise ValueError("A channel name is required.")
    channel_model = apps.get_model("messaging", "Channel")
    with system_context(reason="messaging_integrate_facebook.get_or_create"), transaction.atomic():
        channel = channel_model._base_manager.filter(
            owner=user,
            backend_class=FacebookChannelBackend.key,
            display_name=display_name,
        ).first()
        if channel is None:
            channel = channel_model.objects.create_disconnected(
                user,
                name=display_name,
                backend_class=FacebookChannelBackend.key,
            )
    return channel


def confirmed_facebook_channel(sqid: str) -> Any:
    """Return the Facebook import channel named by public sqid."""

    channel_model = apps.get_model("messaging", "Channel")
    with system_context(reason="messaging_integrate_facebook.channel.confirm"):
        channel = channel_model._base_manager.filter(
            sqid=sqid,
            backend_class=FacebookChannelBackend.key,
        ).first()
    if channel is None:
        raise ValidationError({"target": f"No Facebook channel {sqid!r}."})
    return channel
