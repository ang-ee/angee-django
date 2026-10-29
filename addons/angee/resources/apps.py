"""Native app lifecycle for resource declaration checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.conf import settings

if TYPE_CHECKING:
    from angee.base.provision import ProvisionContext
from django.core import checks


class ResourcesConfig(AppConfig):
    """Validate rowless resource contracts after native app population."""

    default = True
    name = "angee.resources"

    def compose_provision(self, context: ProvisionContext) -> None:
        """Reload selected tiers idempotently; explicit project settings opt into demo."""

        context.call('resources', 'load', database=context.database,
                     include_demo=settings.ANGEE_RESOURCE_INCLUDE_DEMO)

    def ready(self) -> None:
        super().ready()
        from angee.resources.kinds import check_entry_kind_registry
        from angee.resources.sources import check_source_registry

        checks.register(check_entry_kind_registry, checks.Tags.models)
        checks.register(check_source_registry, checks.Tags.models)
