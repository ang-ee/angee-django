"""Django config for Angee's integration runtime addon."""

from __future__ import annotations

from django.apps import AppConfig, apps
from django.core import checks
from django.db.models.signals import pre_delete


class IntegrateConfig(AppConfig):
    """Source app manifest for Angee integration runtime primitives."""

    default = True
    name = "angee.integrate"

    def ready(self) -> None:
        """Wire integration-owned denormalization maintenance after app population."""

        super().ready()
        # Signals resolve concrete models after Django app population.
        from angee.integrate import signals
        from angee.integrate.ownership import (
            ExternalOwnershipMixin,
            check_external_ownership_declarations,
            check_external_ownership_delete,
        )

        signals.connect()
        checks.register(checks.Tags.models)(check_external_ownership_declarations)
        for model in apps.get_models():
            if issubclass(model, ExternalOwnershipMixin):
                pre_delete.connect(
                    check_external_ownership_delete,
                    sender=model,
                    dispatch_uid=f"angee.integrate.ownership_deletes.{model._meta.label_lower}",
                )
