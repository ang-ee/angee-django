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
        """Repair handle holders on every provision, after the resources load parties' install rules.

        ``resolve_stale_owners`` states each holder written onto its row, leaves a
        trashed, confirmed or contested one as it is and re-resolves stale ones; it
        is idempotent, so a deployment never depends on someone running it. One run
        writes at most ``HandleManager.REPAIR_LIMIT`` holders; past that, the
        provision log says how many remain and the next provision continues.
        """

        context.call("resolve_stale_owners")

    def ready(self) -> None:
        """Run parties ready-time hooks after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the models exist; defer.
        from angee.parties import signals

        checks.register(check_settings, checks.Tags.models)

        signals.connect()
