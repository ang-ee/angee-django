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

from angee.base.models import AngeeModel, instance_from_public_id, public_id_of
from angee.base.serialization import json_safe
from angee.resources.entries import RESERVED_ROW_KEYS, ResourceEntry
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
        self._adopted_instances: dict[str, models.Model] = {}
        self._row_hashes: dict[str, str] = {}
        self._stale_ledger_xrefs: set[str] = set()
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

    def before_import_row(
        self,
        row: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        """Record the source row hash before widgets clean field values."""

        xref = self._row_xref(row, row_number=kwargs["row_number"])
        self._row_hashes[xref] = self._row_content_hash(row)

    def import_row(
        self,
        row: Mapping[str, Any],
        instance_loader: BaseInstanceLoader,
        **kwargs: Any,
    ) -> RowResult:
        """Return a row import result after ledger skip/adoption checks."""

        row_number = kwargs["row_number"]
        xref = self._row_xref(row, row_number=row_number)
        row_hash = self._row_content_hash(row)
        ledger = self._ledger_for_xref(xref)
        self._record_row_state(xref, row_hash, ledger)
        instance = self._instance_from_ledger(ledger)
        if instance is not None and self._ledger_resolution_is_stale(row, instance):
            # Sqids encode pks, so after a table drop+recreate a surviving
            # ledger sqid resolves to a DIFFERENT, newly created row (pk
            # reuse). The resolved row failed the entry's adopt identity, so
            # the pointer is stale: fall through to adopt-or-create and let
            # ``after_save_instance`` repoint the ledger.
            self._stale_ledger_xrefs.add(xref)
            instance = None

        adopted = self._adopt_for_row(xref, row, ledger, instance)
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

        xref = self._row_xref(row, row_number=kwargs["row_number"])
        self._restore_auto_fields(instance, row)
        self._upsert_ledger(
            xref=xref,
            instance=instance,
            row_hash=self._row_hashes[xref],
        )

    def instance_for_xref(self, xref: str) -> models.Model | None:
        """Return an existing or adopted instance for a row xref."""

        if xref in self._adopted_instances:
            return self._adopted_instances[xref]
        if xref in self._stale_ledger_xrefs:
            # A stale pointer must not resurface through the instance loader:
            # with no adoptable match the row is created fresh, not written
            # over the wrong row the ledger still names.
            return None
        self._ledger_for_xref(xref)
        ledger = self._existing_ledgers[xref]
        if ledger is None:
            return None
        return self._instance_from_ledger(ledger)

    def _prime_existing_ledgers(self, dataset: tablib.Dataset) -> None:
        """Load existing ledger rows for this import dataset in one query."""

        xrefs = {self._row_xref(row, row_number=index) for index, row in enumerate(dataset.dict, start=1)}
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
        ledger: Resource | None,
        instance: models.Model | None,
    ) -> models.Model | None:
        """Adopt a target before normal row import runs when the ledger cannot."""

        if ledger is not None and instance is not None:
            return None
        adopted = self._adopt_existing_target(row)
        if adopted is None:
            return None
        self._adopted_instances[xref] = adopted
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

    def _row_xref(self, row: Mapping[str, Any], *, row_number: int) -> str:
        """Return the normalized xref for one import row."""

        value = row.get("_xref")
        if not isinstance(value, str) or not value.strip():
            raise ResourceLoadError(f"{self.entry.display} row {row_number}: missing _xref")
        return value.strip()

    def _row_content_hash(self, row: Mapping[str, Any]) -> str:
        """Return a deterministic hash for model field values in ``row``."""

        payload = {key: value for key, value in sorted(row.items()) if key not in RESERVED_ROW_KEYS}
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
        instance = instance_from_public_id(
            self._meta.model,
            str(ledger.target_id),
        )
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
        row: Mapping[str, Any],
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

        identity = self._adopt_identity(row)
        if identity is None:
            return False
        for field_name, value in identity.items():
            field = self._meta.model._meta.get_field(field_name)
            if not isinstance(field, models.Field):
                return False
            current = self._prepared_condition_value(field, getattr(instance, field.attname))
            wanted = self._prepared_condition_value(field, value)
            if current != wanted:
                return True
        return False

    def _adopt_identity(self, row: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return the adopt-key field values one row declares, or ``None``.

        Mirrors the three adoption shapes ``_adopt_existing_target`` accepts:
        one named field, a composite tuple, and ``adopt=True`` (the single
        adoptable unique field carried by the row). ``None`` means the entry
        declares no adoption or the row omits the key values.
        """

        adopt = self.entry.adopt
        if not adopt:
            return None
        if isinstance(adopt, str):
            candidate = self._adoption_candidate(row, adopt)
            return None if candidate is None else dict([candidate])
        if isinstance(adopt, tuple):
            return self._composite_adoption_candidate(row, adopt)
        candidates = [
            candidate
            for field in self._meta.model._meta.fields
            if self._is_adoptable_field(field) and (candidate := self._adoption_candidate(row, field.name)) is not None
        ]
        if len(candidates) != 1:
            return None
        return dict(candidates)

    def _adopt_existing_target(
        self,
        row: Mapping[str, Any],
    ) -> models.Model | None:
        """Return an existing unique-field target when adoption is enabled."""

        adopt = self.entry.adopt
        if not adopt:
            return None

        if isinstance(adopt, str):
            candidate = self._adoption_candidate(row, adopt)
            candidates = [] if candidate is None else [candidate]
        elif isinstance(adopt, tuple):
            composite = self._composite_adoption_candidate(row, adopt)
            if composite is None:
                return None
            queryset = self._meta.model._default_manager.filter(**composite)
            condition = self._unique_field_set_condition(adopt)
            if condition is not None:
                if not self._row_matches_condition(row, condition):
                    return None
                queryset = queryset.filter(condition)
            matches = list(queryset[:2])
            if len(matches) != 1:
                return None
            return matches[0]
        else:
            candidates = []
            for field in self._meta.model._meta.fields:
                if not self._is_adoptable_field(field):
                    continue
                candidate = self._adoption_candidate(row, field.name)
                if candidate is not None:
                    candidates.append(candidate)
            if len(candidates) > 1:
                names = ", ".join(name for name, _value in candidates)
                raise ImproperlyConfigured(f"{self.entry.display}: adopt=True matched multiple unique fields: {names}")

        if not candidates:
            return None
        field_name, value = candidates[0]
        matches = list(self._meta.model._default_manager.filter(**{field_name: value})[:2])
        if len(matches) != 1:
            return None
        return matches[0]

    def _composite_adoption_candidate(
        self,
        row: Mapping[str, Any],
        field_names: tuple[str, ...],
    ) -> dict[str, Any] | None:
        """Return row values for one configured composite adoption key."""

        fields = self._unique_adoption_fields(field_names)
        candidate: dict[str, Any] = {}
        for field in fields:
            resource_field = self.fields.get(field.name)
            if resource_field is None:
                raise ImproperlyConfigured(f"{self.entry.display}: adopt field {field.name!r} is not importable")
            if resource_field.column_name not in row:
                return None
            value = self._adoption_field_value(resource_field, row)
            if value in (None, ""):
                return None
            candidate[field.name] = value
        return candidate

    def _adoption_candidate(
        self,
        row: Mapping[str, Any],
        field_name: str,
    ) -> tuple[str, Any] | None:
        """Return a row value for one configured adoption field."""

        field = self._unique_adoption_field(field_name)
        resource_field = self.fields.get(field.name)
        if resource_field is None:
            raise ImproperlyConfigured(f"{self.entry.display}: adopt field {field_name!r} is not importable")
        if resource_field.column_name not in row:
            return None
        value = self._adoption_field_value(resource_field, row)
        if value in (None, ""):
            return None
        return field.name, value

    def _adoption_field_value(self, resource_field: fields.Field, row: Mapping[str, Any]) -> Any:
        """Return one adoption key value after the field's widget cleans it."""

        return resource_field.clean(row)

    def _row_matches_condition(self, row: Mapping[str, Any], condition: models.Q) -> bool:
        """Return whether a resource row satisfies one conditional unique constraint."""

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
            return self._adoption_field_value(resource_field, row)
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
        if not isinstance(field, models.Field) or not self._is_adoptable_field(field):
            raise ImproperlyConfigured(f"{self.entry.display}: adopt field {field_name!r} must be a unique model field")
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

        xref = self.resource._row_xref(row, row_number=0)
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
