"""Django config for Angee's IAM addon."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import AppConfig

if TYPE_CHECKING:
    from angee.base.provision import ProvisionContext


class IAMConfig(AppConfig):
    """Source app manifest for Angee identity models."""

    default = True
    name = "angee.iam"
    requires_apps = ('axes', 'django.contrib.auth', 'django.contrib.sessions')

    def compose_provision(self, context: ProvisionContext) -> None:
        """Bootstrap only when deployment inputs explicitly request an initial admin."""

        from angee.iam.management.commands.bootstrap_admin import _configured

        if any(_configured(name, env_fallbacks=(fallback,)) for name, fallback in (
            ('ANGEE_BOOTSTRAP_ADMIN_USERNAME', 'DJANGO_SUPERUSER_USERNAME'),
            ('ANGEE_BOOTSTRAP_ADMIN_EMAIL', 'DJANGO_SUPERUSER_EMAIL'),
            ('ANGEE_BOOTSTRAP_ADMIN_PASSWORD', 'DJANGO_SUPERUSER_PASSWORD'),
        )):
            context.call('bootstrap_admin', database=context.database)

    def ready(self) -> None:
        """Connect IAM's receivers once Django's auth app has connected its own."""

        # Deferred to ready(): signal wiring after app population.
        from angee.iam import signals

        signals.connect()
