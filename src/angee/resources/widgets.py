"""Import-export widgets for resolving resource xrefs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import models
from import_export import widgets

from angee.base.models import instance_from_public_id
from angee.resources.entries import resolve_model


class XrefWidgetMixin:
    """Carry the resource ledger model bound by ``AngeeResource``."""

    ledger_model: type[models.Model] | None = None
    """Concrete resource ledger model used to resolve xrefs."""


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
        target = resolve_xref(value, self.ledger_model)
        if target._meta.concrete_model is not self.model._meta.concrete_model:
            raise ValueError(
                f"xref {value!r} targets {target._meta.label}, "
                f"not {self.model._meta.label}"
            )
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
) -> models.Model:
    """Resolve ``<addon>.<xref>`` through the resource ledger."""

    if ledger_model is None:
        raise ValueError("xref resolution requires a bound ledger model")
    if "." not in value:
        raise ValueError("xrefs must use <addon>.<xref>")
    addon_ref, xref = value.rsplit(".", 1)
    matches = list(
        ledger_model._default_manager.filter(
            source_addon=addon_ref,
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
