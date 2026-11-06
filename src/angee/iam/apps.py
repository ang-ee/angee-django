"""Django config for Angee's IAM addon."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import conf
from django.apps import AppConfig, apps
from django.core import checks

from angee.iam.autoconfig import bootstrap_admin_settings

if TYPE_CHECKING:
    from django_compose.provision import ProvisionContext


class IAMConfig(AppConfig):
    """Django app configuration for Angee identity models."""

    default = True
    name = "angee.iam"
    requires_apps = ('axes', 'django.contrib.auth', 'django.contrib.sessions', 'rebac')

    def compose_provision(self, context: ProvisionContext) -> None:
        """Bootstrap only when deployment inputs explicitly request an initial admin."""

        context.call('rebac', 'sync', database=context.database, prune_stale=True, yes=True,
                     force_overwrite=conf.settings.ANGEE_REBAC_FORCE_OVERWRITE, skip_checks=True)
        if any(bootstrap_admin_settings().values()):
            context.call('bootstrap_admin', database=context.database)

    def ready(self) -> None:
        """Connect IAM's receivers once Django's auth app has connected its own."""

        if getattr(conf.settings, "COMPOSE_SNAPSHOT", None) is not None:
            from django_compose.serving import artifact_path

            from angee.iam.permissions import bind_rebac_schema, declared_schema_path, merged_schema_relpath
            for config in apps.get_app_configs():
                if declared_schema_path(config) is not None:
                    bind_rebac_schema(config, artifact_path(merged_schema_relpath(config.name)))
        from angee.iam.checks import check_ownership, check_rebac_caveats
        checks.register(check_ownership, checks.Tags.models)
        checks.register(check_rebac_caveats, checks.Tags.models)
        # Deferred to ready(): signal wiring after app population.
        from angee.iam import signals

        signals.connect()
