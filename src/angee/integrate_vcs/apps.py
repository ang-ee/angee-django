"""Django config for the standalone VCS capability."""

from django.apps import AppConfig
from django.core import checks


class IntegrateVcsConfig(AppConfig):
    """Register VCS capability checks after app population."""

    default = True
    name = "angee.integrate_vcs"

    def ready(self) -> None:
        """Register VCS source-kind contracts."""

        super().ready()
        from angee.integrate_vcs.registry import check_source_kind_contracts

        checks.register(checks.Tags.models)(check_source_kind_contracts)
