"""Django config for Angee's parties addon."""

from __future__ import annotations

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.hooks import check_hooks

check_settings = partial(check_hooks, ("ANGEE_PARTIES_SHARED_SENDER_PROVIDERS", "ANGEE_PARTIES_SIGNING_PROVIDERS"))


class PartiesConfig(AppConfig):
    """Source app manifest for contacts and handles."""

    default = True
    name = "angee.parties"

    def ready(self) -> None:
        """Run parties ready-time hooks after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the models exist; defer.
        from angee.parties import signals

        checks.register(check_settings, checks.Tags.models)

        signals.connect()
