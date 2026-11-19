"""Django config for Angee's parties addon."""

from __future__ import annotations

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hooks

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
        from angee.parties.checks import check_directory_mappings

        checks.register(check_settings, checks.Tags.models)
        checks.register(check_directory_mappings, checks.Tags.models)

        signals.connect()
