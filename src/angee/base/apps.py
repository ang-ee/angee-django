"""Django lifecycle for permission-neutral foundation contracts."""

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hooks

check_settings = partial(check_hooks, ("ANGEE_MERGE_GUARDS",))


class BaseConfig(AppConfig):
    default = True
    name = "angee.base"
    label = "base"
    requires_apps = ("django.contrib.contenttypes",)

    def import_models(self) -> None:
        """Expose addressable abstract contracts through native Django discovery."""
        super().import_models()
        from angee.base.models.append_only import AppendOnlyModel
        from angee.base.models.base import AngeeDataModel, AngeeModel
        from angee.base.models.locks import RowLockMixin
        from angee.base.models.merge import MergeableMixin
        from angee.base.models.refs import RecordRefMixin
        from angee.base.models.scope import DefaultedScopeMixin, NullableScopeMixin
        from angee.base.models.trash import TrashMixin

        assert self.models_module is not None
        for contract in (
            AngeeModel,
            AngeeDataModel,
            AppendOnlyModel,
            RowLockMixin,
            MergeableMixin,
            RecordRefMixin,
            DefaultedScopeMixin,
            NullableScopeMixin,
            TrashMixin,
        ):
            setattr(self.models_module, contract.__name__, contract)

    def ready(self) -> None:
        super().ready()
        from django.db.models.signals import pre_delete
        from rebac.signals import collected_for_delete

        from angee.base.models.locks import RowLockMixin, refuse_locked_row_delete
        from angee.base.models.refs import delete_record_refs, delete_record_refs_collected
        from angee.base.signals import connect_for_models

        connect_for_models(
            pre_delete,
            delete_record_refs,
            applies=lambda model: not model._meta.auto_created,
            dispatch_uid="angee.record_refs.target_delete",
        )
        connect_for_models(
            collected_for_delete,
            delete_record_refs_collected,
            applies=lambda model: not model._meta.auto_created,
            dispatch_uid="angee.record_refs.target_delete.batch",
        )
        connect_for_models(
            pre_delete,
            refuse_locked_row_delete,
            applies=lambda model: issubclass(model, RowLockMixin),
            dispatch_uid="angee.row_lock.delete",
        )
        checks.register(check_settings, checks.Tags.models)
