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

from angee.base.models import instance_from_public_id, public_id_of
from angee.base.serialization import json_safe
from angee.resources.entries import FROZEN_TIERS, RESERVED_ROW_KEYS, ResourceEntry
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

        adopted = self._adopt_for_row(xref, row, row_hash, ledger)
        if adopted is not None:
            return adopted

        skip = self._skip_decision(
            ledger,
            self._instance_from_ledger(ledger),
            row_hash,
        )
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

        self._ledger_for_xref(xref)
        ledger = self._existing_ledgers[xref]
        if ledger is None:
            return self._adopted_instances.get(xref)
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
        row_hash: str,
        ledger: Resource | None,
    ) -> RowResult | None:
        """Adopt an unledgered target and return any frozen-tier skip."""

        if ledger is not None:
            return None
        adopted = self._adopt_existing_target(row)
        if adopted is None:
            return None
        self._adopted_instances[xref] = adopted
        if self.entry.tier not in FROZEN_TIERS:
            return None
        self._upsert_ledger(
            xref=xref,
            instance=adopted,
            row_hash=row_hash,
        )
        return self._skip_result(adopted)

    def _skip_decision(
        self,
        ledger: Resource | None,
        instance: models.Model | None,
        row_hash: str,
    ) -> RowResult | None:
        """Return a skip result when the ledger row needs no import."""

        if ledger is None:
            return None
        if self.entry.tier in FROZEN_TIERS:
            return self._skip_result(instance)
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

        self.ledger_model._default_manager.update_or_create(
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
        value = row.get(resource_field.column_name)
        if value in (None, ""):
            return None
        return field.name, value

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
            )
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
