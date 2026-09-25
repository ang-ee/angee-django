"""Native import-export lifecycle adapter for workflow definition facets."""

from __future__ import annotations

import copy
import functools
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from django.apps import apps
from django.db import connection
from import_export import fields
from import_export.results import RowResult

from angee.base.scoping import system_queryset
from angee.resources.exceptions import ResourceLoadError
from angee.resources.loader import AngeeResource
from angee.resources.widgets import XrefForeignKeyWidget, split_xref

logger = logging.getLogger(__name__)


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
        addon, xref = split_xref(ref, self.resource.addon_aliases)
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
        xref = row["_xref"]
        if xref in self._seen_xrefs:
            raise ResourceLoadError(f"duplicate _xref {xref!r} in workflow definition facet")
        self._seen_xrefs.add(xref)
        super().after_init_instance(instance, new, row, **kwargs)
        self._instances[xref] = instance

    @property
    def workflow_model(self) -> type[Any]:
        return apps.get_model("workflows", "Workflow")

    @classmethod
    def lock_imports(cls, loaded_groups: Sequence[tuple[Any, AngeeResource]]) -> None:
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
        if not connection.in_atomic_block:
            raise ResourceLoadError("Workflow resource locks require an active resource transaction.")
        ids = cls._lock_targets(facets)
        locked = list(system_queryset(model, lock=("self",)).filter(pk__in=ids).order_by("pk"))
        if {row.pk for row in locked} != ids or cls._lock_targets(facets) - ids:
            raise ResourceLoadError("Workflow resource parents changed during lock planning; retry the load.")

    @classmethod
    def _lock_targets(cls, facets: Sequence[tuple[Any, WorkflowDefinitionResource]]) -> set[int]:
        # Rebuild on each pass: the post-lock read must see concurrent parent moves.
        model = facets[0][1].workflow_model
        ids: set[int] = set()
        references: set[tuple[tuple[str, str], type[Any]]] = set()
        for group, resource in facets:
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
                        key = split_xref(value, resource.addon_aliases)
                    except ValueError:
                        continue
                    references.add((key, field.widget.model))
        resolved, targets = AngeeResource.resolve_existing(
            [(group.dataset, resource) for group, resource in facets],
            references=sorted({key for key, _model in references}),
        )
        for resolution in resolved.values():
            for target in (resolution.instance, resolution.retained_instance):
                if target is not None:
                    ids.add(target.pk if isinstance(target, model) else target.workflow_id)
        for key, expected_model in references:
            target = targets.get(key)
            if isinstance(target, expected_model):
                ids.add(target.pk if isinstance(target, model) else target.workflow_id)
        return ids

    def before_import(self, dataset: Any, **kwargs: Any) -> None:
        self._row_hashes.clear()
        self._seen_xrefs: set[str] = set()
        self._pending: dict[str, tuple[Mapping[str, Any], dict[str, Any]]] = {}
        super().before_import(dataset, **kwargs)
        headers = set(dataset.headers or ())
        if any(field.name in headers for field in self._meta.model._meta.many_to_many):
            raise ResourceLoadError("Workflow definition resources do not support imported many-to-many fields.")
        unsupported = headers - self._meta.model.declaration_fields - {"_xref"}
        if unsupported:
            raise ResourceLoadError(f"Unsupported workflow declaration fields: {', '.join(sorted(unsupported))}")

    def import_instance(self, instance: Any, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Clean fields, patch nonempty config at the top level, and let ``{}`` clear it."""

        if row["_xref"] in self._hash_skips:
            return
        step_model = self.workflow_model._meta.get_field("steps").related_model
        old_class = instance.step_class if isinstance(instance, step_model) else None
        old_config = (
            copy.deepcopy(instance.config)
            if isinstance(instance, step_model)
            and not instance._state.adding
            and isinstance(instance.config, Mapping)
            else None
        )
        defaults = self._meta.model()
        retained = {"config", "key"} if self._meta.model is self.workflow_model else {"config"}
        for name in self._meta.model.declaration_fields - set(row) - retained:
            field = instance._meta.get_field(name)
            setattr(instance, field.attname, getattr(defaults, field.attname))
        super().import_instance(instance, row, **kwargs)
        if isinstance(instance, step_model):
            changed_class = instance.step_class != old_class
            if "config" not in row and changed_class:
                instance.config = defaults.config
            elif (
                "config" in row
                and not changed_class
                and old_config is not None
                and isinstance(instance.config, Mapping)
                and instance.config
            ):
                # A nonempty resource config is a top-level patch, matching the
                # row's omitted-field contract. Operator-authored keys survive
                # declaration updates only while the step's config contract still
                # declares them; an explicit empty object still clears.
                declared = instance.resolve_impl("step_class").declared_config_keys()
                retired = old_config.keys() - declared if declared is not None else set()
                for key in sorted(retired):
                    logger.warning(
                        "Dropping retired config key %r from step %r (xref=%s.%s).",
                        key,
                        instance.key,
                        self.entry.addon.name,
                        row["_xref"],
                    )
                kept_config = {key: value for key, value in old_config.items() if key not in retired}
                instance.config = {**kept_config, **instance.config}
            if instance._state.adding or "config" in row or changed_class:
                instance.validate_impl_configs()

    def save_instance(self, instance: Any, is_create: bool, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Retain the native cleaned instance; manager persistence follows the dataset."""

        del is_create
        self.before_save_instance(instance, row, **kwargs)
        self._pending[row["_xref"]] = (row, kwargs)

    def save_m2m(self, instance: Any, row: Mapping[str, Any], **kwargs: Any) -> None:
        """These facets have no imported M2M; an unsaved row cannot run native set()."""

    def after_import(self, dataset: Any, result: Any, **kwargs: Any) -> None:
        if result.has_errors() or result.has_validation_errors():
            return
        # Positional row/instance pairing relies on report_skipped, store_instance, skip_diff, use_bulk=False.
        declarations = {
            xref: row_result.instance
            for xref, row_result in zip(dataset["_xref"], result.rows, strict=True)
        }
        persisted = self.workflow_model.objects.install_definition(
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
