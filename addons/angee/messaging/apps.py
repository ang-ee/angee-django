"""Django config for Angee's messaging addon."""

from __future__ import annotations

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.hooks import check_hook

check_settings = partial(check_hook, "ANGEE_WEBFORM_TOKEN_HOOK")


class MessagingConfig(AppConfig):
    """Source app manifest for threads, messages, and channel bridges."""

    default = True
    name = "angee.messaging"

    def ready(self) -> None:
        from angee.base.signals import connect_for_models, record_access_revoked
        from angee.messaging.events import end_unreadable_follows
        from angee.messaging.models import ThreadedModelMixin

        checks.register(check_settings, checks.Tags.models)

        connect_for_models(
            record_access_revoked,
            end_unreadable_follows,
            applies=lambda model: issubclass(model, ThreadedModelMixin),
            dispatch_uid="messaging.record_access_revoked",
        )
