"""Django configuration for Angee's runtime foundation."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class BaseConfig(AppConfig):
    """Register checks shared by every composed application."""

    default = True
    name = "angee.base"

    def ready(self) -> None:
        """Validate persistence and queryset composition independently of API addons."""

        super().ready()
        # The check imports REBAC models, which require completed app population.
        from angee.base.checks import (
            check_creation_key_constraints,
            check_hierarchy_queryset_order,
            check_ownership,
            check_rebac_caveats,
            check_rebac_database,
        )

        checks.register(check_rebac_database, checks.Tags.models)
        checks.register(check_hierarchy_queryset_order, checks.Tags.models)
        checks.register(check_creation_key_constraints, checks.Tags.models)
        checks.register(check_ownership, checks.Tags.models)
        checks.register(check_rebac_caveats, checks.Tags.models)
