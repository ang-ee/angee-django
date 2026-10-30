"""Django configuration for Angee's runtime foundation."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from django import conf
from django.apps import AppConfig, apps
from django.core import checks

from angee.base.hooks import check_hooks

if TYPE_CHECKING:
    from django_compose.provision import ProvisionContext

check_settings = partial(check_hooks, ("ANGEE_MERGE_GUARDS",))


class BaseConfig(AppConfig):
    """Register checks shared by every composed application."""

    default = True
    name = "angee.base"
    requires_apps = ('django.contrib.contenttypes', 'django.contrib.auth', 'rebac')
    # Moving the Python package must not rename Django's persisted app label:
    # migrations, content types, and the test-model schema all retain ``base``.
    label = "base"

    def compose_provision(self, context: ProvisionContext) -> None:
        """Synchronize the selected database after its native migrations complete."""

        context.call('rebac', 'sync', database=context.database, prune_stale=True, yes=True,
                     force_overwrite=conf.settings.ANGEE_REBAC_FORCE_OVERWRITE, skip_checks=True)

    def ready(self) -> None:
        """Validate runtime safety and persistence independently of API addons."""

        super().ready()
        if getattr(conf.settings, "COMPOSE_SNAPSHOT", None) is not None:
            from django_compose.serving import artifact_path

            from angee.base.permissions import bind_rebac_schema, declared_schema_path, merged_schema_relpath

            for config in apps.get_app_configs():
                if declared_schema_path(config) is not None:
                    bind_rebac_schema(config, artifact_path(merged_schema_relpath(config.name)))
        from django.contrib.contenttypes.fields import GenericRelation
        from django.db.models.signals import pre_delete
        from rebac.field_backing import canonical_model

        from angee.base.models.locks import RowLockMixin, refuse_locked_row_delete
        from angee.base.models.refs import delete_record_refs
        from angee.base.signals import connect_for_models

        connect_for_models(
            pre_delete,
            delete_record_refs,
            applies=lambda model: canonical_model(model) is not None
            or any(isinstance(field, GenericRelation) for field in model._meta.private_fields),
            dispatch_uid="angee.record_refs.target_delete",
        )

        connect_for_models(
            pre_delete, refuse_locked_row_delete,
            applies=lambda model: issubclass(model, RowLockMixin),
            dispatch_uid="angee.row_lock.delete",
        )

        # Model-owned checks require completed app population.
        from angee.base.checks import (
            check_ownership,
            check_rebac_caveats,
        )

        checks.register(check_settings, checks.Tags.models)

        checks.register(check_ownership, checks.Tags.models)
        checks.register(check_rebac_caveats, checks.Tags.models)
