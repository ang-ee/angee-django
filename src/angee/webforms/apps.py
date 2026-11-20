"""Django config for Angee's web forms addon."""

from __future__ import annotations

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hook

check_settings = partial(check_hook, "ANGEE_WEBFORM_TOKEN_HOOK")


class WebformsConfig(AppConfig):
    """Source app manifest for public forms, their responses and their targets."""

    default = True
    name = "angee.webforms"

    def ready(self) -> None:
        """Check the public address's optional token hook with the settings."""

        super().ready()
        checks.register(check_settings, checks.Tags.models)
