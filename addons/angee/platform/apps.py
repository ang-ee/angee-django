"""Django config for Angee's platform addon."""

from __future__ import annotations

from django.apps import AppConfig


class PlatformConfig(AppConfig):
    """Source app manifest for the platform console.

    The platform console is the home for Angee's admin surfaces. It owns the
    "platform menu" — a schema/metadata explorer that reflects the composed
    runtime (the addon registry, concrete models, fields, and relation edges) —
    and hosts the sections other base addons contribute into it (``resources``
    contributes the import ledger, ``operator`` contributes the daemon console).
    It holds no data tables; its reads are gated by the table-less
    ``platform/explorer`` REBAC anchor.
    """

    default = True
    name = "angee.platform"

    def ready(self) -> None:
        """Register the reconcile receiver + the installer-backend system check."""

        super().ready()
        from angee.platform import installer, signals

        installer.register_checks()
        signals.connect()
