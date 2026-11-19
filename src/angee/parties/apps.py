"""Django config for Angee's parties addon."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hooks

if TYPE_CHECKING:
    from django_compose.provision import ProvisionContext

check_settings = partial(check_hooks, ("ANGEE_PARTIES_SHARED_SENDER_PROVIDERS", "ANGEE_PARTIES_SIGNING_PROVIDERS"))


class PartiesConfig(AppConfig):
    """Source app manifest for contacts and handles."""

    default = True
    name = "angee.parties"

    def compose_provision(self, context: ProvisionContext) -> None:
        """Repair handle holders and directory owners' contact read on every provision, after the resources load.

        ``resolve_stale_owners`` states each holder written onto its row, leaves a
        confirmed or contested one for a person and re-resolves stale ones;
        ``grant_directory_owner_contact_reads`` grants each directory's owner the
        read of its contacts where missing (connecting grants it). Both are
        idempotent, so a deployment never depends on someone running them.
        """

        context.call("resolve_stale_owners")
        context.call("grant_directory_owner_contact_reads")

    def ready(self) -> None:
        """Run parties ready-time hooks after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the models exist; defer.
        from angee.parties import signals
        from angee.parties.checks import check_directory_mappings

        checks.register(check_settings, checks.Tags.models)
        checks.register(check_directory_mappings, checks.Tags.models)

        signals.connect()
