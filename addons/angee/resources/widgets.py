"""Import-export widgets for resolving resource xrefs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.apps import apps
from django.db import models
from import_export import widgets

from angee.base.models import instance_from_public_id
from angee.resources.entries import resolve_model


class XrefWidgetMixin:
    """Carry the resource ledger model bound by ``AngeeResource``."""

    ledger_model: type[models.Model] | None = None
    """Concrete resource ledger model used to resolve xrefs."""

    addon_aliases: Mapping[str, str] | None = None
    """Addon aliases keyed by full addon name and short label."""

    model: type[models.Model]
    """Related model bound by the import-export FK/M2M widget base."""

    def resolve_field_target(self, ref: str) -> models.Model:
        """Resolve one xref to a row assignable to this widget's related field.

        ``resolve_xref`` returns the concrete row a ``<addon>.<xref>`` handle
        names. A ``runtime = True`` materialized child is an MTI subclass that
        shares its parent's primary key (``Organization`` IS-A ``Party``), so a
        child xref is a valid value for a foreign key to its MTI parent: accept
        any instance of the bound target model — including such a descendant,
        whose shared pk resolves the parent link — and fail fast on a genuinely
        unrelated type.
        """

        target = resolve_xref(ref, self.ledger_model, self.addon_aliases)
        if not isinstance(target, self.model):
            raise ValueError(f"xref {ref!r} targets {target._meta.label}, not {self.model._meta.label}")
        return target


class XrefForeignKeyWidget(XrefWidgetMixin, widgets.ForeignKeyWidget):
    """Resolve ``<addon>.<xref>`` foreign keys through the ledger."""

    def clean(
        self,
        value: Any,
        row: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Return the target object or primary key for one xref value."""

        del row, kwargs
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError("xref foreign keys must be strings")
        target = self.resolve_field_target(value)
        return target.pk if self.key_is_id else target


class XrefManyToManyWidget(XrefWidgetMixin, widgets.ManyToManyWidget):
    """Resolve scalar or list xref values for many-to-many fields."""

    def clean(
        self,
        value: Any,
        row: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[models.Model]:
        """Return target model objects for every xref in ``value``."""

        del row, kwargs
        return [self.resolve_field_target(ref) for ref in xref_list(value)]


class _NativeJSONWidget(widgets.JSONWidget):
    """Accept native YAML/JSON values as already-clean JSON values."""

    def clean(
        self,
        value: Any,
        row: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Return native JSON values unchanged."""

        if isinstance(value, dict | list | bool | int | float):
            return value
        return super().clean(value, row=row, **kwargs)


def resolve_xref(
    value: str,
    ledger_model: type[models.Model] | None,
    addon_aliases: Mapping[str, str] | None,
) -> models.Model:
    """Resolve ``<addon>.<xref>`` through the resource ledger."""

    if ledger_model is None:
        raise ValueError("xref resolution requires a bound ledger model")
    if addon_aliases is None:
        raise ValueError("xref resolution requires addon aliases")
    source_addon, xref = _split_xref(value, addon_aliases)
    matches = list(
        ledger_model._default_manager.filter(
            source_addon=source_addon,
            xref=xref,
        ).exclude(target_id="")[:2]
    )
    if not matches:
        raise ValueError(f"unresolved xref {value!r}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous xref {value!r}")
    ledger = matches[0]
    model = resolve_model(str(getattr(ledger, "target_model")))
    target = instance_from_public_id(model, str(getattr(ledger, "target_id")))
    if target is None:
        raise ValueError(f"xref {value!r} has no ORM target")
    return target


def resolve_ledger_xref(handle: str) -> models.Model | None:
    """Resolve a ``<addon>.<xref>`` handle through the composed resource ledger.

    The high-level companion to :func:`resolve_xref`: it binds the concrete
    ``resources.Resource`` ledger and builds the addon-alias map from the app
    registry — each installed app's dotted name and short label both resolve to
    its canonical dotted name, the same alias convention the loader builds per
    selected addon (:meth:`~angee.resources.managers.ResourceQuerySet._addon_aliases`).
    So a demo-seed ``after_resource_load`` hook resolves a persona (or any ledger
    row) by the very xref the grant fixtures use, with a single owner for who a
    handle names. Returns ``None`` for an unresolved or ambiguous handle so the
    hook can skip gracefully rather than raise.
    """

    ledger_model = apps.get_model("resources", "Resource")
    aliases: dict[str, str] = {}
    for app_config in apps.get_app_configs():
        aliases.setdefault(app_config.name, app_config.name)
        aliases.setdefault(app_config.label, app_config.name)
    try:
        return resolve_xref(handle, ledger_model, aliases)
    except ValueError:
        return None


def _split_xref(
    value: str,
    addon_aliases: Mapping[str, str],
) -> tuple[str, str]:
    """Return the canonical addon name and local xref from ``value``."""

    parts = value.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        source_addon = addon_aliases.get(candidate)
        if source_addon is not None:
            return source_addon, ".".join(parts[cut:])
    raise ValueError(f"unresolved xref {value!r}")


def xref_list(value: Any) -> list[str]:
    """Return xref strings from a comma-separated string or sequence."""

    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        refs: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("many-to-many values must be xref strings")
            refs.append(item)
        return refs
    raise ValueError("many-to-many values must be a list or string")
