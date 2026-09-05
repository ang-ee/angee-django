"""Import-export resource classes for loading Angee resource rows."""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

import tablib
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from import_export import fields, resources
from import_export.instance_loaders import BaseInstanceLoader
from import_export.results import RowResult
from import_export.utils import get_related_model

from angee.base.identity import public_id_of
from angee.base.models import AngeeModel
from angee.base.serialization import json_safe
from angee.resources.entries import ResourceEntry
from angee.resources.exceptions import ResourceLoadError
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
        super().__init__()
        for field in self.fields.values():
            if isinstance(field.widget, XrefWidgetMixin):
                field.widget.ledger_model = ledger_model
                field.widget.addon_aliases = addon_aliases

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
        """Validate incoming headers before import-export reads rows."""

        del kwargs
        self._validate_catalogue_tier()
        self._validate_headers(list(dataset.headers or []))
        self._prime_existing_ledgers(dataset)

    def import_row(
        self,
        row: Mapping[str, Any],
        instance_loader: BaseInstanceLoader,
        **kwargs: Any,
    ) -> RowResult:
        """Return a row import result after ledger skip/adoption checks."""

        row_number = kwargs["row_number"]
        xref = self._row_xref(row.get("_xref"), row_number=row_number)
        row_hash = self._row_content_hash(row)
        ledger = self._ledger_for_xref(xref)
        self._record_row_state(xref, row_hash, ledger)
        identity = self._adopt_identity(row)
        instance = self._instance_from_ledger(ledger)
        if instance is not None and self._ledger_resolution_is_stale(identity, instance):
            # Sqids encode pks, so after a table drop+recreate a surviving
            # ledger sqid resolves to a DIFFERENT, newly created row (pk
            # reuse). The resolved row failed the entry's adopt identity, so
            # the pointer is stale: fall through to adopt-or-create and let
            # ``after_save_instance`` repoint the ledger.
            instance = None

        self._instances[xref] = instance
        adopted = self._adopt_for_row(xref, row, identity, ledger, instance)
        if adopted is None:
            skip = self._skip_decision(ledger, instance, row_hash)
            if skip is not None:
                return skip

        return cast(
            RowResult,
            super().import_row(row, instance_loader, **kwargs),
        )

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

    def instance_for_xref(self, xref: str) -> models.Model | None:
        """Return an existing or adopted instance for a row xref."""

        if xref not in self._instances:
            ledger = self._ledger_for_xref(xref)
            self._instances[xref] = self._instance_from_ledger(ledger)
        return self._instances[xref]

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

    def _record_row_state(
        self,
        xref: str,
        row_hash: str,
        ledger: Resource | None,
    ) -> None:
        """Record the ledger and content hash for one row."""

        self._check_ledger_target(xref, ledger)
        self._existing_ledgers[xref] = ledger
        self._row_hashes[xref] = row_hash

    def _adopt_for_row(
        self,
        xref: str,
        row: Mapping[str, Any],
        identity: dict[str, Any] | None,
        ledger: Resource | None,
        instance: models.Model | None,
    ) -> models.Model | None:
        """Adopt a target before normal row import runs when the ledger cannot."""

        if ledger is not None and instance is not None:
            return None
        adopted = self._adopt_existing_target(row, identity)
        if adopted is None:
            return None
        self._instances[xref] = adopted
        return adopted

    def _skip_decision(
        self,
        ledger: Resource | None,
        instance: models.Model | None,
        row_hash: str,
    ) -> RowResult | None:
        """Return a skip result when the ledger row needs no import."""

        if ledger is None:
            return None
        if instance is not None and ledger.content_hash == row_hash:
            return self._skip_result(instance)
        return None

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

    def _skip_result(self, instance: models.Model | None) -> RowResult:
        """Return an import-export skip result for one row."""

        row_result = self.get_row_result_class()()
        row_result.import_type = RowResult.IMPORT_TYPE_SKIP
        if instance is not None:
            row_result.add_instance_info(instance)
            if self._meta.store_instance:
                row_result.instance = instance
        return row_result

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
        declared_tier = str(catalogue_model.get_catalogue_tier())
        if self.entry.tier == declared_tier:
            return
        raise ResourceLoadError(
            f"{self.entry.display}: catalogue tier mismatch for {model._meta.label}; "
            f"manifest tier {self.entry.tier!r}, model declares {declared_tier!r}"
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
    """Resolve existing import rows through the resource ledger."""

    resource: AngeeResource

    def get_instance(self, row: Mapping[str, Any]) -> models.Model | None:
        """Return the existing target for one dataset row."""

        xref = self.resource._row_xref(row.get("_xref"), row_number=0)
        return self.resource.instance_for_xref(xref)


def build_resource(
    model: type[models.Model],
    entry: ResourceEntry,
    *,
    ledger_model: type[models.Model],
    addon_aliases: Mapping[str, str],
) -> AngeeResource:
    """Return an xref-aware import-export resource for ``model``."""

    resource_type = resources.modelresource_factory(
        model,
        resource_class=AngeeResource,
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
