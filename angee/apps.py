"""Django configuration for Angee's runtime foundation."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks
from django.db import models


class BaseConfig(AppConfig):
    """Register checks shared by every composed application."""

    default = True
    name = "angee"
    # Moving the Python package must not rename Django's persisted app label:
    # migrations, content types, and the test-model schema all retain ``base``.
    label = "base"

    def ready(self) -> None:
        """Validate runtime safety and persistence independently of API addons."""

        super().ready()
        from django.apps import apps
        from django.db.models.signals import class_prepared, pre_delete
        from rebac.field_backing import canonical_model

        from angee.models.refs import (
            RecordRefMixin,
            delete_record_refs,
            record_ref_cleanup_edges_for_target,
            record_ref_cleanup_target,
        )
        from angee.signals import connect_for_models

        connect_for_models(
            pre_delete,
            delete_record_refs,
            applies=lambda model: bool(record_ref_cleanup_edges_for_target(model))
            or canonical_model(model) is not None,
            dispatch_uid="angee.record_refs.target_delete",
        )

        def bind_late_cleanup_edge(sender: type[models.Model], **_kwargs: object) -> None:
            """Bind targets prepared before a composed cleanup edge existed."""

            if (
                sender._meta.abstract
                or sender._meta.apps is not apps
                or not issubclass(sender, RecordRefMixin)
                or sender.teardown_for_target is None
            ):
                return
            for model in apps.get_models():
                if record_ref_cleanup_target(sender, model):
                    pre_delete.connect(
                        delete_record_refs,
                        sender=model,
                        dispatch_uid=f"angee.record_refs.target_delete.{model._meta.label_lower}",
                    )

        class_prepared.connect(
            bind_late_cleanup_edge,
            dispatch_uid="angee.record_refs.target_delete.late_cleanup_edge",
            weak=False,
        )
        # The check imports REBAC models, which require completed app population.
        from angee.checks import (
            check_changeset_middleware,
            check_hierarchy_queryset_order,
            check_hooks,
            check_impl_registries,
            check_ownership,
            check_rebac_caveats,
            check_rebac_database,
        )

        checks.register(check_changeset_middleware, checks.Tags.security)
        checks.register(check_rebac_database, checks.Tags.models)
        checks.register(check_hierarchy_queryset_order, checks.Tags.models)
        checks.register(check_impl_registries, checks.Tags.models)
        checks.register(check_hooks, checks.Tags.models)
        checks.register(check_ownership, checks.Tags.models)
        checks.register(check_rebac_caveats, checks.Tags.models)
