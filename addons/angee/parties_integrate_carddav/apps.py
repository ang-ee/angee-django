"""Django config for Angee's CardDAV parties integration."""

from __future__ import annotations

import pyexpat
from collections.abc import Sequence

from django.apps import AppConfig
from django.core import checks


def check_expat_version(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require Expat's protections where untrusted CardDAV XML is parsed."""

    del app_configs, kwargs
    if pyexpat.version_info >= (2, 7, 2):
        return []
    version = ".".join(str(part) for part in pyexpat.version_info)
    return [
        checks.Error(
            f"Expat 2.7.2 or later is required to parse untrusted XML; Python is linked against {version}.",
            hint="Upgrade Python or its linked Expat library.",
            id="angee.E035",
        )
    ]


class PartiesIntegrateCarddavConfig(AppConfig):
    """Own CardDAV's parser-specific runtime contract."""

    default = True
    name = "angee.parties_integrate_carddav"

    def ready(self) -> None:
        """Register the XML parser safety check with its only consumer."""

        super().ready()
        checks.register(check_expat_version, checks.Tags.security)
