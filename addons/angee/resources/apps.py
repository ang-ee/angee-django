"""Native app lifecycle for resource declaration checks."""

from django.apps import AppConfig
from django.core import checks


class ResourcesConfig(AppConfig):
    """Validate rowless resource contracts after native app population."""

    default = True
    name = "angee.resources"

    def ready(self) -> None:
        super().ready()
        from angee.resources.kinds import check_entry_kind_registry
        from angee.resources.sources import check_source_registry

        checks.register(check_entry_kind_registry, checks.Tags.models)
        checks.register(check_source_registry, checks.Tags.models)
