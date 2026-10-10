"""Import-export widgets for resolving resource xrefs and content-type labels."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from import_export import fields, widgets
from rebac import GenericTarget, generic_target

from angee.base.fields import ModelLabelField
from angee.base.refs import RecordRefMixin
from angee.base.serialization import canonical_json


class XrefWidgetMixin:
    """Carry the resource ledger model bound by ``AngeeResource``."""

    ledger_model: type[models.Model] | None = None
    """Concrete resource ledger model used to resolve xrefs."""

    addon_aliases: Mapping[str, str] | None = None
    """Addon aliases keyed by full addon name and short label."""

    model: type[models.Model]
    """Related model bound by the import-export FK/M2M widget base."""

    def referenced_handles(self, value: Any) -> frozenset[tuple[str, str]]:
        """Parse relation intent without resolving its database target."""

        if self.addon_aliases is None:
            raise ValueError("xref parsing requires addon aliases")
        return frozenset(split_xref(ref, self.addon_aliases) for ref in self.reference_values(value))

    def reference_values(self, value: Any) -> list[str]:
        """Return the single reference accepted by FK and record-reference fields."""

        if value in (None, ""):
            return []
        if not isinstance(value, str):
            raise ValueError("xrefs must be strings")
        return [value]

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

        return resolve_xref(ref, self.ledger_model, self.addon_aliases, model=self.model)


class RecordRefWidget(XrefWidgetMixin, widgets.Widget):
    """Resolve an xref to the canonical columns of a generic record reference."""

    def clean(
        self,
        value: Any,
        row: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> GenericTarget | None:
        """Return a canonical target, or ``None`` for an empty reference.

        ``ValueError`` for an xref that names a row without a REBAC type: a
        polymorphic edge cannot name it.
        """

        del row, kwargs
        references = self.reference_values(value)
        if not references:
            return None
        return generic_target(resolve_xref(references[0], self.ledger_model, self.addon_aliases))


class RecordRefField(fields.Field):
    """Assign a cleaned record reference to its model-owned backing columns."""

    def __init__(self, model: type[RecordRefMixin]) -> None:
        """Declare the model's import column and its xref widget."""

        super().__init__(
            attribute=model.record_ref_field().name,
            column_name=model.record_ref_field().name,
            widget=RecordRefWidget(),
        )

    def save(
        self,
        instance: RecordRefMixin,
        row: Mapping[str, Any],
        is_m2m: bool = False,
        **kwargs: Any,
    ) -> None:
        """Reject conflicting columns before resolution, then assign both fields."""

        del is_m2m
        reference = instance.record_ref_field()
        field_names = (reference.ct_field, reference.fk_field)
        if any(name in row for name in field_names):
            raise ValueError(f"{self.column_name} cannot be combined with its backing columns")
        target = self.clean(row, **kwargs)
        values = (target.content_type.pk, target.object_id) if target is not None else None
        for index, name in enumerate(field_names):
            field = instance._meta.get_field(name)
            value = values[index] if values is not None else (None if field.null else field.get_default())
            try:
                # Field.clean owns to_python and null/default validation before assignment.
                value = field.clean(value, instance)
            except ValidationError as error:
                raise ValidationError({name: error}) from error
            setattr(instance, field.attname, value)


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
        references = self.reference_values(value)
        if not references:
            return None
        target = self.resolve_field_target(references[0])
        return target.pk if self.key_is_id else target


class XrefManyToManyWidget(XrefWidgetMixin, widgets.ManyToManyWidget):
    """Resolve scalar or list xref values for many-to-many fields."""

    def reference_values(self, value: Any) -> list[str]:
        """Use the same list grammar for prerequisites and target resolution."""

        return many_to_many_values(value)

    def clean(
        self,
        value: Any,
        row: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[models.Model]:
        """Return target model objects for every xref in ``value``."""

        del row, kwargs
        return [self.resolve_field_target(ref) for ref in self.reference_values(value)]


class ContentTypeForeignKeyWidget(widgets.ForeignKeyWidget):
    """Resolve a foreign key to ``contenttypes.ContentType`` from a model label.

    Content types are never ledger rows, so a seed names the target model by
    its Django label (``parties.PartyHandle``) instead of an xref. The native
    ``clean`` keeps empty values and ``key_is_id`` handling.
    """

    def get_instance_by_lookup_fields(self, value: Any, row: Mapping[str, Any] | None, **kwargs: Any) -> ContentType:
        """Return the content type of the model ``value`` names."""

        del row, kwargs
        return resolve_content_type(value)


class ContentTypeManyToManyWidget(widgets.ManyToManyWidget):
    """Resolve scalar or list model labels for many-to-many fields to ``ContentType``."""

    def clean(
        self,
        value: Any,
        row: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[ContentType]:
        """Return the content type of every model label in ``value``."""

        del row, kwargs
        return [resolve_content_type(label) for label in many_to_many_values(value)]


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

    def render(
        self,
        value: Any,
        obj: Any | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Return one canonical JSON representation for import diffs."""

        del kwargs
        self._obj_deprecation_warning(obj)
        if value is None:
            return None
        return canonical_json(value)


def resolve_xref(
    value: str,
    ledger_model: type[models.Model] | None,
    addon_aliases: Mapping[str, str] | None,
    *, model: type[models.Model] | None = None,
) -> models.Model:
    """Resolve ``<addon>.<xref>`` through the resource ledger."""

    if not isinstance(value, str):
        raise ValueError("xrefs must be strings")
    if ledger_model is None:
        raise ValueError("xref resolution requires a bound ledger model")
    if addon_aliases is None:
        raise ValueError("xref resolution requires addon aliases")
    source_addon, xref = split_xref(value, addon_aliases)
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
    target = ledger.target_instance()
    if target is None:
        raise ValueError(f"xref {value!r} has no ORM target")
    if model is not None and not isinstance(target, model):
        raise ValueError(f"xref {value!r} targets {target._meta.label}, not {model._meta.label}")
    return target


def resolve_ledger_xref(handle: str) -> models.Model | None:
    """Resolve a ``<addon>.<xref>`` handle through the composed resource ledger.

    Delegates aliasing and resolution to ``Resource.objects.resolve``. Returns
    ``None`` for an unresolved or ambiguous handle, so optional resource hooks
    can skip missing targets. Alias collisions remain configuration errors.
    """

    ledger_model = apps.get_model("resources", "Resource")
    try:
        return ledger_model.objects.resolve(handle)
    except ValueError:
        return None


def split_xref(
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


def resolve_content_type(label: Any) -> ContentType:
    """Return the content type of the model a Django model label names.

    ``ModelLabelField`` owns which labels name a configured model and raises
    the ``ValueError`` import-export reports against the row; Django's
    content-type manager owns the cached row for that model.
    """

    if not isinstance(label, str):
        raise ValueError("model labels must be strings")
    return ContentType.objects.get_for_model(ModelLabelField.get_model(label))


def many_to_many_values(value: Any) -> list[str]:
    """Return the xrefs or model labels of a comma-separated string or sequence."""

    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        refs: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("many-to-many values must be strings")
            refs.append(item)
        return refs
    raise ValueError("many-to-many values must be a list or string")
