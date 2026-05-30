"""Import-export widgets for cross-addon xref resolution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import models
from django.db.models import Q
from import_export import widgets

from angee.base.models import instance_from_public_id
from angee.base.resources.entries import resolve_model


class XrefWidgetMixin:
    """Carry the concrete ledger model the resource binds at load time.

    Import-export builds widgets through classmethods, so the owning
    ``AngeeResource`` sets ``ledger_model`` on each xref widget instance after
    its fields are built, instead of through any module global.
    """

    ledger_model: type[models.Model] | None = None


class XrefForeignKeyWidget(XrefWidgetMixin, widgets.ForeignKeyWidget):
    """Resolve ``<addon>.<xref>`` foreign keys through the ledger."""

    def clean(
        self,
        value: Any,
        row: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Return the target object or id for one xref."""

        del row, kwargs
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError("xref foreign keys must be strings")
        target = resolve_xref(value, self.ledger_model)
        if target._meta.concrete_model is not self.model._meta.concrete_model:
            raise ValueError(
                f"xref {value!r} targets {target._meta.label}, "
                f"not {self.model._meta.label}"
            )
        return target.pk if self.key_is_id else target


class XrefManyToManyWidget(XrefWidgetMixin, widgets.ManyToManyWidget):
    """Resolve list or comma-separated xrefs through the ledger."""

    def clean(
        self,
        value: Any,
        row: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[models.Model]:
        """Return target objects for xref values."""

        del row, kwargs
        targets: list[models.Model] = []
        for ref in xref_list(value):
            target = resolve_xref(ref, self.ledger_model)
            if (
                target._meta.concrete_model
                is not self.model._meta.concrete_model
            ):
                raise ValueError(
                    f"xref {ref!r} targets {target._meta.label}, "
                    f"not {self.model._meta.label}"
                )
            targets.append(target)
        return targets


class NativeJSONWidget(widgets.JSONWidget):
    """JSON widget that accepts native YAML and JSON values."""

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
) -> models.Model:
    """Resolve ``<addon>.<xref>`` through the resource ledger."""

    if ledger_model is None:
        raise ValueError("xref resolution requires a bound ledger model")
    if "." not in value:
        raise ValueError("xrefs must use <addon>.<xref>")
    addon_ref, xref = value.split(".", 1)
    ledger = (
        ledger_model._default_manager.filter(xref=xref)
        .filter(
            Q(source_addon=addon_ref)
            | Q(source_addon__endswith=f".{addon_ref}")
        )
        .exclude(target_id="")
        .order_by("source_addon", "source_path", "target_model", "pk")
        .first()
    )
    if ledger is None:
        raise ValueError(f"unresolved xref {value!r}")
    model = resolve_model(str(getattr(ledger, "target_model")))
    target = instance_from_public_id(model, str(getattr(ledger, "target_id")))
    if target is None:
        raise ValueError(f"xref {value!r} has no ORM target")
    return target


def xref_list(value: Any) -> list[str]:
    """Return a list of xref strings from scalar or sequence input."""

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
