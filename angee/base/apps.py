"""Django configuration for Angee's runtime foundation."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class BaseConfig(AppConfig):
    """Register checks shared by every composed application."""

    default = True
    name = "angee.base"

    def ready(self) -> None:
        """Validate REBAC persistence independently of optional API addons."""

        super().ready()
        # The check imports REBAC models, which require completed app population.
        from angee.base.checks import check_rebac_database

        checks.register(check_rebac_database, checks.Tags.models)
