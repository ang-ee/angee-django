"""Django config for Angee's messaging addon."""

from __future__ import annotations

from django.apps import AppConfig


class MessagingConfig(AppConfig):
    """Source app manifest for threads, messages, and channel bridges."""

    default = True
    name = "angee.messaging"

    def ready(self) -> None:
        from angee.base.signals import connect_for_models, record_access_revoked
        from angee.messaging.events import end_unreadable_follows
        from angee.messaging.models import ThreadedModelMixin

        connect_for_models(
            record_access_revoked,
            end_unreadable_follows,
            applies=lambda model: issubclass(model, ThreadedModelMixin),
            dispatch_uid="messaging.record_access_revoked",
        )
