"""Django configuration for Angee's runtime foundation."""

from __future__ import annotations

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hooks

check_settings = partial(check_hooks, ("ANGEE_MERGE_GUARDS",))


class BaseConfig(AppConfig):
    """Register checks shared by every composed application."""

    default = True
    name = "angee.base"
    requires_apps = ('django.contrib.contenttypes', 'rebac')
    # Moving the Python package must not rename Django's persisted app label:
    # migrations, content types, and the test-model schema all retain ``base``.
    label = "base"

    def ready(self) -> None:
        """Validate runtime safety and persistence independently of API addons."""

        super().ready()
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
