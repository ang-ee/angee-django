"""Native import-export lifecycle adapter for workflow definition facets."""

from __future__ import annotations

import copy
import functools
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connections
from django.db.models.fields import NOT_PROVIDED
from import_export import fields
from import_export.results import RowResult

from angee.base.db import get_write_alias
from angee.base.permissions import require_authorization_database
from angee.base.scoping import system_queryset
from angee.resources.entries import resolve_model
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
        require_authorization_database(alias, operation="Workflow resource locks", error_class=ResourceLoadError)
        if not connections[alias].in_atomic_block:
            raise ResourceLoadError("Workflow resource locks require an active resource transaction.")
        ids = cls._lock_targets(facets, using=alias)
        locked = list(system_queryset(model, using=alias, lock=("self",)).filter(pk__in=ids).order_by("pk"))
        if {row.pk for row in locked} != ids or cls._lock_targets(facets, using=alias) - ids:
            raise ResourceLoadError("Workflow resource parents changed during lock planning; retry the load.")

    @classmethod
    def _lock_targets(cls, facets: Sequence[tuple[Any, WorkflowDefinitionResource]], *, using: str) -> set[int]:
        # Rebuild on each pass: the post-lock read must see concurrent parent moves.
        ledgers: dict[tuple[str, str], Any] = {}
        owned: list[tuple[Any, Any]] = []
        references: list[tuple[tuple[str, str], type[Any]]] = []
        missing: dict[str, set[str]] = defaultdict(set)
        for group, resource in facets:
            resource._instances.clear()
            resource._prime_existing_ledgers(group.dataset)
            ledgers.update(
                ((group.entry.addon.name, xref), ledger) for xref, ledger in resource._existing_ledgers.items()
            )
            # Retained omitted targets are affected even without current xrefs.
            for ledger in resource.ledger_model._default_manager.using(using).filter(
                source_addon=group.entry.addon.name,
                source_path=group.entry.source,
                target_model=group.model._meta.label,
            ):
                ledgers[(ledger.source_addon, ledger.xref)] = ledger
                owned.append((group.model, ledger))
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
                    references.append((key, field.widget.model))
        for key, _model in references:
            if key not in ledgers:
                missing[key[0]].add(key[1])
        ledger_model = facets[0][1].ledger_model
        for addon, xrefs in missing.items():
            for ledger in ledger_model._default_manager.using(using).filter(source_addon=addon, xref__in=xrefs):
                ledgers[(addon, ledger.xref)] = ledger

        model = facets[0][1].workflow_model
        graph_models = (
            model, model._meta.get_field("steps").related_model, model._meta.get_field("edges").related_model,
        )
        target_pks: dict[type[Any], dict[tuple[str, str], int | None]] = defaultdict(dict)
        for key, ledger in ledgers.items():
            if ledger is None or not ledger.target_id:
                continue
            try:
                target_model = resolve_model(ledger.target_model)
                if issubclass(target_model, graph_models):
                    field = target_model._meta.get_field("sqid")
                    target_pks[target_model][key] = field.public_id_to_value(ledger.target_id)
            except (ImproperlyConfigured, TypeError, ValueError):
                # Invalid ledger identities remain native row diagnostics.
                continue
        targets: dict[tuple[str, str], Any] = {}
        for target_model, pks in target_pks.items():
            rows = target_model._default_manager.using(using).in_bulk({pk for pk in pks.values() if pk is not None})
            targets.update((key, rows.get(pk)) for key, pk in pks.items())

        ids: set[int] = set()
        declared_heads: dict[tuple[str, str], Any] = {}
        # Any facet may adopt an existing target without a ledger yet. Keep
        # declared heads available to references elsewhere in the batch too.
        for group, resource in facets:
            is_head = group.model is resource.workflow_model
            for row in group.dataset.dict:
                row = {name: value for name, value in row.items() if value is not NOT_PROVIDED}
                try:
                    resource._check_ledger_target(row["_xref"], resource._existing_ledgers.get(row["_xref"]))
                except ResourceLoadError:
                    # Ledger collisions belong to the native row diagnostic.
                    continue
                instance = targets.get((group.entry.addon.name, row["_xref"]))
                resource._instances[row["_xref"]] = instance
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
        for target_model, ledger in owned:
            target = targets.get((ledger.source_addon, ledger.xref))
            if target is not None:
                ids.add(target.pk if target_model is model else target.workflow_id)
        for key, expected_model in references:
            target = targets.get(key)
            if not isinstance(target, expected_model):
                target = declared_heads.get(key)
            if target is not None:
                ids.add(target.pk if isinstance(target, model) else target.workflow_id)
        return ids

    def before_import(self, dataset: Any, **kwargs: Any) -> None:
        self._instances.clear()
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
                retained = old_config if declared is None else {
                    key: value for key, value in old_config.items() if key in declared
                }
                instance.config = {**retained, **instance.config}
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
        alias = self.get_db_connection_name()
        # Positional row/instance pairing relies on report_skipped, store_instance, skip_diff, use_bulk=False.
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
