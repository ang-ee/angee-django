"""Django config for Angee's messaging addon."""

from __future__ import annotations

from django.apps import AppConfig


class MessagingConfig(AppConfig):
    """Source app manifest for threads, messages, and channel bridges.

    Owns the chatter thread graph plus channel ingest and outbound delivery paths.
    The one runtime wiring it adds is the chatter-thread teardown receiver (``signals``),
    connected after app population so deleting a chattered record never orphans its
    private thread — on the instance or the bulk ``QuerySet.delete()`` path.
    """

    default = True
    name = "angee.messaging"
    retired_seed_models: tuple[str, ...] = ("messaging.ActivityType",)
    """Removed models whose rows this addon's resource files seeded and that retire with them.

    Activity types moved to ``angee.projects``; on a stack without it, the drop guard
    lets messaging's own seeded catalog rows go with the model (no other row).
    """

    def ready(self) -> None:
        """Wire messaging-owned signal receivers after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the models exist; defer.
        from angee.messaging import signals

        signals.connect()
