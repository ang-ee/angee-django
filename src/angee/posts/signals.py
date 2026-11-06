"""Public feeds begin their live-event horizon at committed discovery finish."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import models
from rebac import system_context

from angee.integrate.signals import discovery_finished


def stamp_live_since(sender: type[models.Model], *, instance: models.Model, **kwargs: Any) -> None:
    """Resolve a feed subtype without adding posts policy to integrate."""

    with system_context(reason="posts.discovery.finished"):
        feed = apps.get_model("posts", "Feed")._base_manager.filter(pk=instance.pk).first()
        if feed is not None:
            feed.discovery_finished()


def connect() -> None:
    """Bind the feed discovery receiver once during app population."""

    discovery_finished.connect(stamp_live_since, dispatch_uid="posts.discovery.live_since")
