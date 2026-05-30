"""Import-export resource and helpers for addon resource loading."""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import tablib
from django.db import models
from import_export import fields, resources
from import_export.instance_loaders import BaseInstanceLoader
from import_export.results import RowResult
from import_export.utils import get_related_model

from angee.base.models import instance_from_public_id, public_id_of
from angee.base.resources.entries import (
    FROZEN_TIERS,
    RESERVED_ROW_KEYS,
    ResourceEntry,
)
from angee.base.resources.exceptions import ResourceLoadError

if TYPE_CHECKING:
    from angee.base.resources.models import Resource
from angee.base.resources.widgets import (
    NativeJSONWidget,
    XrefForeignKeyWidget,
    XrefManyToManyWidget,
    XrefWidgetMixin,
)


class DryRunRollback(Exception):
    """Internal sentinel used to roll back a successful dry run."""


class AngeeResource(resources.ModelResource):
    """ModelResource with Angee xref identity and widgets."""

    WIDGETS_MAP = {
        **resources.ModelResource.WIDGETS_MAP,
        "JSONField": lambda **kwargs: NativeJSONWidget(**kwargs),
    }

    def __init__(
        self,
        *,
        entry: ResourceEntry,
        ledger_model: type[models.Model],
    ) -> None:
        self.entry = entry
        self.ledger_model = ledger_model
        self._existing_ledgers: dict[str, Resource | None] = {}
        self._adopted_instances: dict[str, models.Model] = {}
        self._row_hashes: dict[str, str] = {}
        super().__init__()
        for field in self.fields.values():
            if isinstance(field.widget, XrefWidgetMixin):
                field.widget.ledger_model = ledger_model

    @classmethod
    def get_fk_widget(cls, field: Any) -> functools.partial[Any]:
        """Return xref-aware FK widget via import-export's hook."""

        return functools.partial(
            XrefForeignKeyWidget,
            model=get_related_model(field),
        )

    @classmethod
    def get_m2m_widget(cls, field: Any) -> functools.partial[Any]:
        """Return xref-aware M2M widget via import-export's hook."""

        return functools.partial(
            XrefManyToManyWidget,
            model=get_related_model(field),
        )

    def before_import(self, dataset: tablib.Dataset, **kwargs: Any) -> None:
        """Validate file headers before import-export mutates rows."""

        del kwargs
        self._validate_headers(list(dataset.headers or []))

    def before_import_row(
        self,
        row: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        """Remember row hashes before import-export cleans values."""

        xref = _row_xref(self.entry, row, row_number=kwargs["row_number"])
        self._row_hashes[xref] = _row_content_hash(row)

    def import_row(
        self,
        row: Mapping[str, Any],
        instance_loader: BaseInstanceLoader,
        **kwargs: Any,
    ) -> RowResult:
        """Skip frozen or unchanged ledger rows; adopt existing targets."""

        row_number = kwargs["row_number"]
        xref = _row_xref(self.entry, row, row_number=row_number)
        row_hash = _row_content_hash(row)
        ledger = self._ledger_for_xref(xref)
        self._existing_ledgers[xref] = ledger
        self._row_hashes[xref] = row_hash
        instance = self._instance_from_ledger(ledger)

        if ledger is None:
            adopted = _adopt_existing_target(self, row)
            if adopted is not None:
                self._adopted_instances[xref] = adopted
                if self.entry.tier in FROZEN_TIERS:
                    self._upsert_ledger(
                        xref=xref, instance=adopted, row_hash=row_hash
                    )
                    return self._skip_result(adopted)

        if ledger is not None and self.entry.tier in FROZEN_TIERS:
            return self._skip_result(instance)
        if (
            ledger is not None
            and instance is not None
            and ledger.content_hash == row_hash
        ):
            return self._skip_result(instance)
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
        """Restore auto fields and upsert the ledger."""

        xref = _row_xref(self.entry, row, row_number=kwargs["row_number"])
        _restore_auto_fields(self, instance, row)
        self._upsert_ledger(
            xref=xref, instance=instance, row_hash=self._row_hashes[xref]
        )

    def instance_for_xref(self, xref: str) -> models.Model | None:
        """Return the target instance for an existing row xref."""

        ledger = self._existing_ledgers.get(xref)
        if xref not in self._existing_ledgers:
            ledger = self._ledger_for_xref(xref)
            self._existing_ledgers[xref] = ledger
        if ledger is None:
            return self._adopted_instances.get(xref)
        return self._instance_from_ledger(ledger)

    def _ledger_for_xref(self, xref: str) -> Resource | None:
        """Return the ledger row for this resource file and target model."""

        return (
            self.ledger_model._default_manager.filter(
                source_addon=self.entry.addon.name,
                source_path=self.entry.source,
                xref=xref,
                target_model=self._meta.model._meta.label,
            )
            .order_by("pk")
            .first()
        )

    def _upsert_ledger(
        self, *, xref: str, instance: models.Model, row_hash: str
    ) -> None:
        """Create or update the ledger row for one imported resource row."""

        self.ledger_model._default_manager.update_or_create(
            source_addon=self.entry.addon.name,
            source_path=self.entry.source,
            xref=xref,
            target_model=self._meta.model._meta.label,
            defaults={
                "tier": self.entry.tier,
                "content_hash": row_hash,
                "target_id": public_id_of(instance),
            },
        )

    def _instance_from_ledger(
        self,
        ledger: Resource | None,
    ) -> models.Model | None:
        """Resolve a ledger target to the imported model."""

        if ledger is None:
            return None
        target_id = str(ledger.target_id)
        if not target_id:
            return None
        instance = instance_from_public_id(self._meta.model, target_id)
        if instance is None:
            return None
        expected = self._meta.model._meta.concrete_model
        if instance._meta.concrete_model is not expected:
            raise ResourceLoadError(
                f"{self.entry.display}: {ledger.xref} targets "
                f"{instance._meta.label}, not {self._meta.model._meta.label}"
            )
        return instance

    def _skip_result(self, instance: models.Model | None) -> RowResult:
        """Return an import-export skip result for one row."""

        row_result = self.get_row_result_class()()
        row_result.import_type = RowResult.IMPORT_TYPE_SKIP
        if instance is not None:
            row_result.add_instance_info(instance)
            if self._meta.store_instance:
                row_result.instance = instance
        return row_result

    def _validate_headers(self, headers: list[str]) -> None:
        """Fail fast on fields import-export should not handle."""

        allowed = set(self.fields) | {
            field.column_name for field in self.fields.values()
        }
        pk = self._meta.model._meta.pk
        primary_keys = {pk.name, pk.attname}
        blocked = sorted(set(headers) & primary_keys)
        if blocked:
            names = ", ".join(blocked)
            raise ResourceLoadError(
                f"{self.entry.display}: primary key field(s) are managed "
                f"by _xref: {names}"
            )
        unknown = sorted(set(headers) - allowed)
        if unknown:
            names = ", ".join(unknown)
            raise ResourceLoadError(
                f"{self.entry.display}: unknown field(s) for "
                f"{self._meta.model._meta.label}: {names}"
            )


class XrefInstanceLoader(BaseInstanceLoader):
    """Resolve existing instances through the Resource ledger."""

    resource: AngeeResource

    def get_instance(self, row: Mapping[str, Any]) -> models.Model | None:
        """Return the ledger target for a row, if it already exists."""

        xref = _row_xref(self.resource.entry, row, row_number=0)
        return self.resource.instance_for_xref(xref)


def build_resource(
    model: type[models.Model],
    entry: ResourceEntry,
    *,
    ledger_model: type[models.Model],
) -> AngeeResource:
    """Return an import-export resource instance for one model."""

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
        resource_type(entry=entry, ledger_model=ledger_model),
    )


def result_counts(rows: list[RowResult]) -> tuple[int, int, int]:
    """Return created, updated, and skipped counts from row results."""

    created = 0
    updated = 0
    skipped = 0
    for row in rows:
        if row.import_type == RowResult.IMPORT_TYPE_NEW:
            created += 1
        elif row.import_type == RowResult.IMPORT_TYPE_UPDATE:
            updated += 1
        elif row.import_type == RowResult.IMPORT_TYPE_SKIP:
            skipped += 1
    return created, updated, skipped


def _row_xref(
    entry: ResourceEntry,
    row: Mapping[str, Any],
    *,
    row_number: int,
) -> str:
    """Return the row xref or fail."""

    value = row.get("_xref")
    if not isinstance(value, str) or not value.strip():
        raise ResourceLoadError(
            f"{entry.display} row {row_number}: missing _xref"
        )
    return value.strip()


def _row_content_hash(row: Mapping[str, Any]) -> str:
    """Return a stable content hash for import-export row data."""

    payload = {
        key: value
        for key, value in sorted(row.items())
        if key not in RESERVED_ROW_KEYS
    }
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _json_default(value: object) -> object:
    """Return a deterministic JSON encoding for non-JSON scalars."""

    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _adopt_existing_target(
    resource: AngeeResource,
    row: Mapping[str, Any],
) -> models.Model | None:
    """Find an existing DB row by unique field match and adopt it."""

    candidates: list[tuple[str, Any]] = []
    for field in resource._meta.model._meta.fields:
        if not getattr(field, "unique", False) or field.primary_key:
            continue
        resource_field = resource.fields.get(field.name)
        if resource_field is None or resource_field.column_name not in row:
            continue
        value = row.get(resource_field.column_name)
        if value in (None, ""):
            continue
        candidates.append((field.name, value))
    if len(candidates) != 1:
        return None
    field_name, value = candidates[0]
    matches = list(
        resource._meta.model._default_manager.filter(
            **{field_name: value},
        )[:2]
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _restore_auto_fields(
    resource: AngeeResource,
    instance: models.Model,
    row: Mapping[str, Any],
) -> None:
    """Restore auto_now/auto_now_add fields from resource file values."""

    updates: dict[str, Any] = {}
    for field in resource._meta.model._meta.fields:
        if field.name not in row:
            continue
        if not getattr(field, "auto_now", False) and not getattr(
            field, "auto_now_add", False
        ):
            continue
        resource_field = resource.fields.get(field.name)
        if resource_field is None:
            continue
        updates[field.name] = resource_field.clean(row)
    if updates:
        type(instance)._default_manager.filter(pk=instance.pk).update(
            **updates
        )
        instance.refresh_from_db(fields=list(updates))
