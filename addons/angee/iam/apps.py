"""Django config for Angee's IAM addon."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import AppConfig

from angee.iam.autoconfig import bootstrap_admin_settings

if TYPE_CHECKING:
    from django_compose.provision import ProvisionContext


class IAMConfig(AppConfig):
    """Django app configuration for Angee identity models."""

    default = True
    name = "angee.iam"
    requires_apps = ('axes', 'django.contrib.auth', 'django.contrib.sessions')

    def compose_provision(self, context: ProvisionContext) -> None:
        """Bootstrap only when deployment inputs explicitly request an initial admin."""

        if any(bootstrap_admin_settings().values()):
            context.call('bootstrap_admin', database=context.database)

    def ready(self) -> None:
        """Connect IAM's receivers once Django's auth app has connected its own."""

        # Deferred to ready(): signal wiring after app population.
        from angee.iam import signals

        signals.connect()
