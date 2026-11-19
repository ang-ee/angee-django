"""Django config for Angee's HubSpot directory addon."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class PartiesIntegrateHubspotConfig(AppConfig):
    """Own the startup check of the consumers' HubSpot object mappings."""

    default = True
    name = "angee.parties_integrate_hubspot"

    def ready(self) -> None:
        """Register the object mappings' check once the models exist."""

        super().ready()
        # App population phase 1 imports AppConfig before the models exist; defer.
        from angee.parties_integrate_hubspot.checks import check_object_mappings

        checks.register(check_object_mappings, checks.Tags.models)
