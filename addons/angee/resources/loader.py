"""Import-export resource classes for loading Angee resource rows."""

from __future__ import annotations

import functools
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import tablib
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models.fields import NOT_PROVIDED
from import_export import fields, resources
from import_export.instance_loaders import BaseInstanceLoader
from import_export.utils import get_related_model

from angee.base.identity import public_id_lookup, public_id_of
from angee.base.impl import ImplDefaultsMixin
from angee.base.models import AngeeModel
from angee.base.serialization import json_safe
from angee.resources.entries import ResourceEntry, resolve_model
from angee.resources.exceptions import ResourceLoadError
from angee.resources.mixins import ResourceLoadMixin
from angee.resources.widgets import (
    XrefForeignKeyWidget,
    XrefManyToManyWidget,
    XrefWidgetMixin,
    _NativeJSONWidget,
)

if TYPE_CHECKING:
    from angee.resources.models import Resource


class DryRunRollback(Exception):
    """Signal that a successful dry run should roll back its transaction."""


@dataclass(frozen=True)
class ResourceResolution:
    """Existing row target and retained ledger target, which may differ after adoption."""

    instance: models.Model | None
    ledger: Resource | None
    retained_instance: models.Model | None


class AngeeResource(resources.ModelResource):
    """Import-export resource with xref identity and ledger persistence."""

    WIDGETS_MAP = {
        **resources.ModelResource.WIDGETS_MAP,
        "JSONField": lambda **kwargs: _NativeJSONWidget(**kwargs),
    }
    """Widget map that accepts native JSON values from structured files."""

    def __init__(
        self,
        *,
        entry: ResourceEntry,
        ledger_model: type[models.Model],
        addon_aliases: Mapping[str, str],
    ) -> None:
        """Bind one resource entry and concrete ledger model."""

        self.entry = entry
        self.ledger_model = ledger_model
        self.addon_aliases = addon_aliases
        self._existing_ledgers: dict[str, Resource | None] = {}
        self._instances: dict[str, models.Model | None] = {}
        self._row_hashes: dict[str, str] = {}
        self._hash_skips: set[str] = set()
        super().__init__()
        for field in self.fields.values():
            if isinstance(field.widget, XrefWidgetMixin):
                field.widget.ledger_model = ledger_model
                field.widget.addon_aliases = addon_aliases

    @classmethod
    def lock_imports(cls, loaded_groups: Sequence[tuple[Any, AngeeResource]]) -> None:
        """Acquire batch-wide domain locks before imports; never import or write rows.

        Called once per declared resource class inside the loader's transaction.
        A subclass may inspect the complete batch solely to order its locks.
        """

    @classmethod
    def get_fk_widget(cls, field: Any) -> functools.partial[Any]:
        """Return the xref-aware widget factory for a foreign key."""

        return functools.partial(
            XrefForeignKeyWidget,
            model=get_related_model(field),
        )

    @classmethod
    def get_m2m_widget(cls, field: Any) -> functools.partial[Any]:
        """Return the xref-aware widget factory for a many-to-many field."""

        return functools.partial(
            XrefManyToManyWidget,
            model=get_related_model(field),
        )

    def before_import(self, dataset: tablib.Dataset, **kwargs: Any) -> None:
        """Validate headers and reset any batch-planning state before importing."""

        del kwargs
        self._validate_catalogue_tier()
        self._validate_headers(list(dataset.headers or []))
        self._hash_skips = set()
        self._instances.clear()
        self._row_hashes.clear()
        self._prime_existing_ledgers(dataset)

    def before_import_row(self, row: dict[str, Any], **kwargs: Any) -> None:
        """Record row identity inside the native row diagnostic boundary."""

        for name in tuple(row):
            if row[name] is NOT_PROVIDED:
                del row[name]
        row_number = kwargs["row_number"]
        xref = self._row_xref(row.get("_xref"), row_number=row_number)
        row["_xref"] = xref
        self._row_hashes[xref] = self._row_content_hash(row)
        super().before_import_row(row, **kwargs)

    def import_instance(self, instance: models.Model, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Keep hash-skipped instances intact, including operator-authored values."""

        if row["_xref"] not in self._hash_skips:
            super().import_instance(instance, row, **kwargs)

    def skip_row(
        self, instance: Any, original: Any, row: Mapping[str, Any], import_validation_errors: Any = None,
    ) -> bool:
        """Let native import-export record hash skips and all other row outcomes."""

        return row["_xref"] in self._hash_skips or super().skip_row(instance, original, row, import_validation_errors)

    def after_save_instance(
        self,
        instance: models.Model,
        row: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        """Restore auto-managed source values and upsert the ledger row."""

        xref = self._row_xref(row.get("_xref"), row_number=kwargs["row_number"])
        self._restore_auto_fields(instance, row)
        self._upsert_ledger(
            xref=xref,
            instance=instance,
            row_hash=self._row_hashes[xref],
        )
        self._instances[xref] = instance

    def before_save_instance(
        self,
        instance: models.Model,
        row: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        """Mark model fields supplied by import-export before impl defaults run."""

        del kwargs
        if not isinstance(instance, ImplDefaultsMixin):
            return
        imported_fields = {
            field.attribute
            for field in self.fields.values()
            if not field.readonly and isinstance(field.attribute, str) and field.column_name in row
        }
        instance.mark_impl_provided_fields(imported_fields)

    def instance_for_xref(self, xref: str) -> models.Model | None:
        """Return an existing or adopted instance for a row xref."""

        if xref not in self._instances:
            ledger = self._ledger_for_xref(xref)
            self._instances[xref] = self._instance_from_ledger(ledger)
        return self._instances[xref]

    def instance_for_row(self, row: Mapping[str, Any]) -> models.Model | None:
        """Resolve row identity and record native import hash skips."""

        xref = self._row_xref(row.get("_xref"), row_number=0)
        ledger = self._ledger_for_xref(xref)
        self._check_ledger_target(xref, ledger)
        identity = self._adopt_identity(row)
        instance = self.instance_for_xref(xref)
        if instance is not None and self._ledger_resolution_is_stale(identity, instance):
            # A surviving ledger may point at a reused pk. Adoption repairs it.
            instance = None
        if instance is None:
            instance = self._adopt_existing_target(row, identity)
        elif ledger is not None and ledger.content_hash == self._row_hashes.get(xref):
            self._hash_skips.add(xref)
        self._instances[xref] = instance
        return instance

    @staticmethod
    def resolve_existing(
        batches: Sequence[tuple[tablib.Dataset, AngeeResource]],
        *,
        references: Sequence[tuple[str, str]] = (),
    ) -> tuple[dict[tuple[AngeeResource, str], ResourceResolution], dict[tuple[str, str], models.Model | None]]:
        """Resolve batch rows, retained ledgers, and related xrefs without importing.

        Return owned resolutions keyed by resource/xref and related targets
        keyed by addon/xref. Source moves retain each facet's resolution.
        Each call refreshes batched ledger/target reads and delegates adoption
        to the native instance loader. Omitted rows retain their ledger target;
        stale ledger targets remain available alongside their adopted target.
        Invalid input is left to the native import's source-row diagnostics.
        No import hooks, row writes, or ledger writes run here. Planning primes
        per-import caches; ``before_import`` resets them before row processing.
        """

        if not batches:
            return {}, {}
        ledgers: dict[tuple[str, str], Resource | None] = {}
        for dataset, resource in batches:
            resource._instances.clear()
            resource._row_hashes.clear()
            resource._hash_skips.clear()
            resource._prime_existing_ledgers(dataset)
            resource._existing_ledgers.update(
                (ledger.xref, ledger)
                for ledger in resource.ledger_model._default_manager.filter(
                    source_addon=resource.entry.addon.name,
                    source_path=resource.entry.source,
                    target_model=resource._meta.model._meta.label,
                )
            )
            ledgers.update(
                ((resource.entry.addon.name, xref), ledger)
                for xref, ledger in resource._existing_ledgers.items()
            )
        missing: dict[str, set[str]] = defaultdict(set)
        for key in references:
            if key not in ledgers:
                missing[key[0]].add(key[1])
        for addon, xrefs in missing.items():
            ledgers.update(
                ((addon, ledger.xref), ledger)
                for ledger in batches[0][1].ledger_model._default_manager.filter(source_addon=addon, xref__in=xrefs)
            )
        lookups: dict[tuple[str, str], tuple[type[models.Model], str, Any]] = {}
        values_by_field: dict[tuple[type[models.Model], str], set[Any]] = defaultdict(set)
        for key, ledger in ledgers.items():
            if ledger is None or not ledger.target_id:
                continue
            try:
                model = resolve_model(ledger.target_model)
                (field_name, value), = public_id_lookup(model, ledger.target_id).items()
                field = cast(
                    "models.Field[Any, Any]",
                    model._meta.pk if field_name == "pk" else model._meta.get_field(field_name),
                )
                # Virtual identities expose their concrete column through Django.
                target_field = field.get_col(model._meta.db_table).target
                field_name = target_field.name
                value = target_field.to_python(field.get_prep_value(value))
                lookups[key] = (model, field_name, value)
                values_by_field[(model, field_name)].add(value)
            except (ImproperlyConfigured, TypeError, ValueError, ValidationError):
                continue
        instances = {
            (model, field_name, value): instance
            for (model, field_name), values in values_by_field.items()
            for value, instance in model._default_manager.in_bulk(values, field_name=field_name).items()
        }
        targets = {key: instances.get(lookup) for key, lookup in lookups.items()}
        resolved: dict[tuple[AngeeResource, str], ResourceResolution] = {}
        for _dataset, resource in batches:
            addon = resource.entry.addon.name
            for xref, ledger in resource._existing_ledgers.items():
                target = targets.get((addon, xref))
                if ledger is not None and ledger.target_model != resource._meta.model._meta.label:
                    target = None
                resource._instances[xref] = target
                resolved[(resource, xref)] = ResourceResolution(None, ledger, target)
        for dataset, resource in batches:
            addon = resource.entry.addon.name
            loader = resource._meta.instance_loader_class(resource, dataset)
            for source_row in dataset.dict:
                row = {name: value for name, value in source_row.items() if value is not NOT_PROVIDED}
                try:
                    xref = resource._row_xref(row.get("_xref"), row_number=0)
                    instance = resource.get_instance(loader, row)
                except (ResourceLoadError, ValueError, ValidationError, ImproperlyConfigured):
                    continue
                retained = resolved[(resource, xref)]
                resolved[(resource, xref)] = ResourceResolution(instance, retained.ledger, retained.retained_instance)
                targets[(addon, xref)] = instance or retained.retained_instance
        return resolved, {key: targets.get(key) for key in references}

    def _prime_existing_ledgers(self, dataset: tablib.Dataset) -> None:
        """Load existing ledger rows for this import dataset in one query."""

        xrefs = {self._row_xref(value, row_number=index) for index, value in enumerate(dataset["_xref"], start=1)}
        self._existing_ledgers = {xref: None for xref in xrefs}
        if not xrefs:
            return
        ledgers = self.ledger_model._default_manager.filter(
            source_addon=self.entry.addon.name,
            xref__in=xrefs,
        )
        for ledger in ledgers:
            self._existing_ledgers[str(getattr(ledger, "xref"))] = cast(
                "Resource",
                ledger,
            )

    def _row_xref(self, value: Any, *, row_number: int) -> str:
        """Return the normalized xref for one import row."""

        if not isinstance(value, str) or not value.strip():
            raise ResourceLoadError(f"{self.entry.display} row {row_number}: missing _xref")
        return value.strip()

    def _row_content_hash(self, row: Mapping[str, Any]) -> str:
        """Return a deterministic hash for model field values in ``row``."""

        payload = {key: value for key, value in sorted(row.items()) if key != "_xref"}
        body = json.dumps(
            json_safe(payload),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(body).hexdigest()}"

    def _ledger_for_xref(self, xref: str) -> Resource | None:
        """Return this entry's ledger row for ``xref`` if it exists."""

        if xref in self._existing_ledgers:
            return self._existing_ledgers[xref]
        ledger = (
            self.ledger_model._default_manager.filter(
                source_addon=self.entry.addon.name,
                xref=xref,
            )
            .order_by("pk")
            .first()
        )
        self._existing_ledgers[xref] = cast("Resource | None", ledger)
        ledger = self._existing_ledgers[xref]
        self._check_ledger_target(xref, ledger)
        return ledger

    def _check_ledger_target(
        self,
        xref: str,
        ledger: Resource | None,
    ) -> None:
        """Raise when an existing xref belongs to another target model."""

        if ledger is None:
            return
        expected = self._meta.model._meta.label
        if ledger.target_model != expected:
            raise ResourceLoadError(
                f"xref collision in {self.entry.addon.name}: {xref!r} "
                f"already targets {ledger.target_model}, not {expected}"
            )

    def _upsert_ledger(
        self,
        *,
        xref: str,
        instance: models.Model,
        row_hash: str,
    ) -> None:
        """Create or update the ledger row for an imported object."""

        ledger, _ = self.ledger_model._default_manager.update_or_create(
            source_addon=self.entry.addon.name,
            xref=xref,
            defaults={
                "source_path": self.entry.source,
                "target_model": self._meta.model._meta.label,
                "content_hash": row_hash,
                "target_id": public_id_of(instance),
                "tier": self.entry.tier,
            },
        )
        self._existing_ledgers[xref] = ledger

    def _instance_from_ledger(
        self,
        ledger: Resource | None,
    ) -> models.Model | None:
        """Resolve a ledger row to an instance of this resource's model."""

        if ledger is None or not ledger.target_id:
            return None
        instance = ledger.target_instance()
        if instance is None:
            return None
        expected = self._meta.model._meta.concrete_model
        if instance._meta.concrete_model is not expected:
            raise ResourceLoadError(
                f"{self.entry.display}: {ledger.xref} targets "
                f"{instance._meta.label}, not {self._meta.model._meta.label}"
            )
        return instance

    def _ledger_resolution_is_stale(
        self,
        identity: dict[str, Any] | None,
        instance: models.Model,
    ) -> bool:
        """Whether a ledger-resolved live row fails the entry's adopt identity.

        The adopt key is the seed's declared natural identity, so a resolved
        row that disagrees on it marks the ledger pointer stale rather than the
        row adopted. Entries without an adopt key carry no seed-side identity
        to check, and a row that omits its adopt values offers nothing to
        compare — both trust the resolved row as-is (the prior behavior; xref
        plus target-model checks still apply).
        """

        if identity is None or (self.entry.adopt is True and len(identity) != 1):
            return False
        for field_name, value in identity.items():
            field = self._meta.model._meta.get_field(field_name)
            if not isinstance(field, models.Field):
                return False
            current = self._prepared_condition_value(field, getattr(instance, field.attname))
            wanted = self._prepared_condition_value(field, value)
            if (
                isinstance(self.entry.adopt, str)
                and self.entry.adopt == field_name
                and current in (None, "")
                and wanted not in (None, "")
                and self._adoption_condition is not None
            ):
                # A conditional stable key may be introduced after the ledger
                # already owns the row. Its empty sentinel is unassigned, not a
                # conflicting natural identity; the model still owns whether
                # this one-time assignment is valid.
                continue
            if current != wanted:
                return True
        return False

    @functools.cached_property
    def _adoption_fields(self) -> tuple[models.Field[Any, Any], ...]:
        """Resolve and validate the declaration once for this model import."""

        adopt = self.entry.adopt
        if isinstance(adopt, str):
            return (self._unique_adoption_field(adopt),)
        if isinstance(adopt, tuple):
            return self._unique_adoption_fields(adopt)
        if adopt:
            return tuple(field for field in self._meta.model._meta.fields if self._is_adoptable_field(field))
        return ()

    @functools.cached_property
    def _adoption_condition(self) -> models.Q | None:
        """Read the selected model-owned uniqueness condition once."""

        adopt = self.entry.adopt
        if isinstance(adopt, str):
            field = self._adoption_fields[0]
            return None if field.unique else self._unique_field_set_condition((field.name,))
        if isinstance(adopt, tuple):
            return self._unique_field_set_condition(adopt)
        return None

    def _adopt_identity(self, row: Mapping[str, Any]) -> dict[str, Any] | None:
        """Prepare one row's natural identity once through native field widgets."""

        if not self.entry.adopt:
            return None
        condition = self._adoption_condition
        if isinstance(self.entry.adopt, str) and condition is not None:
            if not self._row_matches_condition(row, condition):
                return None
        identity: dict[str, Any] = {}
        for field in self._adoption_fields:
            resource_field = self.fields.get(field.name)
            if resource_field is None:
                raise ImproperlyConfigured(f"{self.entry.display}: adopt field {field.name!r} is not importable")
            value = resource_field.clean(row) if resource_field.column_name in row else None
            if value in (None, ""):
                if self.entry.adopt is True:
                    continue
                return None
            identity[field.name] = value
        return identity or None

    def _adopt_existing_target(
        self,
        row: Mapping[str, Any],
        identity: dict[str, Any] | None,
    ) -> models.Model | None:
        """Find the prepared natural identity without repeating row coercion."""

        if identity is None:
            return None
        if self.entry.adopt is True and len(identity) > 1:
            names = ", ".join(identity)
            raise ImproperlyConfigured(f"{self.entry.display}: adopt=True matched multiple unique fields: {names}")
        condition = self._adoption_condition
        if isinstance(self.entry.adopt, tuple) and condition is not None:
            if not self._row_matches_condition(row, condition):
                return None
        queryset = self._meta.model._default_manager.filter(**identity)
        if condition is not None:
            queryset = queryset.filter(condition)
        matches = list(queryset[:2])
        if len(matches) > 1 and isinstance(self.entry.adopt, str):
            raise ImproperlyConfigured(f"{self.entry.display}: adopt field {self.entry.adopt!r} matched multiple rows")
        return matches[0] if len(matches) == 1 else None

    def _row_matches_condition(self, row: Mapping[str, Any], condition: models.Q) -> bool:
        """Match the bounded exact/isnull adoption declaration without I/O.

        Django Q.check executes SQL and returns True after DatabaseError.
        Keep this narrower fail-fast contract until a native strict evaluator
        exists; expanding its lookup grammar is not an import concern.
        """

        results: list[bool] = []
        for child in condition.children:
            if isinstance(child, models.Q):
                results.append(self._row_matches_condition(row, child))
                continue
            lookup, expected = child
            results.append(self._row_matches_lookup(row, str(lookup), expected))
        if condition.connector == models.Q.OR:
            matched = any(results)
        elif condition.connector == models.Q.AND:
            matched = all(results)
        else:
            raise ImproperlyConfigured(
                f"{self.entry.display}: adopt condition connector {condition.connector!r} is not supported"
            )
        return not matched if condition.negated else matched

    def _row_matches_lookup(self, row: Mapping[str, Any], lookup: str, expected: Any) -> bool:
        """Return whether a row value satisfies one supported Q lookup."""

        parts = lookup.split("__")
        operator = "exact"
        if parts[-1] in {"exact", "isnull"}:
            operator = parts.pop()
        if len(parts) != 1:
            raise ImproperlyConfigured(f"{self.entry.display}: adopt condition lookup {lookup!r} is not supported")
        field = self._condition_field(parts[0])
        value = self._condition_field_value(field, row)
        if operator == "isnull":
            return (value is None) is bool(expected)
        if operator == "exact":
            return self._prepared_condition_value(field, value) == self._prepared_condition_value(field, expected)
        raise ImproperlyConfigured(f"{self.entry.display}: adopt condition lookup {lookup!r} is not supported")

    def _condition_field(self, field_name: str) -> models.Field[Any, Any]:
        """Return one model field named by a conditional unique constraint."""

        try:
            field = self._meta.model._meta.get_field(field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(
                f"{self.entry.display}: adopt condition field {field_name!r} does not exist"
            ) from error
        if not isinstance(field, models.Field):
            raise ImproperlyConfigured(f"{self.entry.display}: adopt condition field {field_name!r} is not importable")
        return field

    def _condition_field_value(self, field: models.Field[Any, Any], row: Mapping[str, Any]) -> Any:
        """Return one condition field value from the row or the model default."""

        resource_field = self.fields.get(field.name)
        if resource_field is not None and resource_field.column_name in row:
            return resource_field.clean(row)
        if field.has_default():
            return field.get_default()
        return None

    def _prepared_condition_value(self, field: models.Field[Any, Any], value: Any) -> Any:
        """Return one condition value normalized for model-field comparison."""

        if isinstance(field, models.ForeignKey) and isinstance(value, models.Model):
            value = value.pk
        return field.get_prep_value(value)

    def _unique_adoption_field(
        self,
        field_name: str,
    ) -> models.Field[Any, Any]:
        """Return the unique model field named by an adoption declaration."""

        try:
            field = self._meta.model._meta.get_field(field_name)
        except FieldDoesNotExist as error:
            raise ImproperlyConfigured(f"{self.entry.display}: adopt field {field_name!r} does not exist") from error
        if not isinstance(field, models.Field) or field.primary_key:
            raise ImproperlyConfigured(f"{self.entry.display}: adopt field {field_name!r} must be a unique model field")
        if not field.unique:
            found, condition = self._find_unique_field_set_condition((field_name,))
            if not found or condition is None:
                raise ImproperlyConfigured(
                    f"{self.entry.display}: adopt field {field_name!r} must be a unique model field"
                )
        return field

    def _unique_adoption_fields(
        self,
        field_names: tuple[str, ...],
    ) -> tuple[models.Field[Any, Any], ...]:
        """Return model fields named by a composite adoption declaration."""

        if not field_names:
            raise ImproperlyConfigured(f"{self.entry.display}: adopt fields must not be empty")
        if len(set(field_names)) != len(field_names):
            raise ImproperlyConfigured(f"{self.entry.display}: adopt fields must not contain duplicates")
        if len(field_names) == 1:
            return (self._unique_adoption_field(field_names[0]),)

        fields: list[models.Field[Any, Any]] = []
        for field_name in field_names:
            try:
                field = self._meta.model._meta.get_field(field_name)
            except FieldDoesNotExist as error:
                raise ImproperlyConfigured(
                    f"{self.entry.display}: adopt field {field_name!r} does not exist"
                ) from error
            if not isinstance(field, models.Field) or field.primary_key:
                raise ImproperlyConfigured(
                    f"{self.entry.display}: adopt field {field_name!r} must be a non-primary-key model field"
                )
            fields.append(field)
        if not self._has_unique_field_set(field_names):
            names = ", ".join(repr(name) for name in field_names)
            raise ImproperlyConfigured(
                f"{self.entry.display}: adopt fields ({names}) must match a unique model constraint"
            )
        return tuple(fields)

    def _has_unique_field_set(self, field_names: tuple[str, ...]) -> bool:
        """Return whether ``field_names`` identify a model-owned unique constraint."""

        return self._find_unique_field_set_condition(field_names)[0]

    def _unique_field_set_condition(self, field_names: tuple[str, ...]) -> models.Q | None:
        """Return the unique constraint condition for one adoption key, if any."""

        found, condition = self._find_unique_field_set_condition(field_names)
        return condition if found else None

    def _find_unique_field_set_condition(self, field_names: tuple[str, ...]) -> tuple[bool, models.Q | None]:
        """Return whether ``field_names`` match a unique constraint and its condition."""

        expected = frozenset(field_names)
        for unique_together in self._meta.model._meta.unique_together:
            if frozenset(unique_together) == expected:
                return True, None
        for constraint in self._meta.model._meta.constraints:
            if not isinstance(constraint, models.UniqueConstraint):
                continue
            if getattr(constraint, "expressions", ()):
                continue
            if frozenset(getattr(constraint, "fields", ())) == expected:
                return True, getattr(constraint, "condition", None)
        return False, None

    def _is_adoptable_field(self, field: models.Field[Any, Any]) -> bool:
        """Return whether ``field`` can identify an adopted target."""

        return not field.primary_key and bool(getattr(field, "unique", False))

    def _validate_headers(self, headers: Sequence[str]) -> None:
        """Reject primary-key and unknown field headers."""

        allowed = set(self.fields) | {field.column_name for field in self.fields.values()}
        pk = self._meta.model._meta.pk
        primary_keys = {pk.name, pk.attname}

        blocked = sorted(set(headers) & primary_keys)
        if blocked:
            names = ", ".join(blocked)
            raise ResourceLoadError(f"{self.entry.display}: primary key field(s) are managed by _xref: {names}")

        unknown = sorted(set(headers) - allowed)
        if unknown:
            names = ", ".join(unknown)
            raise ResourceLoadError(
                f"{self.entry.display}: unknown field(s) for {self._meta.model._meta.label}: {names}"
            )

    def _validate_catalogue_tier(self) -> None:
        """Reject resource manifests whose tier disagrees with a catalogue model."""

        model = self._meta.model
        if not issubclass(model, AngeeModel):
            return
        catalogue_model = cast(type[AngeeModel], model)
        if not catalogue_model.is_catalogue_model():
            return
        declared_tiers = catalogue_model.get_catalogue_tiers()
        if self.entry.tier in declared_tiers:
            return
        raise ResourceLoadError(
            f"{self.entry.display}: catalogue tier mismatch for {model._meta.label}; "
            f"manifest tier {self.entry.tier!r}, model declares {declared_tiers!r}"
        )

    def _restore_auto_fields(
        self,
        instance: models.Model,
        row: Mapping[str, Any],
    ) -> None:
        """Persist explicit values for auto-managed fields when provided."""

        updates: dict[str, Any] = {}
        for field in self._meta.model._meta.fields:
            if field.name not in row:
                continue
            if not getattr(field, "auto_now", False) and not getattr(
                field,
                "auto_now_add",
                False,
            ):
                continue
            resource_field = self.fields.get(field.name)
            if resource_field is None:
                continue
            updates[field.name] = resource_field.clean(row)

        if not updates:
            return
        type(instance)._default_manager.filter(pk=instance.pk).update(**updates)
        instance.refresh_from_db(fields=list(updates))


class XrefInstanceLoader(BaseInstanceLoader):
    """Resolve existing import rows through ledger identity and declared adoption."""

    resource: AngeeResource

    def get_instance(self, row: Mapping[str, Any]) -> models.Model | None:
        """Return the existing target for one dataset row."""

        return self.resource.instance_for_row(row)


def build_resource(
    model: type[models.Model],
    entry: ResourceEntry,
    *,
    ledger_model: type[models.Model],
    addon_aliases: Mapping[str, str],
) -> AngeeResource:
    """Compose the model's ``resource_class`` with native xref import options.

    ResourceLoadMixin declares the adapter; other models use AngeeResource.
    A custom declaration must subclass AngeeResource so every
    adapter retains the same identity, row diagnostics and canonical ledger.
    """

    resource_class = model.resource_class if issubclass(model, ResourceLoadMixin) else None
    if resource_class is None:
        resource_class = AngeeResource
    if not isinstance(resource_class, type) or not issubclass(resource_class, AngeeResource):
        raise ImproperlyConfigured(f"{model._meta.label}.resource_class must subclass AngeeResource")
    resource_type = resources.modelresource_factory(
        model,
        resource_class=resource_class,
        meta_options={
            "clean_model_instances": True,
            "import_id_fields": (),
            "instance_loader_class": XrefInstanceLoader,
            "report_skipped": True,
            "skip_diff": True,
            "store_instance": True,
            "use_bulk": False,
        },
        custom_fields={
            "_xref": fields.Field(
                attribute=None,
                column_name="_xref",
                readonly=True,
            ),
        },
    )
    return cast(
        AngeeResource,
        resource_type(
            entry=entry,
            ledger_model=ledger_model,
            addon_aliases=addon_aliases,
        ),
    )
