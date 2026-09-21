"""Native import-export lifecycle adapter for workflow definition facets."""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models.fields import NOT_PROVIDED
from import_export import fields
from import_export.results import RowResult

from angee.base.db import get_write_alias
from angee.base.scoping import system_queryset
from angee.resources.exceptions import ResourceLoadError
from angee.resources.loader import AngeeResource
from angee.resources.widgets import XrefForeignKeyWidget, split_xref


class _DeclarationField(fields.Field):
    """Keep explicit null distinct from an omitted field's Django default."""

    def clean(self, row: Mapping[str, Any], **kwargs: Any) -> Any:
        if self.column_name in row and row[self.column_name] is None:
            return None
        return super().clean(row, **kwargs)


class _WorkflowReferenceWidget(XrefForeignKeyWidget):
    """Use native resource instances for earlier rows in this same dataset."""

    resource: WorkflowDefinitionResource

    def resolve_field_target(self, ref: str) -> Any:
        addon, xref = split_xref(ref, self.addon_aliases)
        if self.model is self.resource._meta.model and addon == self.resource.entry.addon.name:
            instance = self.resource.instance_for_xref(xref)
            if instance is not None:
                return instance
        return super().resolve_field_target(ref)


class WorkflowDefinitionResource(AngeeResource):
    """Buffer native cleaned rows, then reconcile one source-owned graph facet.

    Rows and counts remain native import-export results. No ledger is written
    until the definition manager returns persisted instances. Hash skips still
    belong to the complete declaration set, without changing their ledgers.
    """

    DEFAULT_RESOURCE_FIELD = _DeclarationField

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, _WorkflowReferenceWidget):
                field.widget.resource = self

    @classmethod
    def get_fk_widget(cls, field: Any) -> Any:
        return functools.partial(_WorkflowReferenceWidget, model=field.remote_field.model)

    @classmethod
    def field_from_django_field(cls, field_name: str, django_field: Any, readonly: bool) -> Any:
        field = super().field_from_django_field(field_name, django_field, readonly)
        if isinstance(field.widget, _WorkflowReferenceWidget):
            # Native FK assignment retains an earlier buffered model instance;
            # its primary key is filled by ordered persistence in after_import.
            field.attribute = field_name
            field.widget.key_is_id = False
        return field

    def after_init_instance(self, instance: Any, new: bool, row: Mapping[str, Any], **kwargs: Any) -> None:
        super().after_init_instance(instance, new, row, **kwargs)
        self._instances[row["_xref"]] = instance

    @property
    def workflow_model(self) -> type[Any]:
        return apps.get_model("workflows", "Workflow")

    @property
    def declaration_fields(self) -> frozenset[str]:
        manager = self.workflow_model.objects
        model = self._meta.model
        if model is self.workflow_model:
            return manager._WORKFLOW_FIELDS | {"key"}
        if model is self.workflow_model._meta.get_field("steps").related_model:
            return manager._NODE_FIELDS | {"workflow"}
        return manager._EDGE_FIELDS | {"workflow", "source", "target"}

    @classmethod
    def lock_imports(cls, loaded_groups: Sequence[tuple[Any, AngeeResource]], *, using: str | None = None) -> None:
        """Lock all existing affected heads, including omissions and adoption.

        Planning reads may resolve existing xrefs/natural identities only. They
        never clean a declaration for persistence, import, or write a ledger.
        New heads are governed by the enclosing transaction and native uniqueness.
        """

        facets = [
            (group, resource) for group, resource in loaded_groups
            if isinstance(resource, WorkflowDefinitionResource)
        ]
        if not facets:
            return
        model = facets[0][1].workflow_model
        alias = get_write_alias(model, using=using)
        if alias != DEFAULT_DB_ALIAS or not connections[alias].in_atomic_block:
            raise ResourceLoadError("Workflow resource locks require the default resource transaction.")
        ids = cls._lock_targets(facets, using=alias)
        locked = list(system_queryset(model, using=alias, lock=("self",)).filter(pk__in=ids).order_by("pk"))
        if {row.pk for row in locked} != ids or cls._lock_targets(facets, using=alias) - ids:
            raise ResourceLoadError("Workflow resource parents changed during lock planning; retry the load.")

    @classmethod
    def _lock_targets(cls, facets: Sequence[tuple[Any, WorkflowDefinitionResource]], *, using: str) -> set[int]:
        ids: set[int] = set()
        declared_heads: dict[tuple[str, str], Any] = {}
        # Any facet may adopt an existing target without a ledger yet. Keep
        # declared heads available to references elsewhere in the batch too.
        for group, resource in facets:
            is_head = group.model is resource.workflow_model
            resource._instances.clear()
            resource._existing_ledgers.clear()
            for row in group.dataset.dict:
                row = {name: value for name, value in row.items() if value is not NOT_PROVIDED}
                try:
                    instance = resource.instance_for_xref(row["_xref"])
                except ResourceLoadError:
                    # Ledger collisions belong to the native row diagnostic.
                    continue
                if instance is not None:
                    ids.add(instance.pk if is_head else instance.workflow_id)
                try:
                    identity = resource._adopt_identity(row)
                    adopted = resource._adopt_existing_target(row, identity)
                except (ValueError, ValidationError, ImproperlyConfigured):
                    # The native row lifecycle reports malformed/unresolved input.
                    adopted = None
                if adopted is not None:
                    ids.add(adopted.pk if is_head else adopted.workflow_id)
                if is_head:
                    declared_heads[(group.entry.addon.name, row["_xref"])] = adopted or instance
        for group, resource in facets:
            model = resource.workflow_model
            ledgers = resource.ledger_model._default_manager.using(using).filter(
                source_addon=group.entry.addon.name,
                source_path=group.entry.source,
                target_model=group.model._meta.label,
            )
            # Retained omitted targets are affected even without current xrefs.
            for ledger in ledgers:
                target = ledger.target_instance()
                if target is not None:
                    ids.add(target.pk if group.model is model else target.workflow_id)
            for row in group.dataset.dict:
                for name in ("workflow", "error_workflow", "source", "target"):
                    value = row.get(name)
                    field = resource.fields.get(name)
                    if (
                        not isinstance(value, str) or not value or field is None
                        or not isinstance(field.widget, XrefForeignKeyWidget)
                    ):
                        continue
                    try:
                        target = field.widget.resolve_field_target(value)
                    except ValueError:
                        try:
                            target = declared_heads.get(split_xref(value, resource.addon_aliases))
                        except ValueError:
                            target = None
                    if target is not None:
                        ids.add(target.pk if isinstance(target, model) else target.workflow_id)
        return ids

    def before_import(self, dataset: Any, **kwargs: Any) -> None:
        self._instances.clear()
        self._row_hashes.clear()
        self._pending: dict[str, tuple[Mapping[str, Any], dict[str, Any]]] = {}
        super().before_import(dataset, **kwargs)
        headers = set(dataset.headers or ())
        if any(field.name in headers for field in self._meta.model._meta.many_to_many):
            raise ResourceLoadError("Workflow definition resources do not support imported many-to-many fields.")
        unsupported = headers - self.declaration_fields - {"_xref"}
        if unsupported:
            raise ResourceLoadError(f"Unsupported workflow declaration fields: {', '.join(sorted(unsupported))}")

    def import_instance(self, instance: Any, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Apply native field cleaning after the model-owned omission defaults."""

        if row["_xref"] in self._hash_skips:
            return
        step_model = self.workflow_model._meta.get_field("steps").related_model
        old_class = instance.step_class if isinstance(instance, step_model) else None
        defaults = self._meta.model()
        retained = {"config", "key"} if self._meta.model is self.workflow_model else {"config"}
        for name in self.declaration_fields - set(row) - retained:
            field = instance._meta.get_field(name)
            setattr(instance, field.attname, getattr(defaults, field.attname))
        super().import_instance(instance, row, **kwargs)
        if isinstance(instance, step_model):
            changed_class = instance.step_class != old_class
            if "config" not in row and changed_class:
                instance.config = defaults.config
            if instance._state.adding or "config" in row or changed_class:
                instance.validate_impl_configs()

    def save_instance(self, instance: Any, is_create: bool, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Retain the native cleaned instance; manager persistence follows the dataset."""

        self.before_save_instance(instance, row, **kwargs)
        self._pending[row["_xref"]] = (row, kwargs)

    def save_m2m(self, instance: Any, row: Mapping[str, Any], **kwargs: Any) -> None:
        """These facets have no imported M2M; an unsaved row cannot run native set()."""

    def after_import(self, dataset: Any, result: Any, **kwargs: Any) -> None:
        if result.has_errors() or result.has_validation_errors():
            return
        alias = self.get_db_connection_name()
        declarations = {
            xref: row_result.instance
            for xref, row_result in zip(dataset["_xref"], result.rows, strict=True)
        }
        persisted = self.workflow_model.objects.db_manager(alias).install_definition(
            self._meta.model,
            declarations,
            ledger_model=self.ledger_model,
            source_addon=self.entry.addon.name,
            source_path=self.entry.source,
        )
        for xref, row_result in zip(dataset["_xref"], result.rows, strict=True):
            instance = persisted[xref]
            row_result.instance = instance
            row_result.add_instance_info(instance)
            if row_result.import_type != RowResult.IMPORT_TYPE_SKIP:
                row, row_kwargs = self._pending[xref]
                self.after_save_instance(instance, row, **row_kwargs)
        super().after_import(dataset, result, **kwargs)
