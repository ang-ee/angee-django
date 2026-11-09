"""Django config for Angee's IAM addon."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.core import checks

from angee.iam.autoconfig import bootstrap_admin_settings

if TYPE_CHECKING:
    from django_compose.provision import ProvisionContext


class IAMConfig(AppConfig):
    """Django app configuration for Angee identity models."""

    default = True
    name = "angee.iam"
    requires_apps = ('axes', 'django.contrib.auth', 'django.contrib.sessions')

    def compose_provision(self, context: ProvisionContext) -> None:
        """Bootstrap only when deployment inputs explicitly request an initial admin.

        The command writes through the default manager on the one database, as
        every provisioning write does.
        """

        if any(bootstrap_admin_settings().values()):
            context.call('bootstrap_admin')

    def ready(self) -> None:
        """Connect IAM's receivers once Django's auth app has connected its own; register IAM's checks."""

        # Deferred to ready(): signal wiring and schema checks after app population.
        from angee.iam import signals
        from angee.iam.checks import check_view_as_admin_only

        signals.connect()
        checks.register(check_view_as_admin_only, checks.Tags.security)
