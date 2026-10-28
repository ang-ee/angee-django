"""Post-migrate reconcile of the platform ``Addon`` reflection table.

Mirrors Django's content-type / permission sync (``create_contenttypes``): a thin
``post_migrate`` receiver that delegates the work to ``AddonManager`` once
:func:`~angee.base.signals.migrated_model` admits the routed, created table.
(Angee's other derived facts use explicit post-migrate commands; this one
follows Django's signal pattern because it reflects the same kind of
composer-derived metadata content types do.)
"""

from __future__ import annotations

from django.db.models.signals import post_migrate
from rebac import system_context

from angee.platform.models import AddonManager
from angee.base.signals import migrated_model


def connect() -> None:
    """Register the addon-reflection receiver once."""

    post_migrate.connect(_reconcile_addons, dispatch_uid="angee.platform.reconcile_addons")


def _reconcile_addons(*, app_config: object, using: str, **kwargs: object) -> None:
    """Converge the Addon table after migrations create/alter the platform app."""

    addon_model = migrated_model(app_config=app_config, using=using, app_label="platform", model_name="Addon")
    if addon_model is None:
        return
    manager = addon_model._default_manager.db_manager(using)
    assert isinstance(manager, AddonManager)
    with system_context(reason="platform.reconcile_addons"):
        manager.reconcile_loaded_registry()
